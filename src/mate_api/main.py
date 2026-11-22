"""
FastAPI Application - Mate Routine Companion
API v1 con endpoints para chat, agenda, proactividad SSE y calendario .ics
"""
import asyncio
import base64
import hashlib
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request, Response, Cookie, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .models import (
    Tone,
    AvatarState,
    Category,
    ChatRequest,
    ChatResponse,
    ToneRequest,
    ToneResponse,
    ToneModeRequest,
    ToneModeResponse,
    ProactiveTriggerRequest,
    ProactiveTriggerResponse,
    SessionStateSnapshot,
    RevokeTokenResponse,
    HealthResponse,
    CompleteItemRequest,
    CompleteItemResponse,
)
from .database import (
    init_db,
    create_session,
    get_session,
    get_session_by_ics_token,
    get_full_session_state,
    update_session_tone,
    update_session_tone_mode,
    rotate_ics_token,
    delete_session,
    purge_expired_sessions,
    update_session_last_seen,
    update_session_expires_at,
    complete_task,
    complete_habit,
    get_habit,
    create_calendar_block,
    list_tasks_today,
    list_habits,
    list_blocks_today,
    list_memories,
    create_message,
    get_messages,
    complete_block,
    delete_block,
    get_block_with_completed,
)
from .agent import MateAgent, get_agent, strip_tone
from .scheduler import get_scheduler
from .ics_builder import build_ics, build_reminder_ics
from .security import (
    TokenBudgetStore,
    RateLimiter,
    StoreFactory,
    PiiRedactor,
    get_token_budget,
    get_rate_limiter,
)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


# ──────────────────────────────────────────────────────────────────────
# Lifespan: Inicialización y cleanup
# ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    logger.info("Base de datos inicializada")

    # Inicializar stores de seguridad
    await get_token_budget()
    await get_rate_limiter()

    # Iniciar scheduler proactivo
    scheduler = get_scheduler()
    await scheduler.start()

    yield

    # Shutdown
    await scheduler.stop()
    logger.info("Aplicación detenida")


app = FastAPI(
    title="Mate API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

# ──────────────────────────────────────────────────────────────────────
# Rutas estáticas y CSP con hash de los scripts inline
# ──────────────────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


@lru_cache(maxsize=4)
def _inline_script_hashes(path_str: str, mtime: float) -> tuple[str, ...]:
    """Hashes CSP (sha256) de los <script> inline de index.html.

    Permite servir el frontend en un único archivo sin abrir `script-src`
    a 'unsafe-inline': el navegador sólo acepta el script cuyo hash coincide.
    """
    html = Path(path_str).read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    return tuple(
        "'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode() + "'"
        for s in scripts
    )


def _script_src_tokens() -> str:
    """Tokens extra para script-src (hashes de los scripts inline)."""
    if not INDEX_HTML.exists():
        return ""
    try:
        hashes = _inline_script_hashes(str(INDEX_HTML), INDEX_HTML.stat().st_mtime)
    except OSError:
        return ""
    return " " + " ".join(hashes) if hashes else ""


# ──────────────────────────────────────────────────────────────────────
# Middlewares y CORS
# ──────────────────────────────────────────────────────────────────────

# CORS cerrado por defecto - solo orígenes permitidos en settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Añade cabeceras de seguridad a todas las respuestas."""
    response = await call_next(request)

    # CSP restrictiva
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self'{_script_src_tokens()}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), "
        "payment=(), usb=(), magnetometer=(), gyroscope=()"
    )
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting por IP (CF-Connecting-IP o X-Forwarded-For)."""
    # Solo aplicar a endpoints de API
    if not request.url.path.startswith("/api/v1/"):
        return await call_next(request)

    # Obtener IP real detrás de Cloudflare/proxy
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if not ip:
        ip = request.client.host if request.client else "unknown"

    limiter = await get_rate_limiter()
    allowed, remaining = await limiter.check(ip)
    logger.debug(f"Rate limit: IP={ip} allowed={allowed} remaining={remaining}")
    retry_after = 60  # default fallback

    if not allowed:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "type": "about:blank",
                "title": "Too Many Requests",
                "status": 429,
                "detail": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                "instance": str(request.url),
            },
            headers={"Retry-After": str(retry_after)},
        )

    return await call_next(request)


# ──────────────────────────────────────────────────────────────────────
# Helpers: último mensaje assistant para evitar duplicar saludo
# ──────────────────────────────────────────────────────────────────────


async def _ultimo_mensaje_assistant(session_id: str) -> Optional[dict]:
    """Retorna el último mensaje de assistant para la sesión, o None."""
    from .database import get_connection

    async with get_connection() as conn:
        row = await conn.execute(
            """
            SELECT content FROM messages
            WHERE session_id = ? AND role = 'assistant'
            ORDER BY id DESC LIMIT 1
            """,
            (session_id,),
        )
        row = await row.fetchone()
        if not row:
            return None
        return {"content": row["content"]}


# ──────────────────────────────────────────────────────────────────────
# Helper: Calendar aggregation for /api/v1/session
# ──────────────────────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """Normaliza título para comparación: lowercase, sin acentos, sin puntuación extra."""
    import unicodedata
    normalized = unicodedata.normalize('NFD', title.lower())
    # Remover acentos y caracteres no alfanuméricos básicos
    normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
    # Remover puntuación común, mantener alfanuméricos y espacios
    normalized = ''.join(c for c in normalized if c.isalnum() or c.isspace())
    return ' '.join(normalized.split())  # normalizar espacios


async def build_calendar_events(session_id: str) -> list[dict]:
    """Construye lista unificada de eventos para los próximos 30 días:
    tareas, bloques, recordatorios y eventos especiales de memories.
    Deduplica memories contra tasks/reminders existentes mismo día (título normalizado)."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    CORDOBA_TZ = ZoneInfo("America/Argentina/Cordoba")
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc + timedelta(days=30)
    now_iso = now_utc.isoformat(timespec="seconds")
    cutoff_iso = cutoff_utc.isoformat(timespec="seconds")

    events = []

    # Tareas con due_date
    from .database import get_connection
    async with get_connection() as conn:
        rows = await conn.execute(
            """
            SELECT id, title, due_date, due_time, category, completed
            FROM tasks
            WHERE session_id = ? AND due_date IS NOT NULL
            """,
            (session_id,),
        )
        rows = await rows.fetchall()
        for row in rows:
            if row["due_date"]:
                # Combine date + time if available
                time_str = row["due_time"] or "00:00"
                dt_str = f"{row['due_date']}T{time_str}:00-03:00"
                try:
                    dt = datetime.fromisoformat(dt_str)
                    if now_utc <= dt <= cutoff_utc:
                        events.append({
                            "date": row["due_date"],
                            "title": row["title"],
                            "time": time_str,
                            "category": row["category"] or "personal",
                            "type": "task",
                            "id": row["id"],
                            "completed": bool(row["completed"]),
                        })
                except Exception:
                    pass

    # Bloques de calendario (excluyendo recordatorios, que van en su propia sección)
    from .database import list_upcoming_blocks
    blocks = await list_upcoming_blocks(session_id, limit=100)
    for block in blocks:
        if block.start_utc <= cutoff_utc and block.category.value != "recordatorio":
            local_start = block.start_utc.astimezone(CORDOBA_TZ)
            events.append({
                "date": local_start.strftime("%Y-%m-%d"),
                "title": block.title,
                "time": local_start.strftime("%H:%M"),
                "category": block.category.value,
                "type": "block",
                "id": block.id,
                "completed": False,
            })

    # Recordatorios (bloques con categoría RECORDATORIO)
    from .database import list_upcoming_blocks
    reminders = await list_upcoming_blocks(session_id, limit=100)
    for r in reminders:
        if r.category.value == "recordatorio" and r.start_utc <= cutoff_utc:
            local_start = r.start_utc.astimezone(CORDOBA_TZ)
            events.append({
                "date": local_start.strftime("%Y-%m-%d"),
                "title": r.title,
                "time": local_start.strftime("%H:%M"),
                "category": "recordatorio",
                "type": "reminder",
                "id": r.id,
                "completed": False,
            })

    # Build set of normalized titles per date for deduplication (tasks + reminders)
    existing_titles_by_date = {}
    for ev in events:
        if ev["type"] in ("task", "reminder"):
            date_key = ev["date"]
            norm_title = _normalize_title(ev["title"])
            existing_titles_by_date.setdefault(date_key, set()).add(norm_title)

    # Eventos especiales de memories - deduplicar contra tasks/reminders mismo día
    from .database import get_upcoming_memory_events
    memories = await get_upcoming_memory_events(session_id, days_ahead=30)
    for mem in memories:
        if mem.event_date:
            try:
                event_dt = datetime.strptime(mem.event_date, "%Y-%m-%d")
                if now_utc.date() <= event_dt.date() <= cutoff_utc.date():
                    norm_title = _normalize_title(mem.fact_value)
                    # Solo agregar si no existe ya un task/reminder con título similar ese día
                    if mem.event_date not in existing_titles_by_date or norm_title not in existing_titles_by_date[mem.event_date]:
                        events.append({
                            "date": mem.event_date,
                            "title": mem.fact_value,
                            "time": "09:00",
                            "category": "recordatorio",
                            "type": "birthday",
                            "id": mem.id,
                            "completed": False,
                        })
            except Exception:
                pass

    # Ordenar por fecha y hora
    events.sort(key=lambda e: (e["date"], e["time"]))
    return events


# ──────────────────────────────────────────────────────────────────────
# Helpers: Problem Details (RFC 9457)
# ──────────────────────────────────────────────────────────────────────

def problem_details(
    status: int,
    title: str,
    detail: str,
    instance: str,
    type_uri: str = "about:blank",
) -> JSONResponse:
    """Devuelve respuesta en formato Problem Details (RFC 9457)."""
    return JSONResponse(
        status_code=status,
        content={
            "type": type_uri,
            "title": title,
            "status": status,
            "detail": detail,
            "instance": instance,
        },
    )


async def _generate_completion_reaction(session_id: str, item_type: str, item_desc: str) -> str:
    """Genera una reacción corta del LLM al completar un ítem.
    Si el LLM falla, devuelve string vacío (no se dice nada, solo se tacha)."""
    try:
        from .agent import LLMClient, strip_tone
        from .prompts import arm_contexto, get_system_prompt
        from .database import list_memories, list_habits, list_upcoming_blocks, get_upcoming_memory_events, get_session as get_db_session
        from datetime import datetime, timezone

        db_session = await get_db_session(session_id)
        tone = db_session.tone.value if db_session else "amable"

        now = datetime.now(timezone.utc)
        memories_list = await list_memories(session_id)
        habits_list = await list_habits(session_id)
        blocks_list = await list_upcoming_blocks(session_id, limit=30)
        upcoming_memories = await get_upcoming_memory_events(session_id, days_ahead=7)

        ctx = arm_contexto(
            session_id,
            memories=[m.model_dump(mode="json") for m in memories_list],
            habits=[h.model_dump(mode="json") for h in habits_list],
            blocks=[b.model_dump(mode="json") for b in blocks_list],
            upcoming_memories=[m.model_dump(mode="json") for m in upcoming_memories],
            now=now,
            tone=tone,
        )

        system_prompt = get_system_prompt(tone)

        prompt = f"""Generá una reacción corta (máx 1 frase) a que el usuario completó: {item_desc}.
No uses felicitaciones genéricas ("¡Bien!", "¡Genial!"). Adaptá al contexto y al tono.
Ejemplos: "¿Cómo te fue en la reunión?", "Listo, uno menos. ¿Seguimos?", "Buen ritmo, ¿qué sigue?"

Contexto:
{ctx['memoria']}
{ctx['agenda']}
{ctx['fecha_hora']}
{ctx['dias']}

Tono: {tone}."""

        llm = LLMClient()
        if not llm.api_key:
            return ""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"<user_data>{prompt}</user_data>"},
        ]
        resp = await llm.chat(messages, [])
        if not resp.error and resp.content:
            content, _ = strip_tone(resp.content.strip())
            return content
    except Exception:
        pass
    return ""


# ──────────────────────────────────────────────────────────────────────
# Helpers: Obtener/crear sesión desde cookie
# ──────────────────────────────────────────────────────────────────────

SESSION_COOKIE_NAME = "mate_sid"


async def get_or_create_session_id(
    request: Request,
    response: Response,
    mate_sid: str = Cookie(default=None, alias="mate_sid"),
) -> str:
    """Obtiene session_id de cookie o crea nueva sesión anónima."""
    if mate_sid:
        session = await get_session(mate_sid)
        if session:
            # Renovar last_seen_utc y expires_at_utc en la base de datos
            # (venimiento deslizante: 30 días desde última escritura, como máximo una por hora por sesión)
            await update_session_last_seen(mate_sid)
            await update_session_expires_at(mate_sid)
            # Renovar cookie con Max-Age deslizante (30 días = 2592000 segundos)
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=mate_sid,
                httponly=True,
                samesite="lax",
                secure=settings.SESSION_COOKIE_SECURE,
                max_age=settings.SESSION_TTL_HOURS * 3600,
                path="/",
            )
            return session.session_id

    # Crear nueva sesión
    session_id = secrets.token_urlsafe(32)
    ics_token = secrets.token_urlsafe(32)
    await create_session(session_id, ics_token)
    # Set cookie HttpOnly con Max-Age 30 días
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        path="/",
    )
    return session_id


# ──────────────────────────────────────────────────────────────────────
# Endpoints API v1
# ──────────────────────────────────────────────────────────────────────

@app.get("/api/v1/session")
async def get_session_state(
    request: Request,
    response: Response,
    session_id: str = Depends(get_or_create_session_id),
):
    """Devuelve estado completo de la sesión (tasks, habits, blocks, memories, tone, ics_url, greeting, display_name, memory_days)."""
    state = await get_full_session_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    # Calcular quota remaining
    budget = await get_token_budget()
    _, quota_remaining = await budget.consume(session_id, 0)  # Solo leer sin consumir

    session_data = state["session"]
    # Build greeting
    from .greeting import build_greeting
    from datetime import timezone
    now = datetime.now(timezone.utc)
    # Get habits and blocks for greeting
    habits = await list_habits(session_id)
    blocks = await list_blocks_today(session_id)
    memories = await list_memories(session_id)
    from .database import get_session as get_db_session
    db_session = await get_db_session(session_id)
    session_tone = Tone(db_session.tone) if db_session else Tone.AMABLE
    session_tone_mode = Tone(db_session.tone_mode) if db_session else Tone.AUTOMATICO
    greeting = await build_greeting(db_session, habits, blocks, memories, now, session_tone, session_tone_mode) if db_session else None
    display_name = db_session.display_name if db_session else None

    # Evitar duplicar el saludo en messages al recargar la página.
    # Sólo guardamos el saludo si cambió respecto del último mensaje assistant guardado.
    if greeting:
        last_assistant = await _ultimo_mensaje_assistant(session_id)
        if not last_assistant or last_assistant["content"] != greeting["text"]:
            await create_message(session_id, "assistant", greeting["text"], None)

    # Calendar: próximos 30 días unificados
    calendar = await build_calendar_events(session_id)

    return {
        "session": session_data.model_dump() if hasattr(session_data, "model_dump") else session_data,
        "tasks": [t.model_dump() for t in state["tasks"]],
        "habits": [h.model_dump() for h in state["habits"]],
        "blocks": [b.model_dump() for b in state["blocks"]],
        "memories": [m.model_dump() for m in state["memories"]],
        "tone": state["tone"].value,
        "quota_remaining": quota_remaining,
        "ics_url": state["ics_url"],
        "greeting": greeting,
        "display_name": display_name,
        "memory_days": 30,
        "calendar": calendar,
    }


@app.get("/api/v1/messages")
async def get_messages_endpoint(
    request: Request,
    session_id: str = Depends(get_or_create_session_id),
    before_id: Optional[int] = None,
    limit: int = 30,
):
    """Historial paginado de mensajes de la sesión.

    Params:
        before_id: Si se proporciona, solo mensajes con id < before_id (paginación hacia atrás)
        limit: Máximo 30 mensajes por página
    """
    messages = await get_messages(session_id, before_id=before_id, limit=limit)
    return {"messages": messages, "has_more": len(messages) == limit}


@app.post("/api/v1/chat")
async def chat_endpoint(
    request: Request,
    response: Response,
    chat_req: ChatRequest,
    session_id: str = Depends(get_or_create_session_id),
):
    """
    Envía mensaje al agente. Ejecuta tools (máx 4), devuelve reply, avatar_state,
    tools_executed, state_snapshot, is_degraded.
    Guarda ambos mensajes (user + assistant) en la tabla messages con PiiRedactor.
    """
    # Verificar presupuesto de tokens antes de procesar
    budget = await get_token_budget()
    quota_remaining = await budget.get_remaining(session_id)

    # El tono vive en la sesión: "amable" vs "firme"
    session = await get_session(session_id)
    tone = session.tone if session else Tone.AMABLE
    tone_mode = session.tone_mode if session else Tone.AUTOMATICO

    if quota_remaining <= 0:
        # Modo degradado por presupuesto agotado
        agent = get_agent(session_id, tone, tone_mode)
        fallback_response = await agent.fallback.handle(chat_req.message)
        # Guardar el mensaje del usuario y la respuesta degradada en messages
        await create_message(session_id, "user", chat_req.message)
        await create_message(session_id, "assistant", fallback_response.reply)
        return JSONResponse(
            status_code=200,
            content={
                "reply": fallback_response.reply,
                "avatar_state": fallback_response.avatar_state.value,
                "tools_executed": [],
                "state_snapshot": {},
                "is_degraded": True,
            },
        )

    # Procesar con agente
    agent = get_agent(session_id, tone, tone_mode)
    # get_messages devuelve DESC; el LLM necesita orden cronológico
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in reversed(await get_messages(session_id, limit=12))
    ]
    response_data = await agent.process(chat_req.message, history)

    # Consumir tokens estimados (aproximación)
    estimated_tokens = len(chat_req.message) // 4 + sum(
        len(str(t.result)) // 4 for t in response_data.tools_executed if t.result
    )
    await budget.consume(session_id, max(estimated_tokens, 100))

    # Guardar ambos mensajes en messages (user + assistant) con PiiRedactor
    user_message_tools = chat_req.message  # User message, no tools
    assistant_message_reply = response_data.reply

    await create_message(session_id, "user", chat_req.message)
    await create_message(session_id, "assistant", assistant_message_reply)

    return {
        "reply": response_data.reply,
        "avatar_state": response_data.avatar_state.value,
        "tools_executed": [
            {"name": t.name, "success": t.success, "result": t.result}
            for t in response_data.tools_executed
        ],
        "state_snapshot": response_data.state_snapshot,
        "is_degraded": response_data.is_degraded,
    }


@app.post("/api/v1/tone")
async def set_tone(
    request: Request,
    response: Response,
    tone_req: ToneRequest,
    session_id: str = Depends(get_or_create_session_id),
):
    """Cambia el tono entre 'amable' y 'firme'."""
    ok = await update_session_tone(session_id, tone_req.tone)
    if not ok:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return {"tone": tone_req.tone.value}


@app.post("/api/v1/tone/mode", response_model=ToneModeResponse)
async def set_tone_mode(
    request: Request,
    response: Response,
    tone_mode_req: ToneModeRequest,
    session_id: str = Depends(get_or_create_session_id),
):
    """Cambia el modo de tono: 'automatico', 'amable' o 'firme'."""
    ok = await update_session_tone_mode(session_id, tone_mode_req.tone_mode)
    if not ok:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return {"tone_mode": tone_mode_req.tone_mode.value}


@app.post("/api/v1/items/{item_type}/{item_id}/complete")
async def complete_item(
    request: Request,
    response: Response,
    item_type: str,
    item_id: int,
    session_id: str = Depends(get_or_create_session_id),
):
    """
    Marca tarea, hábito, bloque o recordatorio como cumplido (protegido contra BOLA).
    item_type: "task" | "habit" | "block" | "reminder"
    """
    if item_type not in ("task", "habit", "block", "reminder"):
        return problem_details(
            status=400,
            title="Bad Request",
            detail="item_type debe ser 'task', 'habit', 'block' o 'reminder'",
            instance=str(request.url),
        )

    if item_type == "task":
        ok = await complete_task(session_id, item_id)
        if not ok:
            return problem_details(
                status=404,
                title="Not Found",
                detail="Tarea no encontrada o no pertenece a tu sesión",
                instance=str(request.url),
            )
        reaction = await _generate_completion_reaction(session_id, "tarea", "Tarea completada")
        return {"completed": True, "message": reaction, "avatar_state": AvatarState.CONTENTO.value}
    elif item_type == "habit":
        ok = await complete_habit(session_id, item_id)
        if not ok:
            return problem_details(
                status=404,
                title="Not Found",
                detail="Hábito no encontrado, no pertenece a tu sesión o ya completado hoy",
                instance=str(request.url),
            )
        habit = await get_habit(session_id, item_id)
        reaction = await _generate_completion_reaction(session_id, "hábito", f"{habit.name} ({habit.done_this_week}/{habit.target_per_week} esta semana)")
        return {
            "completed": True,
            "message": reaction,
            "avatar_state": AvatarState.CONTENTO.value,
        }
    else:  # block or reminder
        ok = await complete_block(session_id, item_id)
        if not ok:
            return problem_details(
                status=404,
                title="Not Found",
                detail="Bloque/recordatorio no encontrado o no pertenece a tu sesión",
                instance=str(request.url),
            )
        reaction = await _generate_completion_reaction(session_id, "bloque", "Bloque completado")
        return {"completed": True, "message": reaction, "avatar_state": AvatarState.CONTENTO.value}


@app.delete("/api/v1/items/{item_type}/{item_id}")
async def delete_item(
    request: Request,
    response: Response,
    item_type: str,
    item_id: int,
    session_id: str = Depends(get_or_create_session_id),
):
    """
    Elimina un bloque o recordatorio (protegido contra BOLA).
    item_type: "block" | "reminder"
    """
    if item_type not in ("block", "reminder"):
        return problem_details(
            status=400,
            title="Bad Request",
            detail="item_type debe ser 'block' o 'reminder'",
            instance=str(request.url),
        )

    ok = await delete_block(session_id, item_id)
    if not ok:
        return problem_details(
            status=404,
            title="Not Found",
            detail="Bloque/recordatorio no encontrado o no pertenece a tu sesión",
            instance=str(request.url),
        )
    return {"deleted": True, "message": "Bloque/recordatorio eliminado"}


@app.post("/api/v1/proactive/trigger")
async def trigger_proactive(
    request: Request,
    response: Response,
    trigger_req: ProactiveTriggerRequest,
    session_id: str = Depends(get_or_create_session_id),
):
    """Dispara un aviso proactivo por SSE (para demo/reclutador).
    Toma el próximo evento real de la sesión; si no hay, responde "No hay eventos próximos para avisar"."""
    scheduler = get_scheduler()

    # Obtener próximo evento real de la sesión
    from .database import list_upcoming_blocks, get_upcoming_memory_events
    blocks = await list_upcoming_blocks(session_id, limit=1)
    memories = await get_upcoming_memory_events(session_id, days_ahead=7)

    next_event = None
    if blocks:
        next_event = {"title": blocks[0].title, "time": blocks[0].start_utc, "type": "block"}
    elif memories:
        next_event = {"title": memories[0].fact_value, "date": memories[0].event_date, "type": "memory"}

    if not next_event:
        return {"triggered": False, "message": "No hay eventos próximos para avisar"}

    # Generar mensaje con LLM
    from .agent import LLMClient
    from .prompts import arm_contexto, get_system_prompt
    from .database import list_memories, list_habits, list_upcoming_blocks as list_upcoming_blocks_db, get_session as get_db_session
    from .models import Tone
    from datetime import datetime, timezone

    db_session = await get_db_session(session_id)
    tone = db_session.tone.value if db_session else "amable"

    now = datetime.now(timezone.utc)
    memories_list = await list_memories(session_id)
    habits_list = await list_habits(session_id)
    blocks_list = await list_upcoming_blocks_db(session_id, limit=30)
    upcoming_memories = await get_upcoming_memory_events(session_id, days_ahead=7)

    ctx = arm_contexto(
        session_id,
        memories=[m.model_dump(mode="json") for m in memories_list],
        habits=[h.model_dump(mode="json") for h in habits_list],
        blocks=[b.model_dump(mode="json") for b in blocks_list],
        upcoming_memories=[m.model_dump(mode="json") for m in upcoming_memories],
        now=now,
        tone=tone,
    )

    system_prompt = get_system_prompt(tone)

    event_desc = ""
    if next_event["type"] == "block":
        dt = datetime.fromisoformat(next_event["time"].replace("Z", "+00:00"))
        event_desc = f"Próximo evento: {next_event['title']} a las {dt.strftime('%H:%M')} hs"
    else:
        event_desc = f"Próximo recordatorio: {next_event['title']} ({next_event['date']})"

    prompt = f"""Generá un mensaje proactivo corto (máx 2 frases) para avisar sobre:
{event_desc}

Contexto:
{ctx['memoria']}
{ctx['agenda']}
{ctx['fecha_hora']}
{ctx['dias']}

Tono: {tone}.
Natural, como un amigo que avisa. No saludes genéricos."""

    llm = LLMClient()
    message = "Próximo evento detectado"
    if llm.api_key:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"<user_data>{prompt}</user_data>"},
        ]
        resp = await llm.chat(messages, [])
        if not resp.error and resp.content:
            message, _ = strip_tone(resp.content.strip())

    # Enviar a conexiones SSE activas
    await scheduler._broadcast_to_session(session_id, type('obj', (object,), {
        'session_id': session_id,
        'event_type': 'proactive_trigger',
        'message': message,
        'avatar_state': 'contento',
    })())
    return {"triggered": True, "message": message}


@app.get("/api/v1/stream")
async def sse_stream(
    request: Request,
    session_id: str = Depends(get_or_create_session_id),
):
    """Stream Server-Sent Events para proactividad y actualizaciones de estado."""
    scheduler = get_scheduler()
    queue = scheduler.register_sse_connection(session_id)

    async def event_generator():
        try:
            # Enviar estado inicial
            state = await get_full_session_state(session_id)
            import json
            initial_data = json.dumps({
                "type": "state_update",
                "tasks": len(state["tasks"]),
                "habits": len(state["habits"]),
                "blocks": len(state["blocks"]),
            })
            yield f"event: state_update\ndata: {initial_data}\n\n"

            # Loop de eventos
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield message
                except asyncio.TimeoutError:
                    # Heartbeat para mantener conexión viva
                    yield f"event: heartbeat\ndata: {{\"ts\": \"{datetime.now(timezone.utc).isoformat()}\"}}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            scheduler.unregister_sse_connection(session_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/calendar/{ics_token}.ics")
async def get_calendar(ics_token: str):
    """Suscripción/descarga del calendario iCalendar RFC 5545."""
    session = await get_session_by_ics_token(ics_token)
    if not session:
        raise HTTPException(status_code=404, detail="Token de calendario inválido o expirado")

    tasks = await list_tasks_today(session.session_id)
    blocks = await list_blocks_today(session.session_id)

    ics_content = build_ics(tasks, blocks, session.session_id)

    return Response(
        content=ics_content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="mate-{ics_token[:8]}.ics"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/v1/calendar/revoke")
async def revoke_calendar_token(
    request: Request,
    response: Response,
    session_id: str = Depends(get_or_create_session_id),
):
    """Revoca y rota el token secreto del .ics."""
    new_ics_token = secrets.token_urlsafe(32)
    ok = await rotate_ics_token(session_id, new_ics_token)
    if not ok:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    new_url = f"/api/v1/calendar/{new_ics_token}.ics"
    return {"new_ics_token": new_ics_token, "new_ics_url": new_url}


@app.delete("/api/v1/session")
async def delete_session_endpoint(
    request: Request,
    response: Response,
    session_id: str = Depends(get_or_create_session_id),
):
    """Borra todos los datos de la sesión en SQLite/Redis y reinicia de cero ('Borrar todo')."""
    ok = await delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    # Limpiar cookie
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")

    # Crear nueva sesión limpia
    new_session_id = secrets.token_urlsafe(32)
    new_ics_token = secrets.token_urlsafe(32)
    await create_session(new_session_id, new_ics_token)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=new_session_id,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        path="/",
    )

    return {"deleted": True, "message": "Sesión borrada. Nueva sesión creada."}


# ──────────────────────────────────────────────────────────────────────
# Health Checks
# ──────────────────────────────────────────────────────────────────────

@app.get("/health/live", response_model=HealthResponse)
async def liveness():
    """Sonda de vida - siempre OK si el proceso responde."""
    return HealthResponse(
        status="ok",
        database="connected",
        redis="connected" if settings.REDIS_URL else "not_configured",
    )


@app.get("/health/ready", response_model=HealthResponse)
async def readiness():
    """Sonda de preparación - verifica SQLite."""
    try:
        from .database import get_connection
        async with get_connection() as conn:
            await conn.execute("SELECT 1")
        return HealthResponse(
            status="ok",
            database="connected",
            redis="connected" if settings.REDIS_URL else "not_configured",
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return HealthResponse(
            status="degraded",
            database="disconnected",
            redis="connected" if settings.REDIS_URL else "not_configured",
        )


# ──────────────────────────────────────────────────────────────────────
# Frontend estático (se sirve en /)
# ──────────────────────────────────────────────────────────────────────

# Servir archivos estáticos
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, response: Response):
    """Sirve el index.html del frontend."""
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return HTMLResponse("<h1>Mate API</h1><p>Frontend no construido aún. Ver /docs</p>")


# Importar get_habit para endpoint de complete_item
