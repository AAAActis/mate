from typing import Optional, List, Tuple
"""
Las 8 Herramientas del Agente Mate - Ejecutadas server-side con session_id inyectado.
El LLM NUNCA escribe SQL directo; solo invoca estas funciones tipadas.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from .database import (
    create_task,
    list_tasks_today,
    create_habit,
    list_habits,
    complete_task,
    complete_habit,
    get_habit,
    find_free_slots,
    create_calendar_block,
    list_blocks_today,
    list_upcoming_blocks,
    create_memory,
    list_memories,
    get_upcoming_memory_events,
)
from .models import (
    TaskRead,
    HabitRead,
    CalendarBlockRead,
    MemoryRead,
    Category,
    CrearTareaArgs,
    ListarHoyArgs,
    CrearHabitoArgs,
    RegistrarCumplimientoArgs,
    BuscarHuecoArgs,
    ReservarBloqueArgs,
    CrearRecordatorioArgs,
    GuardarDatoArgs,
)
from .security import PiiRedactor


# ──────────────────────────────────────────────────────────────────────
# Result schemas (lo que devuelve cada herramienta al LLM)
# ──────────────────────────────────────────────────────────────────────

class ToolResult(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class ListarHoyResult(BaseModel):
    tasks: List[TaskRead]
    habits: List[HabitRead]
    blocks: List[CalendarBlockRead]
    upcoming_memories: List[MemoryRead]


class BuscarHuecoResult(BaseModel):
    slots: List[Tuple[str, str]]  # [(inicio_iso, fin_iso), ...]


class CrearRecordatorioResult(BaseModel):
    block: CalendarBlockRead
    memory: Optional[MemoryRead] = None


# ──────────────────────────────────────────────────────────────────────
# Implementación de las 8 herramientas
# ──────────────────────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """Normaliza título para comparación: lowercase, sin acentos, sin puntuación extra."""
    import unicodedata
    normalized = unicodedata.normalize('NFD', title.lower())
    normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
    normalized = ''.join(c for c in normalized if c.isalnum() or c.isspace())
    return ' '.join(normalized.split())


async def tool_crear_tarea(session_id: str, args: dict) -> ToolResult:
    """1. Crear una tarea con título, hora opcional (HH:MM), fecha opcional (YYYY-MM-DD) y categoría opcional.
    Si ya existe una tarea con mismo título normalizado en la misma fecha, devuelve la existente.
    La hora se interpreta en zona horaria America/Argentina/Cordoba y se guarda en UTC.
    La respuesta incluye la hora original local para mostrar al usuario.
    Si el texto original menciona "tarde" o "noche" y la hora es < 12, suma 12 horas (formato 24h)."""
    try:
        validated = CrearTareaArgs(**args)

        # Guardar hora local original para la respuesta
        original_due_time = validated.hora_sugerida
        original_due_date = validated.fecha_sugerida

        # Inferir tarde/noche desde texto_original y ajustar hora si corresponde
        if validated.hora_sugerida and validated.texto_original:
            hour, minute = map(int, validated.hora_sugerida.split(":"))
            texto = validated.texto_original.lower()
            if ("tarde" in texto or "noche" in texto) and hour < 12:
                hour += 12
                validated.hora_sugerida = f"{hour:02d}:{minute:02d}"

        # Validar hora final en rango 00:00-23:59
        if validated.hora_sugerida:
            hour, minute = map(int, validated.hora_sugerida.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return ToolResult(success=False, error="Hora inválida: debe estar entre 00:00 y 23:59")

        # Verificar duplicado por título normalizado + fecha
        if validated.fecha_sugerida:
            from .database import get_connection
            norm_title = _normalize_title(validated.titulo)
            async with get_connection() as conn:
                rows = await conn.execute(
                    """
                    SELECT id, title, due_date, due_time, category, completed, created_at_utc, has_time
                    FROM tasks
                    WHERE session_id = ? AND due_date = ?
                    """,
                    (session_id, validated.fecha_sugerida),
                )
                rows = await rows.fetchall()
                for row in rows:
                    if _normalize_title(row["title"]) == norm_title:
                        # Ya existe, devolver la existente
                        from .models import TaskRead, Category as CategoryModel
                        return ToolResult(success=True, data=TaskRead(
                            id=row["id"],
                            session_id=session_id,
                            title=row["title"],
                            due_time=row["due_time"],
                            due_date=row["due_date"],
                            category=CategoryModel(row["category"]) if row["category"] else None,
                            has_time=bool(row["has_time"]) if "has_time" in row.keys() else True,
                            completed=bool(row["completed"]),
                            created_at_utc=row["created_at_utc"],
                        ).model_dump())

        # Convertir hora local Córdoba a UTC si hay hora y fecha
        due_time = validated.hora_sugerida
        due_date = validated.fecha_sugerida
        has_time = False
        if due_time and due_date:
            # Construir datetime en zona horaria Córdoba y convertir a UTC
            hour, minute = map(int, due_time.split(":"))
            year, month, day = map(int, due_date.split("-"))
            dt_cordoba = datetime(year, month, day, hour, minute, tzinfo=CORDOBA_TZ)
            dt_utc = dt_cordoba.astimezone(timezone.utc)
            due_time = dt_utc.strftime("%H:%M")
            due_date = dt_utc.strftime("%Y-%m-%d")
            has_time = True
        elif due_time and not due_date:
            # Si solo hay hora, usar fecha de hoy en Córdoba
            now_cordoba = datetime.now(CORDOBA_TZ)
            today_iso = now_cordoba.strftime("%Y-%m-%d")
            hour, minute = map(int, due_time.split(":"))
            dt_cordoba = datetime(now_cordoba.year, now_cordoba.month, now_cordoba.day, hour, minute, tzinfo=CORDOBA_TZ)
            dt_utc = dt_cordoba.astimezone(timezone.utc)
            due_time = dt_utc.strftime("%H:%M")
            due_date = dt_utc.strftime("%Y-%m-%d")
            has_time = True

        task = await create_task(
            session_id,
            validated.titulo,
            due_time,
            due_date,
            validated.categoria.value if validated.categoria else None,
        )
        # Sobrescribir has_time y due_time/due_date con hora local original para respuesta
        task_dict = task.model_dump()
        task_dict["has_time"] = has_time
        if original_due_time:
            task_dict["due_time"] = original_due_time
        if original_due_date:
            task_dict["due_date"] = original_due_date
        return ToolResult(success=True, data=task_dict)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


CORDOBA_TZ = ZoneInfo("America/Argentina/Cordoba")

async def tool_listar_hoy(session_id: str, args: dict) -> ToolResult:
    """2. Devuelve tareas de hoy, hábitos pendientes, bloques de hoy y recordatorios próximos.
    Todos los cálculos de "hoy" y la franja 08:00-22:00 se hacen en hora Córdoba,
    guardando en UTC en la base de datos."""
    try:
        validated = ListarHoyArgs(**args)
        now_cordoba = datetime.now(CORDOBA_TZ)
        today_iso = now_cordoba.strftime("%Y-%m-%d")
        # Pasamos today_iso a las herramientas para que usen Córdoba
        tasks = await list_tasks_today(session_id, {"today_iso": today_iso})
        habits = await list_habits(session_id, {"today_iso": today_iso})
        blocks = await list_blocks_today(session_id, {"today_iso": today_iso})
        memories = await get_upcoming_memory_events(session_id, days_ahead=7)
        result = ListarHoyResult(
            tasks=tasks,
            habits=habits,
            blocks=blocks,
            upcoming_memories=memories,
        )
        return ToolResult(success=True, data=result.model_dump())
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_crear_habito(session_id: str, args: dict) -> ToolResult:
    """3. Registra un hábito recurrente."""
    try:
        validated = CrearHabitoArgs(**args)
        habit = await create_habit(
            session_id,
            validated.nombre,
            validated.veces_por_semana,
            validated.hora_habitual,
        )
        return ToolResult(success=True, data=habit.model_dump())
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_registrar_cumplimiento(session_id: str, args: dict) -> ToolResult:
    """4. Marca tarea como completada o suma +1 a done_this_week del hábito."""
    try:
        validated = RegistrarCumplimientoArgs(**args)
        if validated.tipo == "tarea":
            ok = await complete_task(session_id, validated.item_id)
            if not ok:
                return ToolResult(success=False, error="Tarea no encontrada o no pertenece a tu sesión")
            return ToolResult(success=True, data={"tipo": "tarea", "item_id": validated.item_id, "completed": True})
        elif validated.tipo == "habito":
            ok = await complete_habit(session_id, validated.item_id)
            if not ok:
                return ToolResult(success=False, error="Hábito no encontrado, no pertenece a tu sesión o ya completado hoy")
            habit = await get_habit(session_id, validated.item_id)
            return ToolResult(
                success=True,
                data={
                    "tipo": "habito",
                    "item_id": validated.item_id,
                    "done_this_week": habit.done_this_week if habit else 0,
                },
            )
        else:
            return ToolResult(success=False, error="Tipo inválido: debe ser 'tarea' o 'habito'")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_buscar_hueco(session_id: str, args: dict) -> ToolResult:
    """5. Analiza calendar_blocks del día (08:00-22:00 en hora Córdoba)
    y devuelve hasta 3 franjas libres. Los cálculos de "hoy" y la franja 08:00-22:00
    se hacen en America/Argentina/Cordoba, guardando en UTC y devuelve ISO con offset."""
    try:
        validated = BuscarHuecoArgs(**args)
        # Usar today_iso si viene de listar_hoy, sino calcular en Córdoba
        today_iso = args.get("today_iso") or datetime.now(CORDOBA_TZ).strftime("%Y-%m-%d")
        slots = await find_free_slots(session_id, validated.duracion_minutos, today_iso)
        result = BuscarHuecoResult(slots=slots)
        return ToolResult(success=True, data=result.model_dump())
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_reservar_bloque(session_id: str, args: dict) -> ToolResult:
    """6. Agenda un bloque en el calendario verificando que no se solape.
    inicio_iso y fin_iso deben ser ISO 8601 UTC (ej: 2026-11-23T11:00:00+00:00)."""
    try:
        validated = ReservarBloqueArgs(**args)
        cat = Category(validated.categoria)
        block = await create_calendar_block(
            session_id,
            validated.titulo,
            validated.inicio_iso,
            validated.fin_iso,
            cat,
            has_time=True,
        )
        return ToolResult(success=True, data=block.model_dump())
    except ValueError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_crear_recordatorio(session_id: str, args: dict) -> ToolResult:
    """7. Crea un bloque tipo recordatorio y, si es evento especial, registra memoria para avisar 7 días antes.
    Si ya existe un recordatorio con mismo título normalizado en la misma fecha, devuelve el existente.
    La hora se interpreta en zona horaria America/Argentina/Cordoba y se guarda en UTC.
    La respuesta incluye la hora original local para mostrar al usuario.
    Si el texto original menciona "tarde" o "noche" y la hora es < 12, suma 12 horas (formato 24h)."""
    try:
        validated = CrearRecordatorioArgs(**args)

        # Guardar hora local original para la respuesta
        original_hora = validated.hora
        original_fecha = validated.fecha_iso

        # Inferir tarde/noche desde texto_original y ajustar hora si corresponde
        if validated.hora and validated.texto_original:
            hour, minute = map(int, validated.hora.split(":"))
            texto = validated.texto_original.lower()
            if ("tarde" in texto or "noche" in texto) and hour < 12:
                hour += 12
                validated.hora = f"{hour:02d}:{minute:02d}"

        # Validar hora final en rango 00:00-23:59
        if validated.hora:
            hour, minute = map(int, validated.hora.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return ToolResult(success=False, error="Hora inválida: debe estar entre 00:00 y 23:59")

        # Verificar duplicado por título normalizado + fecha (en bloques tipo recordatorio)
        norm_title = _normalize_title(validated.titulo)
        from .database import get_connection
        async with get_connection() as conn:
            rows = await conn.execute(
                """
                SELECT id, title, start_utc, end_utc, category, completed, has_time
                FROM calendar_blocks
                WHERE session_id = ? AND category = 'recordatorio'
                """,
                (session_id,),
            )
            rows = await rows.fetchall()
            for row in rows:
                block_date = row["start_utc"][:10]  # YYYY-MM-DD
                if block_date == validated.fecha_iso and _normalize_title(row["title"]) == norm_title:
                    # Ya existe, devolver el existente
                    from .models import CalendarBlockRead, Category as CategoryModel
                    return ToolResult(success=True, data=CalendarBlockRead(
                        id=row["id"],
                        session_id=session_id,
                        title=row["title"],
                        start_utc=row["start_utc"],
                        end_utc=row["end_utc"],
                        category=CategoryModel(row["category"]),
                        completed=bool(row["completed"]),
                        has_time=bool(row["has_time"]) if "has_time" in row.keys() else True,
                    ).model_dump())

        # Convertir hora local Córdoba a UTC
        hour, minute = map(int, validated.hora.split(":"))
        year, month, day = map(int, validated.fecha_iso.split("-"))
        dt_cordoba = datetime(year, month, day, hour, minute, tzinfo=CORDOBA_TZ)
        dt_utc = dt_cordoba.astimezone(timezone.utc)
        inicio_iso = dt_utc.isoformat(timespec="seconds")
        # Duración por defecto 30 min para recordatorios
        fin_dt = dt_utc + timedelta(minutes=30)
        fin_iso = fin_dt.isoformat(timespec="seconds")

        block = await create_calendar_block(
            session_id,
            validated.titulo,
            inicio_iso,
            fin_iso,
            Category.RECORDATORIO,
            has_time=True,
        )
        # Sobrescribir start_utc/end_utc con hora local original para respuesta
        block_dict = block.model_dump()
        block_dict["start_utc"] = f"{original_fecha}T{original_hora}:00"
        block_dict["end_utc"] = f"{original_fecha}T{original_hora}:30"

        # Si parece un evento especial, guardar en memoria con fecha_evento
        memory = None
        keywords = ["aniversario", "aniversari", "evento", "cita", "reunion", "reunión"]
        if any(kw in validated.titulo.lower() for kw in keywords):
            mem = await create_memory(
                session_id,
                "recordatorio_especial",
                validated.titulo,
                validated.fecha_iso,
            )
            memory = mem

        result = CrearRecordatorioResult(block=block, memory=memory)
        return ToolResult(success=True, data=result.model_dump())
    except ValueError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_guardar_dato(session_id: str, args: dict) -> ToolResult:
    """8. Guarda un hecho en la memoria de largo plazo (previa limpieza con PiiRedactor)."""
    try:
        validated = GuardarDatoArgs(**args)
        memory = await create_memory(
            session_id,
            validated.clave,
            validated.valor,
            validated.fecha_evento,
        )
        return ToolResult(success=True, data=memory.model_dump())
    except Exception as e:
        return ToolResult(success=False, error=str(e))


# ──────────────────────────────────────────────────────────────────────
# Mapa de herramientas para el agente
# ──────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "crear_tarea",
            "description": "Crea una tarea para hoy con título y hora opcional (HH:MM).",
            "parameters": CrearTareaArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_hoy",
            "description": "Devuelve tareas de hoy, hábitos pendientes, bloques de calendario de hoy y recordatorios próximos (7 días).",
            "parameters": ListarHoyArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_habito",
            "description": "Registra un hábito recurrente con nombre, veces por semana (1-7) y hora habitual (HH:MM).",
            "parameters": CrearHabitoArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registrar_cumplimiento",
            "description": "Marca una tarea como completada o registra cumplimiento de un hábito (suma +1 a la semana).",
            "parameters": RegistrarCumplimientoArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_hueco",
            "description": "Busca hasta 3 franjas libres entre 08:00 y 22:00 para una duración dada (minutos) en una fecha (hoy por defecto).",
            "parameters": BuscarHuecoArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reservar_bloque",
            "description": "Agenda un bloque en el calendario con título, inicio/fin ISO 8601 UTC y categoría (estudio|trabajo|deporte|social|personal|recordatorio). Verifica no solapamiento.",
            "parameters": ReservarBloqueArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_recordatorio",
            "description": "Crea un bloque tipo recordatorio en el calendario. Si detecta palabras clave de evento especial (aniversario, evento), también lo guarda en memoria para avisar 7 días antes.",
            "parameters": CrearRecordatorioArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_dato",
            "description": "Guarda un hecho clave-valor en la memoria de largo plazo de la sesión (máx 30 hechos, FIFO). Redacta PII automáticamente.",
            "parameters": GuardarDatoArgs.model_json_schema(),
        },
    },
]

TOOL_MAP = {
    "crear_tarea": tool_crear_tarea,
    "listar_hoy": tool_listar_hoy,
    "crear_habito": tool_crear_habito,
    "registrar_cumplimiento": tool_registrar_cumplimiento,
    "buscar_hueco": tool_buscar_hueco,
    "reservar_bloque": tool_reservar_bloque,
    "crear_recordatorio": tool_crear_recordatorio,
    "guardar_dato": tool_guardar_dato,
}