"""System prompt and context arming for the Mate LLM.

Contains the system prompt definition and the function that arms the
context blocks sent to the LLM in each turn ( §5.2 CONTEXTO-BACKEND.md ).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


# ──────────────────────────────────────────────────────────────────────
# System prompt base ( §5.1 )
# ──────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_BASE = """Sos Mate, un asistente personal. Hablás en español rioplatense, con calidez y naturalidad, sin exagerar el lunfardo. No sabés nada del usuario más allá de lo que aparece en <memoria> y <agenda>: no supongas gustos, rutinas ni actividades (no hables de actividades que el usuario no haya mencionado).
Tu trabajo: ayudar con lo que pida (organizar su día, recordar cosas, redactar textos, dar ideas, opinar, motivar) y conocerlo de a poco. Si algo no está claro, preguntá. Adaptá tus ideas y tu tono a lo que el usuario cuenta que le gusta o necesita.
Herramientas:
- Anotá SOLO lo que el usuario menciona en su ÚLTIMO mensaje. El historial y la agenda son contexto; nunca vuelvas a crear algo que ya está en <agenda>.
- Cada cosa con día u hora es UN evento: usá crear_recordatorio (si tiene hora) o crear_tarea (si es algo para hacer ese día). Calculá las fechas con la tabla <dias> (hoy, mañana, pasado, nombres de días).
- guardar_dato solo para cosas estables sobre la persona (gustos, actividades que hace, personas importantes, objetivos), no para eventos.
- Si menciona más cosas de las que podés anotar en un turno, anotá las más próximas y preguntá si querés que sigas con el resto.
Después de anotar, confirmá en una o dos frases qué anotaste con día y hora, y si viene al caso, hacé una pregunta o un comentario útil sobre eso. Respuestas cortas salvo que pida un texto largo."""


def get_system_prompt(tone: str = "amable") -> str:
    """Return the system prompt for the given tone.

    Args:
        tone: "amable" or "firme"

    Returns:
        The full system prompt string.
    """
    return SYSTEM_PROMPT_BASE


# ──────────────────────────────────────────────────────────────────────
# Context arming ( §5.2 )
# ──────────────────────────────────────────────────────────────────────

def _format_memoria_block(memories: List[Dict[str, Any]]) -> str:
    """Bloque <memoria>: lista clave: valor (fecha), máx. 30. Siempre presente (§5.2)."""
    items = []
    for m in memories[:30]:
        clave, valor, fecha = m.get("fact_key", ""), m.get("fact_value", ""), m.get("event_date")
        items.append(f"- {clave}: {valor} ({fecha})" if fecha else f"- {clave}: {valor}")
    return "<memoria>\n" + ("\n".join(items) or "(vacía)") + "\n</memoria>"


def _parse_iso(iso_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _format_agenda_block(
    habits: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]],
    upcoming_memories: List[Dict[str, Any]],
    now: datetime,
) -> str:
    """Bloque <agenda>: hábitos con progreso semanal + bloques y recordatorios de hoy y 7 días. Siempre presente."""
    lines: List[str] = []

    for h in habits:
        lines.append(
            f"- Hábito {h.get('name', '')}: {h.get('done_this_week', 0)}/{h.get('target_per_week', 3)} "
            f"esta semana a las {h.get('preferred_time', '19:00')}"
        )

    today = now.date()
    for b in blocks:
        start = _parse_iso(b.get("start_utc", ""))
        if start is None:
            continue
        delta = (start.date() - today).days
        if not 0 <= delta <= 7:
            continue
        etiqueta = "hoy" if delta == 0 else "mañana" if delta == 1 else f"en {delta} días"
        lines.append(f"- {b.get('title', '')} ({etiqueta} a las {start.strftime('%H:%M')} hs)")

    for m in upcoming_memories[:7]:
        lines.append(f"- Recordatorio: {m.get('fact_value', '')} ({m.get('event_date', '')})")

    return "<agenda>\n" + ("\n".join(lines) or "(sin nada agendado)") + "\n</agenda>"


def _format_fecha_hora_block(now: datetime) -> str:
    """Formatea el bloque de fecha y hora actuales."""
    tz = ZoneInfo("America/Argentina/Cordoba")
    now_local = now.astimezone(tz)
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia = dias_semana[now_local.weekday()]
    return f"<fecha_hora>\nHora actual: {now_local.strftime('%H:%M')}\nDía: {dia} {now_local.strftime('%d de %B de %Y')}\n</fecha_hora>"


def _format_dias_block(now: datetime) -> str:
    """Bloque <dias> con hoy y los 7 días siguientes en America/Argentina/Cordoba."""
    tz = ZoneInfo("America/Argentina/Cordoba")
    now_local = now.astimezone(tz)
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

    lines = []
    for i in range(8):
        day = now_local + timedelta(days=i)
        nombre = dias_semana[day.weekday()]
        fecha = day.strftime("%d/%m/%Y")
        if i == 0:
            lines.append(f"hoy = {nombre} {fecha}")
        elif i == 1:
            lines.append(f"mañana = {nombre} {fecha}")
        elif i == 2:
            lines.append(f"pasado mañana = {nombre} {fecha}")
        else:
            lines.append(f"{nombre} = {fecha}")

    return "<dias>\n" + "\n".join(lines) + "\n</dias>"


def _format_historial_block(
    messages: List[Dict[str, str]], max_messages: int = 12
) -> str:
    """Formatea los últimos mensajes del historial."""
    recientes = messages[-max_messages:] if messages else []
    if not recientes:
        return ""

    lines = ["<historial>"]
    for m in recientes:
        rol = m.get("role", "user")
        contenido = m.get("content", "")
        lines.append(f"  [{rol}]: {contenido}")
    lines.append("</historial>")
    return "\n".join(lines)


def arm_contexto(
    session_id: str,
    memories: List[Dict[str, Any]],
    habits: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]],
    upcoming_memories: List[Dict[str, Any]],
    now: datetime,
    tone: str = "amable",
    recent_messages: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, str]:
    """Arma el contexto completo para enviar al LLM en cada turno.

    Conformado por (según §5.2):
    1. System prompt.
    2. <memoria>: todos los memories de la sesión (máx. 30) como lista clave: valor (fecha).
    3. <agenda>: hábitos con su progreso semanal, y bloques y recordatorios de hoy y de los próximos 7 días.
    4. Fecha y hora actuales en America/Argentina/Cordoba y día de la semana.
    5. <dias>: tabla con hoy y los 7 días siguientes para cálculo de fechas.
    6. Últimos 12 mensajes del historial.
    7. El mensaje nuevo dentro de <user_data>.

    Returns un dict con cada bloque formateado para ser insertado en el prompt.
    """
    # Formatear cada bloque
    memoria_block = _format_memoria_block(memories)
    agenda_block = _format_agenda_block(habits, blocks, upcoming_memories, now)
    fecha_hora_block = _format_fecha_hora_block(now)
    dias_block = _format_dias_block(now)

    # Historial: usar los mensajes recientes de la sesión
    historial_block = _format_historial_block(recent_messages or [])

    # Build the full context prompt parts
    system_prompt = get_system_prompt(tone)

    # Combine all blocks into the format the LLM expects
    parts: List[str] = [system_prompt]

    parts.append(memoria_block)
    parts.append(agenda_block)
    parts.append(fecha_hora_block)
    parts.append(dias_block)
    if historial_block:
        parts.append(historial_block)

    # El mensaje nuevo se insertará en <user_data> cuando se construyan los mensajes
    # (esto lo hace _build_messages en agent.py)

    return {
        "system_prompt": system_prompt,
        "memoria": memoria_block,
        "agenda": agenda_block,
        "fecha_hora": fecha_hora_block,
        "dias": dias_block,
        "historial": historial_block,
    }