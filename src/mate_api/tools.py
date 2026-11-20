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

async def tool_crear_tarea(session_id: str, args: dict) -> ToolResult:
    """1. Crear una tarea para hoy."""
    try:
        validated = CrearTareaArgs(**args)
        task = await create_task(session_id, validated.titulo, validated.hora_sugerida)
        return ToolResult(success=True, data=task.model_dump())
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
    """3. Registra un hábito (ej. gym, estudio)."""
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
    """6. Agenda un bloque en el calendario verificando que no se solape."""
    try:
        validated = ReservarBloqueArgs(**args)
        cat = Category(validated.categoria)
        block = await create_calendar_block(
            session_id,
            validated.titulo,
            validated.inicio_iso,
            validated.fin_iso,
            cat,
        )
        return ToolResult(success=True, data=block.model_dump())
    except ValueError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def tool_crear_recordatorio(session_id: str, args: dict) -> ToolResult:
    """7. Crea un bloque tipo recordatorio y, si es evento especial, registra memoria para avisar 7 días antes."""
    try:
        validated = CrearRecordatorioArgs(**args)
        # Crear bloque en calendario (categoría recordatorio)
        # Combinar fecha + hora en ISO UTC
        inicio_iso = f"{validated.fecha_iso}T{validated.hora}:00+00:00"
        # Duración por defecto 30 min para recordatorios
        inicio_dt = datetime.fromisoformat(inicio_iso.replace("Z", "+00:00"))
        fin_dt = inicio_dt + timedelta(minutes=30)
        fin_iso = fin_dt.isoformat()

        block = await create_calendar_block(
            session_id,
            validated.titulo,
            inicio_iso,
            fin_iso,
            Category.RECORDATORIO,
        )

        # Si parece un cumpleaños/evento especial, guardar en memoria con fecha_evento
        memory = None
        keywords = ["cumple", "cumpleaños", "aniversario", "aniversari", "evento", "cita", "reunion", "reunión"]
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
            "description": "Agenda un bloque en el calendario con título, inicio/fin ISO 8601 UTC y categoría (estudio|gym|personal). Verifica no solapamiento.",
            "parameters": ReservarBloqueArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_recordatorio",
            "description": "Crea un bloque tipo recordatorio en el calendario. Si detecta palabras clave de evento especial (cumpleaños, aniversario), también lo guarda en memoria para avisar 7 días antes.",
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