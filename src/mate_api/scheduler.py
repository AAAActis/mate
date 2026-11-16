"""
Scheduler proactivo para Mate - Loop asíncrono que evalúa sesiones cada 60s
y dispara eventos SSE (gym, estudio, cumpleaños).
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

from .config import get_settings
from .database import (
    get_connection,
    list_habits,
    list_blocks_today,
    list_upcoming_blocks,
    get_upcoming_memory_events,
)
from .database import list_active_sessions
from .models import Category, ProactiveTriggerRequest

logger = logging.getLogger(__name__)


@dataclass
class ProactiveMessage:
    """Mensaje proactivo para enviar por SSE."""
    session_id: str
    event_type: str  # "gym" | "estudio" | "cumple"
    message: str
    avatar_state: str


class ProactiveScheduler:
    """
    Loop en segundo plano que cada 60s evalúa:
    1. Hábitos pendientes pasada su hora habitual
    2. Bloques de estudio próximos (<= 30 min)
    3. Eventos/cumpleaños dentro de 7 días
    """

    def __init__(self):
        self.settings = get_settings()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Sesiones con conexiones SSE activas
        self._active_connections: Dict[str, Set[asyncio.Queue]] = {}

    async def start(self):
        """Inicia el loop de proactividad."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("ProactiveScheduler iniciado")

    async def stop(self):
        """Detiene el loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ProactiveScheduler detenido")

    def register_sse_connection(self, session_id: str) -> asyncio.Queue:
        """Registra una nueva conexión SSE para la sesión."""
        queue = asyncio.Queue()
        if session_id not in self._active_connections:
            self._active_connections[session_id] = set()
        self._active_connections[session_id].add(queue)
        return queue

    def unregister_sse_connection(self, session_id: str, queue: asyncio.Queue):
        """Desregistra una conexión SSE."""
        if session_id in self._active_connections:
            self._active_connections[session_id].discard(queue)
            if not self._active_connections[session_id]:
                del self._active_connections[session_id]

    async def _run_loop(self):
        """Loop principal: cada 60s evalúa sesiones activas."""
        while self._running:
            try:
                await self._evaluate_all_sessions()
            except Exception as e:
                logger.exception(f"Error en loop proactivo: {e}")
            await asyncio.sleep(self.settings.PROACTIVE_CHECK_INTERVAL_SECONDS)

    async def _evaluate_all_sessions(self):
        """Evalúa todas las sesiones con conexiones SSE activas."""
        if not self._active_connections:
            return

        async with get_connection() as conn:
            # Obtener todas las sesiones activas
            sessions = await list_active_sessions(conn)

        for session in sessions:
            session_id = session.session_id
            if session_id not in self._active_connections:
                continue

            # Evaluar los 3 escenarios
            messages = await self._evaluate_session(session_id, conn)
            for msg in messages:
                await self._broadcast_to_session(msg.session_id, msg)

    async def _evaluate_session(self, session_id: str, conn) -> List[ProactiveMessage]:
        """Evalúa una sesión y devuelve mensajes proactivos."""
        messages = []
        now = datetime.now(timezone.utc)
        now_local = now.astimezone()  # Hora local del servidor

        # 1. Hábitos pendientes pasada su hora habitual
        habits = await list_habits(session_id)
        for habit in habits:
            if habit.done_this_week >= habit.target_per_week:
                continue  # Ya cumplido esta semana

            try:
                h, m = map(int, habit.preferred_time.split(":"))
                habit_time = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

                # Si pasó la hora habitual y es el mismo día
                if now_local >= habit_time and now_local.date() == habit_time.date():
                    # Verificar si ya notificamos hoy (evitar spam)
                    if habit.last_checkin_date != now_local.date().isoformat():
                        messages.append(ProactiveMessage(
                            session_id=session_id,
                            event_type="gym",
                            message=f"¿Ya fuiste al {habit.name.lower()} hoy? 💪",
                            avatar_state="hablando",
                        ))
            except (ValueError, AttributeError):
                pass

        # 2. Bloques de estudio próximos (<= 30 min)
        blocks = await list_upcoming_blocks(session_id, limit=10)
        for block in blocks:
            if block.category == Category.ESTUDIO:
                minutes_until = int((block.start_utc - now).total_seconds() / 60)
                if not 0 <= minutes_until <= 30:
                    continue
                messages.append(ProactiveMessage(
                    session_id=session_id,
                    event_type="estudio",
                    message=f"Hoy toca {block.title.lower()}, ¿arrancamos? (en {minutes_until} min) 📚",
                    avatar_state="hablando",
                ))

        # 3. Eventos/cumpleaños dentro de 7 días
        memories = await get_upcoming_memory_events(session_id, days_ahead=7)
        for mem in memories:
            if mem.event_date:
                try:
                    event_date = datetime.fromisoformat(mem.event_date).date()
                    days_until = (event_date - now_local.date()).days
                    if 0 <= days_until <= 7:
                        if days_until == 0:
                            msg = f"¡Hoy es {mem.fact_value}! 🎂"
                        elif days_until == 7:
                            msg = f"Falta una semana para {mem.fact_value}, ¿qué le regalamos? 🎁"
                        else:
                            msg = f"Faltan {days_until} días para {mem.fact_value} 📅"
                        messages.append(ProactiveMessage(
                            session_id=session_id,
                            event_type="cumple",
                            message=msg,
                            avatar_state="contento",
                        ))
                except (ValueError, AttributeError):
                    pass

        return messages

    async def _broadcast_to_session(self, session_id: str, msg: ProactiveMessage):
        """Envía mensaje a todas las conexiones SSE de una sesión."""
        if session_id not in self._active_connections:
            return

        # Formato SSE: event: proactive\ndata: {...}\n\n
        import json
        data = json.dumps({
            "event_type": msg.event_type,
            "message": msg.message,
            "avatar_state": msg.avatar_state,
        })
        sse_message = f"event: proactive\ndata: {data}\n\n"

        dead_queues = set()
        for queue in self._active_connections[session_id]:
            try:
                await queue.put(sse_message)
            except Exception:
                dead_queues.add(queue)

        # Limpiar colas muertas
        for q in dead_queues:
            self._active_connections[session_id].discard(q)

    # ── Trigger manual para dashboard (reclutador) ──

    async def trigger_proactive(self, request: ProactiveTriggerRequest) -> str:
        """Dispara un evento proactivo manual (para demo/reclutador)."""
        messages = {
            "gym": "¡Hora del gym! ¿Ya fuiste hoy? 💪",
            "estudio": "Hoy toca estudiar, ¿arrancamos? 📚",
            "cumple": "¡Próximo cumpleaños detectado! ¿Qué le regalamos? 🎁",
        }
        return messages.get(request.scenario, "Evento proactivo activado")


# Instancia global
_scheduler: Optional[ProactiveScheduler] = None


def get_scheduler() -> ProactiveScheduler:
    """Obtiene la instancia singleton del scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ProactiveScheduler()
    return _scheduler