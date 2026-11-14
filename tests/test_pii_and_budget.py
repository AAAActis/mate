import pytest
import pytest_asyncio

from src.mate_api.security import PiiRedactor, InMemoryTokenBudgetStore, InMemoryRateLimiter
from src.mate_api.database import create_memory, list_memories
from src.mate_api.config import get_settings


class TestPiiRedactor:
    """Pruebas de redacción determinística de PII."""

    def test_redact_email(self):
        text = "Mi email es juan.perez@ejemplo.com y otro test@dominio.org"
        redacted = PiiRedactor.redact(text)
        assert "[EMAIL_REDACTADO]" in redacted
        assert "juan.perez@ejemplo.com" not in redacted
        assert "test@dominio.org" not in redacted

    def test_redact_phone_argentina(self):
        tests = [
            "+54 9 11 1234-5678",
            "+54 11 1234 5678",
            "011 1234-5678",
            "11 1234 5678",
            "112345678",
            "+54 9 341 555-1234",
        ]
        for phone in tests:
            redacted = PiiRedactor.redact(f"Llamame al {phone}")
            assert "[TEL_REDACTADO]" in redacted, f"Falló con: {phone}"

    def test_redact_dni_cuit_cuil(self):
        tests = [
            "12345678",
            "12.345.678",
            "12-345-678",
            "20-12345678-9",
            "27.12345678.9",
            "23123456789",
        ]
        for dni in tests:
            redacted = PiiRedactor.redact(f"Mi DNI es {dni}")
            assert "[DNI_REDACTADO]" in redacted, f"Falló con: {dni}"

    def test_redact_cbu_cvu_tarjeta(self):
        tests = [
            "1234567890123456789012",  # 22 dígitos CBU
            "1234567890123456",         # 16 dígitos tarjeta
            "12345678901234567890",     # 20 dígitos
        ]
        for cbu in tests:
            redacted = PiiRedactor.redact(f"CBU: {cbu}")
            assert "[CBU_REDACTADO]" in redacted, f"Falló con: {cbu}"

    def test_redact_multiple_in_one_text(self):
        text = "Email: test@mail.com, Tel: +54 9 11 1234-5678, DNI: 12.345.678, CBU: 1234567890123456789012"
        redacted = PiiRedactor.redact(text)
        assert "[EMAIL_REDACTADO]" in redacted
        assert "[TEL_REDACTADO]" in redacted
        assert "[DNI_REDACTADO]" in redacted
        assert "[CBU_REDACTADO]" in redacted

    def test_redact_dict(self):
        data = {
            "email": "user@example.com",
            "phone": "+54 9 11 1234-5678",
            "name": "Juan",
            "age": 30,
        }
        redacted = PiiRedactor.redact_dict(data)
        assert redacted["email"] == "[EMAIL_REDACTADO]"
        assert redacted["phone"] == "[TEL_REDACTADO]"
        assert redacted["name"] == "Juan"
        assert redacted["age"] == 30

    async def test_memory_stores_redacted_value(self, session_a):
        await create_memory(session_a, "cbu_guardado", "Mi CBU es 1234567890123456789012")
        memories = await list_memories(session_a)
        assert len(memories) == 1
        assert "[CBU_REDACTADO]" in memories[0].fact_value
        assert "1234567890123456789012" not in memories[0].fact_value


class TestTokenBudget:
    """Pruebas de presupuesto diario de tokens con corte exacto."""

    @pytest_asyncio.fixture
    async def budget_store(self):
        store = InMemoryTokenBudgetStore(daily_budget=1000)
        yield store

    async def test_consume_within_budget(self, budget_store):
        ok, remaining = await budget_store.consume("session-1", 100)
        assert ok is True
        assert remaining == 900

    async def test_consume_exceeds_budget_cuts_exactly(self, budget_store):
        # Consumir 950, quedan 50
        await budget_store.consume("session-1", 950)
        ok, remaining = await budget_store.consume("session-1", 60)
        assert ok is False
        assert remaining == 0

    async def test_consume_at_limit_allows_exact(self, budget_store):
        await budget_store.consume("session-1", 1000)
        ok, remaining = await budget_store.consume("session-1", 1)
        assert ok is False
        assert remaining == 0

    async def test_different_sessions_independent_budgets(self, budget_store):
        await budget_store.consume("session-A", 500)
        await budget_store.consume("session-B", 800)
        rem_a = await budget_store.get_remaining("session-A")
        rem_b = await budget_store.get_remaining("session-B")
        assert rem_a == 500
        assert rem_b == 200

    async def test_daily_reset_clears_budget(self, budget_store):
        await budget_store.consume("session-1", 1000)
        await budget_store.reset_daily()
        rem = await budget_store.get_remaining("session-1")
        assert rem == 1000


class TestRateLimiter:
    """Pruebas de rate limiting por IP."""

    @pytest_asyncio.fixture
    async def rate_limiter(self):
        limiter = InMemoryRateLimiter(limit=5, window=60)
        yield limiter

    async def test_allows_requests_within_limit(self, rate_limiter):
        for i in range(5):
            allowed, remaining = await rate_limiter.check("192.168.1.1")
            assert allowed is True
            assert remaining == 4 - i

    async def test_blocks_request_over_limit(self, rate_limiter):
        for _ in range(5):
            await rate_limiter.check("192.168.1.1")
        allowed, remaining = await rate_limiter.check("192.168.1.1")
        assert allowed is False
        assert remaining == 0

    async def test_different_ips_independent_limits(self, rate_limiter):
        for _ in range(5):
            await rate_limiter.check("192.168.1.1")
        allowed, _ = await rate_limiter.check("192.168.1.2")
        assert allowed is True