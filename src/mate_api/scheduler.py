"""
Scheduler proactivo para Mate - Loop asíncrono que evalúa sesiones cada 60s
y dispara eventos SSE. Detecta situaciones y pide al LLM el mensaje.
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
from .prompts import arm_contexto
from .agent import get_agent
from .models import Tone

logger = logging.getLogger(__name__)


@dataclass
class ProactiveMessage:
    """Mensaje proactivo para enviar por SSE."""
    session_id: str
    event_type: str  # "evento_proximo" | "tarea_vencida" | "manana" | "recordatorio"
    message: str
    avatar_state: str


class ProactiveScheduler:
    """
    Loop en segundo plano que cada 60s evalúa:
    1. Evento que empieza en <= 30 min
    2. Evento/recordatorio para mañana
    3. Tarea/hábito que pasó su hora sin completar
    4. Pide al LLM que genere el mensaje contextual
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

            # Evaluar situaciones proactivas
            messages = await self._evaluate_session(session_id, conn)
            for msg in messages:
                await self._broadcast_to_session(msg.session_id, msg)

    async def _evaluate_session(self, session_id: str, conn) -> List[ProactiveMessage]:
        """Evalúa una sesión y devuelve mensajes proactivos generados por LLM."""
        messages = []
        now = datetime.now(timezone.utc)
        now_local = now.astimezone()  # Hora local del servidor

        # Obtener datos de la sesión
        from .database import list_memories
        memories = await list_memories(session_id)
        habits = await list_habits(session_id)
        blocks = await list_upcoming_blocks(session_id, limit=30)
        upcoming_memories = await get_upcoming_memory_events(session_id, days_ahead=7)

        # Obtener tono de la sesión
        from .database import get_session
        db_session = await get_session(session_id)
        tone = db_session.tone.value if db_session else "amable"

        # Armar contexto para el LLM
        ctx = arm_contexto(
            session_id,
            memories=[m.model_dump(mode="json") for m in memories],
            habits=[h.model_dump(mode="json") for h in habits],
            blocks=[b.model_dump(mode="json") for b in blocks],
            upcoming_memories=[m.model_dump(mode="json") for m in upcoming_memories],
            now=now,
            tone=tone,
        )

        system_prompt = ctx["system_prompt"]
        memoria = ctx["memoria"]
        agenda = ctx["agenda"]
        fecha_hora = ctx["fecha_hora"]
        dias = ctx["dias"]

        # 1. Detectar: Evento que empieza en <= 30 min
        for block in blocks:
            try:
                block_start = datetime.fromisoformat(block.start_utc.replace("Z", "+00:00"))
                minutes_until = int((block_start - now).total_seconds() / 60)
                if 0 <= minutes_until <= 30:
                    # Pedir al LLM el mensaje
                    llm_message = await self._ask_llm_proactive(
                        system_prompt, memoria, agenda, fecha_hora, dias,
                        f"Evento que empieza en {minutes_until} minutos: {block.title} a las {block_start.strftime('%H:%M')}",
                        tone
                    )
                    if llm_message:
                        messages.append(ProactiveMessage(
                            session_id=session_id,
                            event_type="evento_proximo",
                            message=llm_message,
                            avatar_state="hablando",
                        ))
            except Exception:
                pass

        # 2. Detectar: Evento/recordatorio para mañana
        tomorrow = now + timedelta(days=1)
        tomorrow_start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = tomorrow_start + timedelta(days=1)
        for block in blocks:
            try:
                block_start = datetime.fromisoformat(block.start_utc.replace("Z", "+00:00"))
                if block_start >= tomorrow_start and block_start < tomorrow_end:
                    llm_message = await self._ask_llm_proactive(
                        system_prompt, memoria, agenda, fecha_hora, dias,
                        f"Evento para mañana: {block.title} a las {block_start.strftime('%H:%M')}",
                        tone
                    )
                    if llm_message:
                        messages.append(ProactiveMessage(
                            session_id=session_id,
                            event_type="manana",
                            message=llm_message,
                            avatar_state="contento",
                        ))
            except Exception:
                pass

        # 3. Detectar: Hábito que toca hoy y ya pasó su hora sin cumplimiento
        today_str = now_local.strftime("%Y-%m-%d")
        for habit in habits:
            if habit.done_this_week >= habit.target_per_week:
                continue
            try:
                h, m = map(int, habit.preferred_time.split(":"))
                habit_time = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

                # Si pasó la hora habitual y es el mismo día, y no se notificó hoy
                if now_local >= habit_time and now_local.date() == habit_time.date():
                    if habit.last_checkin_date != now_local.date().isoformat():
                        llm_message = await self._ask_llm_proactive(
                            system_prompt, memoria, agenda, fecha_hora, dias,
                            f"Hábito pendiente: {habit.name} (hora habitual {habit.preferred_time}, ya pasó)",
                            tone
                        )
                        if llm_message:
                            messages.append(ProactiveMessage(
                                session_id=session_id,
                                event_type="tarea_vencida",
                                message=llm_message,
                                avatar_state="serio" if tone == "firme" else "hablando",
                            ))
            except Exception:
                pass

        # 4. Detectar: Tarea con due_date de hoy que ya pasó su due_time sin completar
        from .database import list_tasks_today
        tasks = await list_tasks_today(session_id, {"today_iso": today_str})
        for task in tasks:
            if task.completed or not task.due_time:
                continue
            try:
                h, m = map(int, task.due_time.split(":"))
                task_time = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
                if now_local >= task_time and now_local.date() == task_time.date():
                    llm_message = await self._ask_llm_proactive(
                        system_prompt, memoria, agenda, fecha_hora, dias,
                        f"Tarea pendiente: {task.title} (para hoy a las {task.due_time}, ya pasó la hora)",
                        tone
                    )
                    if llm_message:
                        messages.append(ProactiveMessage(
                            session_id=session_id,
                            event_type="tarea_vencida",
                            message=llm_message,
                            avatar_state="serio" if tone == "firme" else "hablando",
                        ))
            except Exception:
                pass

        return messages

    async def _ask_llm_proactive(
        self,
        system_prompt: str,
        memoria: str,
        agenda: str,
        fecha_hora: str,
        dias: str,
        situation: str,
        tone: str,
    ) -> Optional[str]:
        """Pide al LLM un mensaje proactivo contextual."""
        try:
            from .agent import LLMClient, strip_tone

            # Prompt para mensaje proactivo
            prompt = f"""Generá un mensaje proactivo corto (máx 2 frases) para esta situación:
{situation}

Contexto del usuario:
{memoria}
{agenda}
{fecha_hora}
{dias}

Tono: {tone}.
El mensaje debe ser natural, como si un amigo avisara. No saludes genéricos."""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"<user_data>{prompt}</user_data>"},
            ]

            llm = LLMClient()
            if not llm.api_key:
                return None

            resp = await llm.chat(messages, [])

            if resp.error or not resp.content:
                return None

            content, _ = strip_tone(resp.content.strip())
            return content
        except Exception:
            return None

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
        """Dispara un evento proactivo manual (para demo/reclutador).
        Toma el próximo evento real de la sesión; si no hay, responde que no hay eventos."""
        # Este endpoint se usa desde /api/v1/proactive/trigger con session_id
        # La lógica real está en main.py que pasa el session_id
        return "No hay eventos próximos para avisar"


# Instancia global
_scheduler: Optional[ProactiveScheduler] = None


def get_scheduler() -> ProactiveScheduler:
    """Obtiene la instancia singleton del scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ProactiveScheduler()
    return _scheduler