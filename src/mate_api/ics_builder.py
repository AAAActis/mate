"""
Generador de iCalendar RFC 5545 (.ics) para Mate.
Sin librerías pesadas - implementación pura estándar.
"""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from .models import TaskRead, CalendarBlockRead, Category


# ──────────────────────────────────────────────────────────────────────
# Utilidades de formateo RFC 5545
# ──────────────────────────────────────────────────────────────────────

def _format_dt(dt: datetime) -> str:
    """Formatea datetime a RFC 5545 (UTC, sin zona horaria en el string)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _format_date(date_str: str) -> str:
    """Formatea YYYY-MM-DD a YYYYMMDD."""
    return date_str.replace("-", "")


def _escape_text(text: str) -> str:
    """Escapa texto para iCalendar (RFC 5545)."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold_line(line: str) -> str:
    """Pliega líneas largas (>75 chars) según RFC 5545."""
    if len(line) <= 75:
        return line
    result = []
    while len(line) > 75:
        result.append(line[:75])
        line = " " + line[75:]
    result.append(line)
    return "\r\n".join(result)


# ──────────────────────────────────────────────────────────────────────
# Generador Principal
# ──────────────────────────────────────────────────────────────────────

PRODID = "-//Mate Routine Agent//ES"
VERSION = "2.0"
CALSCALE = "GREGORIAN"
METHOD = "PUBLISH"


def build_ics(
    tasks: List[TaskRead],
    blocks: List[CalendarBlockRead],
    session_id: str,
) -> str:
    """
    Construye un archivo iCalendar válido RFC 5545.

    Incluye:
    - Tareas con hora (due_time) como VEVENT con alarma
    - Bloques de calendario como VEVENT
    - UID único por evento (basado en session_id + tipo + id)
    """
    lines = [
        "BEGIN:VCALENDAR",
        f"PRODID:{PRODID}",
        f"VERSION:{VERSION}",
        f"CALSCALE:{CALSCALE}",
        f"METHOD:{METHOD}",
        "X-WR-CALNAME:Mate - Tu Rutina",
        "X-WR-TIMEZONE:UTC",
    ]

    now = datetime.now(timezone.utc)
    dtstamp = _format_dt(now)

    # ── Tareas con due_time ──
    for task in tasks:
        if task.due_time and not task.completed:
            try:
                # Combinar fecha de hoy con hora de la tarea
                today = now.date()
                hour, minute = map(int, task.due_time.split(":"))
                dt_start = datetime(today.year, today.month, today.day, hour, minute, tzinfo=timezone.utc)
                dt_end = dt_start.replace(hour=min(hour + 1, 23), minute=minute)

                uid = f"task-{session_id}-{task.id}@mate"
                summary = _escape_text(f"📋 {task.title}")

                lines.extend([
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{dtstamp}",
                    f"DTSTART:{_format_dt(dt_start)}",
                    f"DTEND:{_format_dt(dt_end)}",
                    f"SUMMARY:{summary}",
                    "STATUS:NEEDS-ACTION",
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    "DESCRIPTION:Recordatorio de tarea",
                    "TRIGGER:-PT30M",  # 30 min antes
                    "END:VALARM",
                    "END:VEVENT",
                ])
            except (ValueError, AttributeError):
                # Ignorar tareas con formato de hora inválido
                pass

    # ── Bloques de calendario ──
    for block in blocks:
        try:
            dt_start = block.start_utc
            dt_end = block.end_utc

            if dt_start.tzinfo is None:
                dt_start = dt_start.replace(tzinfo=timezone.utc)
            if dt_end.tzinfo is None:
                dt_end = dt_end.replace(tzinfo=timezone.utc)

            uid = f"block-{session_id}-{block.id}@mate"

            # Icono según categoría
            icons = {
                Category.ESTUDIO: "📚",
                Category.GYM: "💪",
                Category.PERSONAL: "👤",
                Category.RECORDATORIO: "🔔",
            }
            icon = icons.get(block.category, "📅")
            summary = _escape_text(f"{icon} {block.title}")

            lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{dtstamp}",
                f"DTSTART:{_format_dt(dt_start)}",
                f"DTEND:{_format_dt(dt_end)}",
                f"SUMMARY:{summary}",
                f"CATEGORIES:{block.category.value.upper()}",
                "STATUS:CONFIRMED",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ])
        except (ValueError, AttributeError):
            # Ignorar bloques con fechas inválidas
            pass

    lines.append("END:VCALENDAR")

    # Unir con CRLF y pliegue de líneas
    result = "\r\n".join(lines)
    # Aplicar pliegue a cada línea
    folded_lines = []
    for line in result.split("\r\n"):
        folded_lines.append(_fold_line(line))
    return "\r\n".join(folded_lines) + "\r\n"


# ──────────────────────────────────────────────────────────────────────
# Generador para recordatorios especiales (cumpleaños, etc.)
# ──────────────────────────────────────────────────────────────────────

def build_reminder_ics(
    title: str,
    event_date: str,  # YYYY-MM-DD
    event_time: str = "09:00",  # HH:MM
    session_id: str = "",
    reminder_days_before: int = 7,
) -> str:
    """
    Genera un .ics para un recordatorio de evento especial (cumpleaños, aniversario).
    Incluye alarma a los N días antes y el día del evento.
    """
    lines = [
        "BEGIN:VCALENDAR",
        f"PRODID:{PRODID}",
        f"VERSION:{VERSION}",
        f"CALSCALE:{CALSCALE}",
        f"METHOD:{METHOD}",
        "X-WR-CALNAME:Mate - Recordatorios",
    ]

    now = datetime.now(timezone.utc)
    dtstamp = _format_dt(now)

    try:
        hour, minute = map(int, event_time.split(":"))
        year, month, day = map(int, event_date.split("-"))
        dt_start = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        dt_end = dt_start.replace(hour=min(hour + 1, 23), minute=minute)

        uid = f"reminder-{session_id}-{uuid4().hex[:8]}@mate"
        summary = _escape_text(f"🎂 {title}")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{_format_dt(dt_start)}",
            f"DTEND:{_format_dt(dt_end)}",
            f"SUMMARY:{summary}",
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            # Alarma N días antes a las 09:00
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape_text(f'Faltan {reminder_days_before} días para: {title}')}",
            f"TRIGGER:-P{reminder_days_before}D",
            "END:VALARM",
            # Alarma el día del evento
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape_text(f'Hoy es: {title}')}",
            "TRIGGER:-PT0S",
            "END:VALARM",
            "END:VEVENT",
        ])
    except (ValueError, AttributeError):
        pass

    lines.append("END:VCALENDAR")

    result = "\r\n".join(lines)
    folded_lines = []
    for line in result.split("\r\n"):
        folded_lines.append(_fold_line(line))
    return "\r\n".join(folded_lines) + "\r\n"