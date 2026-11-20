import re
import time
import secrets
import hashlib
from abc import ABC, abstractmethod
from typing import Optional
from contextlib import asynccontextmanager

import redis.asyncio as redis
from pydantic import BaseModel

from .config import get_settings


# ──────────────────────────────────────────────────────────────────────
# PiiRedactor — Redacción determinística de datos sensibles
# ──────────────────────────────────────────────────────────────────────

class PiiRedactor:
    """
    Redacta PII antes de enviar al LLM, guardar en SQLite o loguear.
    Patrones compilados una sola vez para rendimiento.
    """

    EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    PHONE_RE = re.compile(
        r"""
        (?<!\d)                          # No precedido de dígito
        (?:\+?54\s?)?                    # Código de país Argentina opcional
        (?:9\s?)?                        # Código móvil opcional
        (?:\(?\d{2,4}\)?[\s-]?)?         # Código de área opcional
        \d{3,4}[\s-]?\d{4}               # Número local (7-8 dígitos totales)
        (?!\d)                           # No seguido de dígito
        """,
        re.VERBOSE,
    )
    # DNI: 7-8 dígitos (con puntos/guiones opcionales como separadores: 12.345.678 o 12-345-678)
    # CUIT/CUIL: 11 dígitos con formato XX-XXXXXXXX-X, XX.XXXXXXXX.X o 11 dígitos seguidos
    # Nota: Para DNI con separadores usamos punto O guion (no espacios), para no confundir con teléfonos
    DNI_RE = re.compile(
        r"""
        \b
        (?:
            \d{2}[.-]\d{8}[.-]\d          # CUIT/CUIL: 20-12345678-9 o 20.12345678.9
            |
            \d{11}                        # CUIT/CUIL sin separadores: 23123456789 (11 dígitos exactos)
            |
            \d{1,2}[.-](?:\d{3}[.-]){1}\d{3}  # DNI con separadores: 12.345.678 o 12-345-678 (8 dígitos)
            |
            \b\d{7,8}\b                   # DNI simple: 12345678
        )
        \b
        """,
        re.VERBOSE,
    )
    # CBU/CVU: 22 dígitos exactamente. Tarjetas: 16 dígitos. Otros: 20 dígitos.
    CBU_RE = re.compile(r"\b(?:\d{22}|\d{20}|\d{16})\b")

    @classmethod
    def redact(cls, text: str) -> str:
        if not text:
            return text
        # Orden importante: más específicos primero
        text = cls.EMAIL_RE.sub("[EMAIL_REDACTADO]", text)
        text = cls.CBU_RE.sub("[CBU_REDACTADO]", text)
        text = cls.DNI_RE.sub("[DNI_REDACTADO]", text)
        text = cls.PHONE_RE.sub("[TEL_REDACTADO]", text)
        return text

    @classmethod
    def redact_dict(cls, data: dict) -> dict:
        return {k: cls.redact(v) if isinstance(v, str) else v for k, v in data.items()}


# ──────────────────────────────────────────────────────────────────────
# TokenBudgetStore — Cuota diaria de tokens con Redis + fallback memoria
# ──────────────────────────────────────────────────────────────────────

class TokenBudgetStore(ABC):
    @abstractmethod
    async def consume(self, session_id: str, tokens: int) -> tuple[bool, int]:
        """Intenta consumir tokens. Devuelve (ok, remaining)."""

    @abstractmethod
    async def get_remaining(self, session_id: str) -> int:
        pass

    @abstractmethod
    async def reset_daily(self) -> None:
        pass


class RedisTokenBudgetStore(TokenBudgetStore):
    def __init__(self, client: redis.Redis, prefix: str, daily_budget: int):
        self.client = client
        self.prefix = prefix
        self.daily_budget = daily_budget

    def _key(self, session_id: str) -> str:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return f"{self.prefix}budget:{day}:{session_id}"

    async def consume(self, session_id: str, tokens: int) -> tuple[bool, int]:
        key = self._key(session_id)
        pipe = self.client.pipeline()
        pipe.incrby(key, tokens)
        pipe.expire(key, 86400 * 2)  # 2 días de TTL por seguridad
        results = await pipe.execute()
        used = results[0]
        remaining = max(0, self.daily_budget - used)
        return used <= self.daily_budget, remaining

    async def get_remaining(self, session_id: str) -> int:
        key = self._key(session_id)
        used = await self.client.get(key)
        used = int(used) if used else 0
        return max(0, self.daily_budget - used)

    async def reset_daily(self) -> None:
        # Las claves expiran solas; esto es para limpieza manual si se necesita
        pass


class InMemoryTokenBudgetStore(TokenBudgetStore):
    """Fallback cuando Redis no está disponible."""

    def __init__(self, daily_budget: int):
        self.daily_budget = daily_budget
        self._store: dict[str, int] = {}
        self._current_day = time.strftime("%Y-%m-%d", time.gmtime())

    def _key(self, session_id: str) -> str:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if day != self._current_day:
            self._store.clear()
            self._current_day = day
        return f"{day}:{session_id}"

    async def consume(self, session_id: str, tokens: int) -> tuple[bool, int]:
        key = self._key(session_id)
        used = self._store.get(key, 0) + tokens
        self._store[key] = used
        remaining = max(0, self.daily_budget - used)
        return used <= self.daily_budget, remaining

    async def get_remaining(self, session_id: str) -> int:
        key = self._key(session_id)
        used = self._store.get(key, 0)
        return max(0, self.daily_budget - used)

    async def reset_daily(self) -> None:
        self._store.clear()


# ──────────────────────────────────────────────────────────────────────
# RateLimiter — Por IP (CF-Connecting-IP o X-Forwarded-For)
# ──────────────────────────────────────────────────────────────────────

class RateLimiter(ABC):
    @abstractmethod
    async def check(self, identifier: str) -> tuple[bool, int]:
        """Devuelve (allowed, remaining)."""

    @abstractmethod
    async def reset(self, identifier: str) -> None:
        pass


class RedisRateLimiter(RateLimiter):
    def __init__(self, client: redis.Redis, prefix: str, limit: int, window: int):
        self.client = client
        self.prefix = prefix
        self.limit = limit
        self.window = window

    def _key(self, identifier: str) -> str:
        return f"{self.prefix}ratelimit:{identifier}"

    async def check(self, identifier: str) -> tuple[bool, int]:
        key = self._key(identifier)
        now = time.time()
        pipe = self.client.pipeline()
        pipe.zremrangebyscore(key, 0, now - self.window)
        pipe.zcard(key)
        pipe.zadd(key, {secrets.token_urlsafe(8): now})
        pipe.expire(key, self.window + 1)
        results = await pipe.execute()
        current = results[1]
        remaining = max(0, self.limit - current - 1)
        return current < self.limit, remaining

    async def reset(self, identifier: str) -> None:
        await self.client.delete(self._key(identifier))


class InMemoryRateLimiter(RateLimiter):
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._store: dict[str, list[float]] = {}

    def _cleanup(self, identifier: str, now: float) -> None:
        if identifier in self._store:
            # Use > to expire entries at the window boundary (exclusive start)
            # This ensures exactly `limit` requests per `window` seconds
            self._store[identifier] = [t for t in self._store[identifier] if t > now - self.window]

    async def check(self, identifier: str) -> tuple[bool, int]:
        now = time.time()
        self._cleanup(identifier, now)
        current = len(self._store.get(identifier, []))
        if current < self.limit:
            self._store.setdefault(identifier, []).append(now)
            return True, self.limit - current - 1
        return False, 0

    async def reset(self, identifier: str) -> None:
        self._store.pop(identifier, None)


# ──────────────────────────────────────────────────────────────────────
# Fábrica de stores (Redis con degradación elegante)
# ──────────────────────────────────────────────────────────────────────

class StoreFactory:
    def __init__(self):
        self._redis_client: Optional[redis.Redis] = None
        self._budget_store: Optional[TokenBudgetStore] = None
        self._rate_limiter: Optional[RateLimiter] = None
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        settings = get_settings()
        if settings.REDIS_URL:
            try:
                self._redis_client = redis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                    socket_timeout=2.0,
                )
                await self._redis_client.ping()
                self._budget_store = RedisTokenBudgetStore(
                    self._redis_client, settings.REDIS_KEY_PREFIX, settings.DAILY_TOKEN_BUDGET
                )
                self._rate_limiter = RedisRateLimiter(
                    self._redis_client,
                    settings.REDIS_KEY_PREFIX,
                    settings.RATE_LIMIT_REQUESTS,
                    settings.RATE_LIMIT_WINDOW_SECONDS,
                )
            except Exception:
                # Degradación elegante: fallback a memoria
                self._redis_client = None
        if self._budget_store is None:
            self._budget_store = InMemoryTokenBudgetStore(settings.DAILY_TOKEN_BUDGET)
        if self._rate_limiter is None:
            self._rate_limiter = InMemoryRateLimiter(
                settings.RATE_LIMIT_REQUESTS, settings.RATE_LIMIT_WINDOW_SECONDS
            )
        self._initialized = True

    @property
    def budget_store(self) -> TokenBudgetStore:
        if not self._initialized:
            raise RuntimeError("StoreFactory no inicializado. Llamá a initialize() primero.")
        return self._budget_store

    @property
    def rate_limiter(self) -> RateLimiter:
        if not self._initialized:
            raise RuntimeError("StoreFactory no inicializado. Llamá a initialize() primero.")
        return self._rate_limiter

    async def close(self) -> None:
        if self._redis_client:
            await self._redis_client.close()


# Instancia global
_factory: Optional[StoreFactory] = None


def get_factory() -> StoreFactory:
    """Obtiene la factory singleton."""
    global _factory
    if _factory is None:
        _factory = StoreFactory()
    return _factory


async def get_token_budget() -> TokenBudgetStore:
    """Obtiene el store de presupuesto de tokens (inicializa si necesario)."""
    factory = get_factory()
    await factory.initialize()
    return factory.budget_store


async def get_rate_limiter() -> RateLimiter:
    """Obtiene el rate limiter (inicializa si necesario)."""
    factory = get_factory()
    await factory.initialize()
    return factory.rate_limiter


async def reset_stores() -> None:
    """Resetea los stores globales (útil para tests)."""
    global _factory
    if _factory:
        await _factory.close()
    _factory = None


store_factory = StoreFactory()


@asynccontextmanager
async def lifespan_stores():
    await store_factory.initialize()
    try:
        yield
    finally:
        await store_factory.close()