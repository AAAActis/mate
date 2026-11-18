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
    rotate_ics_token,
    delete_session,
    purge_expired_sessions,
    update_session_last_seen,
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
)
from .agent import MateAgent, get_agent
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
            await update_session_last_seen(mate_sid)
            # Renovar cookie con Max-Age deslizante
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
    await seed_demo_events(session_id)

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


async def seed_demo_events(session_id: str) -> None:
    """Siembra 2 eventos de demostración con horarios relativos a 'ahora'.

    El primero cae en +20 min (dentro de la ventana de 30 min del aviso
    proactivo de estudio) y el segundo en +2 h. Así la demo respira siempre,
    sin importar a qué hora se abra la app.
    """
    base = datetime.now().astimezone().replace(second=0, microsecond=0)

    def _at(**kwargs):
        # Redondeo al múltiplo de 5 min más cercano hacia arriba
        moment = base + timedelta(**kwargs)
        return moment + timedelta(minutes=(-moment.minute) % 5)

    demo = (
        ("Estudiar Bases de Datos", _at(minutes=20), Category.ESTUDIO),
        ("Ir al gym", _at(hours=2), Category.GYM),
    )
    for title, start, category in demo:
        end = start + timedelta(hours=1)
        try:
            await create_calendar_block(
                session_id,
                title,
                start.astimezone(timezone.utc).isoformat(),
                end.astimezone(timezone.utc).isoformat(),
                category,
            )
        except ValueError:
            pass  # Ya hay un bloque que se solapa: no insistir


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
    from .database import get_session as get_db_session
    db_session = await get_db_session(session_id)
    session_tone = Tone(db_session.tone) if db_session else Tone.AMABLE
    greeting = build_greeting(db_session, habits, blocks, now, session_tone) if db_session else None
    display_name = db_session.display_name if db_session else None

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

    if quota_remaining <= 0:
        # Modo degradado por presupuesto agotado
        agent = get_agent(session_id, tone)
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
    agent = get_agent(session_id, tone)
    response_data = await agent.process(chat_req.message, [])

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


@app.post("/api/v1/items/{item_type}/{item_id}/complete")
async def complete_item(
    request: Request,
    response: Response,
    item_type: str,
    item_id: int,
    session_id: str = Depends(get_or_create_session_id),
):
    """
    Marca tarea o hábito como cumplido (protegido contra BOLA).
    item_type: "task" | "habit"
    """
    if item_type not in ("task", "habit"):
        return problem_details(
            status=400,
            title="Bad Request",
            detail="item_type debe ser 'task' o 'habit'",
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
        return {"completed": True, "message": "¡Tarea completada! ✅", "avatar_state": AvatarState.CONTENTO.value}
    else:
        ok = await complete_habit(session_id, item_id)
        if not ok:
            return problem_details(
                status=404,
                title="Not Found",
                detail="Hábito no encontrado, no pertenece a tu sesión o ya completado hoy",
                instance=str(request.url),
            )
        habit = await get_habit(session_id, item_id)
        return {
            "completed": True,
            "message": f"¡Hábito registrado! Llevas {habit.done_this_week}/{habit.target_per_week} esta semana 💪",
            "avatar_state": AvatarState.CONTENTO.value,
        }


@app.post("/api/v1/proactive/trigger")
async def trigger_proactive(
    request: Request,
    response: Response,
    trigger_req: ProactiveTriggerRequest,
    session_id: str = Depends(get_or_create_session_id),
):
    """Dispara un aviso proactivo por SSE (para demo/reclutador)."""
    scheduler = get_scheduler()
    message = await scheduler.trigger_proactive(trigger_req)
    # Enviar a conexiones SSE activas
    await scheduler._broadcast_to_session(session_id, type('obj', (object,), {
        'session_id': session_id,
        'event_type': trigger_req.scenario,
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
    await seed_demo_events(new_session_id)

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
