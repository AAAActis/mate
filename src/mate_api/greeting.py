"""Saludo generado por LLM con caché de 30 minutos por sesión."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import asyncio

from .agent import get_agent, strip_tone
from .database import get_session as get_db_session
from .models import Tone


# Cache simple en memoria: session_id -> (greeting_dict, expires_at)
_greeting_cache: Dict[str, tuple[dict, datetime]] = {}
CACHE_TTL_SECONDS = 30 * 60  # 30 minutos


async def build_greeting(
    session,
    habits: list,
    blocks: list,
    memories: list,
    now: datetime,
    tone: str = "amable",
    tone_mode: str = "automatico",
) -> dict:
    """Construye el saludo usando el LLM con prompt corto.

    Cachea el resultado por sesión 30 minutos para no gastar tokens en cada recarga.
    Si el LLM falla: "Hola, ¿en qué te ayudo?".
    """
    global _greeting_cache

    session_id = getattr(session, "session_id", None) if session else None
    display_name = getattr(session, "display_name", None) if session else None

    # Verificar caché
    if session_id and session_id in _greeting_cache:
        cached_greeting, expires_at = _greeting_cache[session_id]
        if now < expires_at:
            return cached_greeting

    # Construir prompt corto para el LLM
    # Formatear agenda resumida
    agenda_lines = []
    for h in habits:
        # Handle both pydantic models and dicts
        if hasattr(h, 'model_dump'):
            h_dict = h.model_dump()
        elif hasattr(h, 'dict'):
            h_dict = h.dict()
        else:
            h_dict = h
        agenda_lines.append(f"- Hábito {h_dict.get('name', '')}: {h_dict.get('done_this_week', 0)}/{h_dict.get('target_per_week', 3)} esta semana a las {h_dict.get('preferred_time', '19:00')}")

    today = now.date()
    for b in blocks:
        if hasattr(b, 'model_dump'):
            b_dict = b.model_dump()
        elif hasattr(b, 'dict'):
            b_dict = b.dict()
        else:
            b_dict = b
        start = _parse_iso(b_dict.get("start_utc", ""))
        if start is None:
            continue
        delta = (start.date() - today).days
        if not 0 <= delta <= 7:
            continue
        etiqueta = "hoy" if delta == 0 else "mañana" if delta == 1 else f"en {delta} días"
        agenda_lines.append(f"- {b_dict.get('title', '')} ({etiqueta} a las {start.strftime('%H:%M')} hs)")

    for m in memories[:7]:
        if hasattr(m, 'model_dump'):
            m_dict = m.model_dump()
        elif hasattr(m, 'dict'):
            m_dict = m.dict()
        else:
            m_dict = m
        agenda_lines.append(f"- Recordatorio: {m_dict.get('fact_value', '')} ({m_dict.get('event_date', '')})")

    agenda_text = "\n".join(agenda_lines) if agenda_lines else "(sin nada agendado)"

    # Formatear hora actual en Córdoba
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Argentina/Cordoba")
    now_local = now.astimezone(tz)
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia = dias_semana[now_local.weekday()]
    hora_actual = now_local.strftime("%H:%M")
    fecha_actual = f"{dia} {now_local.strftime('%d de %B de %Y')}"

    # Prompt para el saludo
    greeting_prompt = f"""Saludá al usuario en una frase según la hora ({hora_actual}) y lo que tiene próximo en la agenda:
{agenda_text}

Si no hay nada agendado, presentate en una frase y preguntá en qué lo ayudás.
Tono: {tone} (amable = cálido, firme = directo).
{'Usuario: ' + display_name if display_name else ''}"""

    try:
        # Usar el agente para generar el saludo
        if session_id:
            agent = get_agent(session_id, Tone(tone), Tone(tone_mode))
            # Construir mensajes mínimos para el saludo
            messages = [
                {"role": "system", "content": agent._get_system_prompt()},
                {"role": "user", "content": f"<user_data>{greeting_prompt}</user_data>"},
            ]

            # Llamar al LLM directamente (sin tool calls)
            resp = await agent.llm.chat(messages, [])

            if resp.error or not resp.content:
                raise Exception(resp.error or "No content")

            greeting_text = resp.content.strip()
            greeting_text, _ = strip_tone(greeting_text)

            result = {
                "text": greeting_text,
                "avatar_state": "contento",
                "reason": "llm_greeting",
            }

            # Guardar en caché
            _greeting_cache[session_id] = (result, now + timedelta(seconds=CACHE_TTL_SECONDS))
            return result
    except Exception:
        pass

    # Fallback si falla el LLM
    fallback = "Hola, ¿en qué te ayudo?"
    if display_name:
        fallback = f"¡Hola, {display_name}! {fallback}"

    return {
        "text": fallback,
        "avatar_state": "contento",
        "reason": "fallback",
    }


def _parse_iso(iso_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def clear_greeting_cache(session_id: str) -> None:
    """Limpia la caché de saludo para una sesión (útil al cambiar tono o datos)."""
    if session_id in _greeting_cache:
        del _greeting_cache[session_id]