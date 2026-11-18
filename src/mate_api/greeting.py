"""Saludo determinístico al volver al usuario. Sin LLM, testeable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .database import list_memories
from .models import HabitRead, CalendarBlockRead, Tone


MAX_GREETING_AGE_HOURS = 6  # Para la regla "vuelve después de 6h sin pending"


def _format_time_utc(iso_str: Optional[str]) -> str:
    """Formatea una fecha UTC en HH:MM."""
    if not iso_str:
        return "--:"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return "--:"


def _days_between(event_date: Optional[str], now_date: datetime) -> Optional[int]:
    """Días entre event_date y now. Devuelve None si event_date es None o ya pasó."""
    if not event_date:
        return None
    try:
        event = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        delta = (event - now_date.date()).days
        return delta if delta >= 0 else None
    except Exception:
        return None


def build_greeting(
    session,
    habits: list[HabitRead],
    blocks: list[CalendarBlockRead],
    now: datetime,
    tone: Tone = Tone.AMABLE,
) -> dict:
    """Construye el saludo determinístico.

    Regla 1-6 (primera que aplique). session puede ser None (sesión nueva).
    Devuelve dict {text, avatar_state, reason}.
    Siempre incluye display_name si está disponible.
    """

    display_name = getattr(session, "display_name", None) if session else None
    last_seen_iso = getattr(session, "last_seen_utc", None) if session else None

    # Helper: ¿hace más de MAX_GREETING_AGE_HOURS que no se ve la sesión?
    hours_since_last = None
    if last_seen_iso:
        try:
            from datetime import datetime as _dt
            last_dt = _dt.fromisoformat(last_seen_iso.replace("Z", "+00:00"))
            delta = now - last_dt
            hours_since_last = delta.total_seconds() / 3600
        except Exception:
            hours_since_last = None

    # Si no hay sesión (nueva), regla 6
    if session is None:
        text = "¡Hola! Soy Mate. Contame tus rutinas, fechas importantes o pedime lo que necesites."
        avatar_state = "contento"
        if display_name:
            text = f"¡Hola, {display_name}! {text}"
        return {"text": text, "avatar_state": avatar_state, "reason": "nueva_sesión"}

    # Regla 5: Usuario que vuelve (>6h sin pending) y sin pendientes
    if hours_since_last is not None and hours_since_last > MAX_GREETING_AGE_HOURS:
        # Revisar si hay hábitos pendientes o bloques próximos
        tiene_pendientes = any(
            not h.completed for h in habits
        ) or len(blocks) > 0
        if not tiene_pendientes:
            text = "¡Volviste! ¿En qué te doy una mano hoy?"
            avatar_state = "contento"
            if display_name:
                text = f"¡Hola, {display_name}! {text}"
            return {"text": text, "avatar_state": avatar_state, "reason": "vuelve_sin_pendientes"}

    # Regla 1: Bloque o recordatorio que empieza en las próximas 2 horas
    two_hours_from_now = now + timedelta(hours=2)
    for block in blocks:
        try:
            from datetime import datetime as _dt
            block_start = _dt.fromisoformat(block.start_utc.replace("Z", "+00:00"))
            if block_start <= two_hours_from_now and block_start >= now:
                title = block.title
                hhmm = _format_time_utc(block.start_utc)
                text = f"En un rato tenés {title} ({hhmm} hs)."
                avatar_state = "contento"
                if display_name:
                    text = f"¡Hola, {display_name}! {text}"
                return {"text": text, "avatar_state": avatar_state, "reason": "bloque_proximo"}
        except Exception:
            continue

    # Regla 2: Hábito que toca hoy y ya pasó su hora sin cumplimiento
    today_str = now.strftime("%Y-%m-%d")
    for habit in habits:
        if habit.preferred_time and _hoy_paso_hora(habit.preferred_time, now, today_str) and not habit.completed:
            text = f"¿Fuiste hoy {habit.name}?"
            avatar_state = "serio" if tone.value == "firme" else "contento"
            if display_name:
                text = f"¡Hola, {display_name}! {text}"
            return {"text": text, "avatar_state": avatar_state, "reason": "hábito_pendiente_hoy"}

    # Regla 3: Memory con event_date dentro de 7 días
    memories = await _list_memories_quick(session.session_id if session else "")
    for memory in memories:
        delta = _days_between(memory.event_date, now)
        if delta is not None and 0 <= delta <= 7:
            if delta == 1:
                texto_día = "mañana"
            elif delta == 0:
                texto_día = "hoy"
            else:
                texto_día = f"faltan {delta} días"
            text = f"{texto_día} para {memory.fact_value}."
            avatar_state = "contento"
            if display_name:
                text = f"¡Hola, {display_name}! {text}"
            return {"text": text, "avatar_state": avatar_state, "reason": "memory_proxima"}

    # Regla 4: Algo agendado para mañana
    tomorrow = now + timedelta(days=1)
    tomorrow_start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    for block in blocks:
        try:
            from datetime import datetime as _dt
            block_start = _dt.fromisoformat(block.start_utc.replace("Z", "+00:00"))
            if block_start >= tomorrow_start and block_start < tomorrow_end:
                title = block.title
                hhmm = _format_time_utc(block.start_utc)
                text = f"Mañana tenés {title} a las {hhmm} hs."
                avatar_state = "contento"
                if display_name:
                    text = f"¡Hola, {display_name}! {text}"
                return {"text": text, "avatar_state": avatar_state, "reason": "bloque_mañana"}
        except Exception:
            continue

    # Regla 6 (fallback): Sesión nueva o genérico
    text = "¡Hola! Soy Mate. Contame tus rutinas, fechas importantes o pedime lo que necesites."
    avatar_state = "contento"
    if display_name:
        text = f"¡Hola, {display_name}! {text}"
    return {"text": text, "avatar_state": avatar_state, "reason": "nueva_sesión"}


def _hoy_paso_hora(preferred_time: str, now: datetime, today_str: str) -> bool:
    """Devuelve True si ya pasó la hora preferida de hoy."""
    try:
        parts = preferred_time.split(":")
        preferred_h = int(parts[0])
        preferred_m = int(parts[1]) if len(parts) > 1 else 0
        now_hour = now.hour
        now_minute = now.minute
        # Si la hora preferida ya pasó hoy
        if now_hour > preferred_h:
            return True
        if now_hour == preferred_h and now_minute >= preferred_m:
            return True
        return False
    except Exception:
        return False


async def _list_memories_quick(session_id: str) -> list:
    """Lista rápida de memories para la regla del saludo."""
    if not session_id:
        return []
    return await list_memories(session_id)