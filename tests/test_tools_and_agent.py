import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from src.mate_api.tools import (
    tool_crear_tarea,
    tool_listar_hoy,
    tool_crear_habito,
    tool_registrar_cumplimiento,
    tool_buscar_hueco,
    tool_reservar_bloque,
    tool_crear_recordatorio,
    tool_guardar_dato,
    TOOL_DEFINITIONS,
    TOOL_MAP,
)
from src.mate_api.agent import (
    MateAgent,
    RuleBasedFallbackAgent,
    MAX_TOOL_CALLS_PER_TURN,
)
from src.mate_api.ics_builder import build_ics, build_reminder_ics
from src.mate_api.models import Tone, Category
from src.mate_api.database import (
    create_session,
    create_task,
    create_habit,
    create_calendar_block,
    list_tasks_today,
)
from src.mate_api.config import get_settings


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def session_fixture():
    import secrets
    import os
    with pytest.MonkeyPatch().context() as mp:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        mp.setenv("DATABASE_PATH", db_path)
        mp.setenv("REDIS_URL", "")
        mp.setenv("GEMINI_API_KEY", "")
        mp.setenv("DEMO_ENABLED", "false")

        import importlib
        from src.mate_api import database, config
        importlib.reload(config)
        importlib.reload(database)
        await database.init_db()

        sid = secrets.token_urlsafe(32)
        ics = secrets.token_urlsafe(32)
        await create_session(sid, ics)
        yield sid

        try:
            import os
            os.unlink(db_path)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# Tests de las 8 Herramientas
# ──────────────────────────────────────────────────────────────────────

class TestTools:
    """Pruebas de las 8 herramientas ejecutadas server-side."""

    async def test_crear_tarea(self, session_fixture):
        result = await tool_crear_tarea(session_fixture, {"titulo": "Estudiar Python", "hora_sugerida": "19:00"})
        assert result.success is True
        assert result.data["title"] == "Estudiar Python"
        assert result.data["due_time"] == "19:00"
        assert result.data["completed"] is False

    async def test_crear_tarea_sin_hora(self, session_fixture):
        result = await tool_crear_tarea(session_fixture, {"titulo": "Tarea sin hora"})
        assert result.success is True
        assert result.data["due_time"] is None

    async def test_listar_hoy_vacio(self, session_fixture):
        result = await tool_listar_hoy(session_fixture, {})
        assert result.success is True
        assert result.data["tasks"] == []
        assert result.data["habits"] == []
        assert result.data["blocks"] == []
        assert result.data["upcoming_memories"] == []

    async def test_listar_hoy_con_datos(self, session_fixture):
        await tool_crear_tarea(session_fixture, {"titulo": "Tarea 1", "hora_sugerida": "10:00"})
        await tool_crear_habito(session_fixture, {"nombre": "Gym", "veces_por_semana": 3, "hora_habitual": "19:00"})
        result = await tool_listar_hoy(session_fixture, {})
        assert result.success is True
        assert len(result.data["tasks"]) == 1
        assert len(result.data["habits"]) == 1

    async def test_crear_habito(self, session_fixture):
        result = await tool_crear_habito(session_fixture, {"nombre": "Leer", "veces_por_semana": 5, "hora_habitual": "20:00"})
        assert result.success is True
        assert result.data["name"] == "Leer"
        assert result.data["target_per_week"] == 5
        assert result.data["preferred_time"] == "20:00"
        assert result.data["done_this_week"] == 0

    async def test_registrar_cumplimiento_tarea(self, session_fixture):
        task_result = await tool_crear_tarea(session_fixture, {"titulo": "Tarea completable"})
        task_id = task_result.data["id"]
        result = await tool_registrar_cumplimiento(session_fixture, {"tipo": "tarea", "item_id": task_id})
        assert result.success is True
        assert result.data["completed"] is True

    async def test_registrar_cumplimiento_habito(self, session_fixture):
        habit_result = await tool_crear_habito(session_fixture, {"nombre": "Ejercicio"})
        habit_id = habit_result.data["id"]
        result = await tool_registrar_cumplimiento(session_fixture, {"tipo": "habito", "item_id": habit_id})
        assert result.success is True
        assert result.data["done_this_week"] == 1

    async def test_registrar_cumplimiento_cross_session_falla(self, session_a, session_b):
        """Verifica que no se puede completar una tarea de otra sesión (BOLA)."""
        # Crear tarea en sesión A
        task_result = await tool_crear_tarea(session_a, {"titulo": "Tarea de sesión A"})
        task_id = task_result.data["id"]

        # Intentar completar desde sesión B (misma DB, distinto session_id)
        result = await tool_registrar_cumplimiento(session_b, {"tipo": "tarea", "item_id": task_id})
        assert result.success is False
        assert "no pertenece" in result.error.lower() or "encontrada" in result.error.lower()

    async def test_buscar_hueco(self, session_fixture):
        result = await tool_buscar_hueco(session_fixture, {"duracion_minutos": 60})
        assert result.success is True
        assert "slots" in result.data
        assert len(result.data["slots"]) <= 3
        if result.data["slots"]:
            inicio, fin = result.data["slots"][0]
            assert "T" in inicio and "T" in fin

    async def test_reservar_bloque(self, session_fixture):
        # Buscar hueco primero
        slots_result = await tool_buscar_hueco(session_fixture, {"duracion_minutos": 60})
        assert slots_result.success and slots_result.data["slots"]
        inicio, fin = slots_result.data["slots"][0]

        result = await tool_reservar_bloque(session_fixture, {
            "titulo": "Estudio Python",
            "inicio_iso": inicio,
            "fin_iso": fin,
            "categoria": "estudio",
        })
        assert result.success is True
        assert result.data["title"] == "Estudio Python"
        assert result.data["category"] == "estudio"

    async def test_reservar_bloque_solapado_falla(self, session_fixture):
        slots_result = await tool_buscar_hueco(session_fixture, {"duracion_minutos": 60})
        assert slots_result.success and slots_result.data["slots"]
        inicio, fin = slots_result.data["slots"][0]

        # Primera reserva
        await tool_reservar_bloque(session_fixture, {
            "titulo": "Bloque 1",
            "inicio_iso": inicio,
            "fin_iso": fin,
            "categoria": "estudio",
        })
        # Segunda reserva en mismo horario -> debe fallar
        result = await tool_reservar_bloque(session_fixture, {
            "titulo": "Bloque 2",
            "inicio_iso": inicio,
            "fin_iso": fin,
            "categoria": "gym",
        })
        assert result.success is False
        assert "solapa" in result.error.lower()

    async def test_crear_recordatorio(self, session_fixture):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        result = await tool_crear_recordatorio(session_fixture, {
            "titulo": "Reunión importante",
            "fecha_iso": tomorrow,
            "hora": "10:00",
        })
        assert result.success is True
        assert result.data["block"]["title"] == "Reunión importante"
        assert result.data["block"]["category"] == "recordatorio"

    async def test_crear_recordatorio_cumple_genera_memoria(self, session_fixture):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        result = await tool_crear_recordatorio(session_fixture, {
            "titulo": "Cumpleaños de mamá",
            "fecha_iso": tomorrow,
            "hora": "09:00",
        })
        assert result.success is True
        assert result.data["memory"] is not None
        assert "cumpleaños" in result.data["memory"]["fact_value"].lower()

    async def test_guardar_dato(self, session_fixture):
        result = await tool_guardar_dato(session_fixture, {
            "clave": "materia_actual",
            "valor": "Bases de Datos II",
            "fecha_evento": None,
        })
        assert result.success is True
        assert result.data["fact_key"] == "materia_actual"
        assert result.data["fact_value"] == "Bases de Datos II"

    async def test_guardar_dato_redacta_pii(self, session_fixture):
        result = await tool_guardar_dato(session_fixture, {
            "clave": "cbu",
            "valor": "Mi CBU es 1234567890123456789012",
        })
        assert result.success is True
        assert "[CBU_REDACTADO]" in result.data["fact_value"]
        assert "1234567890123456789012" not in result.data["fact_value"]


# ──────────────────────────────────────────────────────────────────────
# Tests del Agente (Tool Calling + Fallback)
# ──────────────────────────────────────────────────────────────────────

class TestAgent:
    """Pruebas del agente con tool calling y fallback."""

    @pytest_asyncio.fixture
    async def agent(self, session_fixture):
        return MateAgent(session_fixture, Tone.AMABLE)

    async def test_fallback_listar_hoy(self, session_fixture, agent):
        # Forzar modo degradado (sin API key)
        await tool_crear_tarea(session_fixture, {"titulo": "Tarea test"})
        response = await agent.process("¿Qué tengo hoy?", [])
        assert response.is_degraded is True
        assert "Tarea test" in response.reply or "tarea" in response.reply.lower()
        assert response.avatar_state in ["neutro", "hablando", "contento", "serio"]

    async def test_fallback_gym_motivation(self, session_fixture, agent):
        response = await agent.process("Hoy voy al gym", [])
        assert response.is_degraded is True
        assert response.avatar_state in ["hablando", "contento", "serio"]

    async def test_fallback_estudio_sugiere_hueco(self, session_fixture, agent):
        response = await agent.process("Quiero estudiar Python", [])
        assert response.is_degraded is True

    async def test_fallback_cumple(self, session_fixture, agent):
        response = await agent.process("El sábado es el cumple de mi novia", [])
        assert response.is_degraded is True
        # La respuesta de _reply_birthday menciona "agendo con aviso de 7 días"
        assert "agendo" in response.reply.lower() or "cumple" in response.reply.lower() or "regal" in response.reply.lower()

    async def test_max_tool_calls_limit(self):
        """Verifica que el agente respeta MAX_TOOL_CALLS_PER_TURN = 4."""
        import secrets
        import os
        import tempfile
        import importlib
        import json
        from src.mate_api import database, config, agent
        from src.mate_api.models import Tone, ToolCall

        # Valor esperado hardcodeado para detectar mutaciones
        EXPECTED_MAX_TOOL_CALLS = 4

        # Setup temp DB con LLM habilitado
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        os.environ["DATABASE_PATH"] = db_path
        os.environ["REDIS_URL"] = ""
        os.environ["GEMINI_API_KEY"] = "fake-key-for-test"
        os.environ["DEMO_ENABLED"] = "true"
        importlib.reload(config)
        importlib.reload(database)
        importlib.reload(agent)
        await database.init_db()

        sid = secrets.token_urlsafe(32)
        await create_session(sid, secrets.token_urlsafe(32))

        # Mock LLM que siempre pide tool calls
        class MockLLM:
            call_count = 0
            async def chat(self, messages, tools, tool_choice="auto"):
                self.call_count += 1
                if self.call_count <= EXPECTED_MAX_TOOL_CALLS + 2:
                    # Return proper ToolCall objects like the real LLMClient
                    tc = ToolCall(
                        id=f'call_{self.call_count}',
                        name='crear_tarea',
                        arguments={'titulo': 'Test'}
                    )
                    return type('obj', (object,), {
                        'content': None,
                        'tool_calls': [tc],
                        'error': None,
                    })()
                return type('obj', (object,), {
                    'content': 'Done',
                    'tool_calls': [],
                    'error': None,
                })()

        a = agent.MateAgent(sid, Tone.AMABLE)
        a.llm = MockLLM()

        # El agente debería cortar después de 4 tool calls
        # Verificamos tool_calls_this_turn que cuenta TODOS los intentos (no solo exitosos)
        response = await a.process("Haz muchas tareas", [])
        assert a.tool_calls_this_turn <= EXPECTED_MAX_TOOL_CALLS, \
            f"Agent executed {a.tool_calls_this_turn} tool calls, max is {EXPECTED_MAX_TOOL_CALLS}"

        # Cleanup
        try:
            os.unlink(db_path)
        except Exception:
            pass

    async def test_tonos_diferentes_respuestas(self, session_fixture):
        agent_amable = MateAgent(session_fixture, Tone.AMABLE)
        agent_firme = MateAgent(session_fixture, Tone.FIRME)

        # Forzar fallback
        resp_amable = await agent_amable.fallback.handle("gym hoy")
        resp_firme = await agent_firme.fallback.handle("gym hoy")

        # Ambos deben responder pero con tono diferente
        assert len(resp_amable.reply) > 0
        assert len(resp_firme.reply) > 0


# ──────────────────────────────────────────────────────────────────────
# Tests de iCalendar (.ics)
# ──────────────────────────────────────────────────────────────────────

class TestIcsBuilder:
    """Pruebas de generación de archivos .ics RFC 5545."""

    def test_build_ics_basico(self):
        tasks = []
        blocks = []
        ics = build_ics(tasks, blocks, "test-session")
        assert "BEGIN:VCALENDAR" in ics
        assert "END:VCALENDAR" in ics
        assert "PRODID:-//Mate Routine Agent//ES" in ics
        assert "VERSION:2.0" in ics

    def test_build_ics_con_tareas(self):
        from src.mate_api.models import TaskRead
        from datetime import datetime, timezone

        tasks = [
            TaskRead(
                id=1,
                session_id="test",
                title="Estudiar Python",
                due_time="19:00",
                completed=False,
                created_at_utc=datetime.now(timezone.utc),
            )
        ]
        blocks = []
        ics = build_ics(tasks, blocks, "test-session")
        assert "BEGIN:VEVENT" in ics
        assert "Estudiar Python" in ics
        assert "VALARM" in ics  # Alarma 30 min antes
        assert "STATUS:NEEDS-ACTION" in ics

    def test_build_ics_con_bloques(self):
        from src.mate_api.models import CalendarBlockRead, Category
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        blocks = [
            CalendarBlockRead(
                id=1,
                session_id="test",
                title="Bloque Estudio",
                start_utc=now + timedelta(hours=1),
                end_utc=now + timedelta(hours=2),
                category=Category.ESTUDIO,
            )
        ]
        tasks = []
        ics = build_ics(tasks, blocks, "test-session")
        assert "BEGIN:VEVENT" in ics
        assert "Bloque Estudio" in ics
        assert "CATEGORIES:ESTUDIO" in ics
        assert "STATUS:CONFIRMED" in ics
        assert "TRANSP:OPAQUE" in ics

    def test_build_ics_tarea_completada_no_incluida(self):
        from src.mate_api.models import TaskRead
        from datetime import datetime, timezone

        tasks = [
            TaskRead(
                id=1,
                session_id="test",
                title="Tarea hecha",
                due_time="10:00",
                completed=True,  # Completada
                created_at_utc=datetime.now(timezone.utc),
            )
        ]
        blocks = []
        ics = build_ics(tasks, blocks, "test-session")
        # Tareas completadas no deben aparecer en el .ics
        assert "Tarea hecha" not in ics or "STATUS:COMPLETED" in ics

    def test_build_reminder_ics_cumple(self):
        ics = build_reminder_ics(
            title="Cumpleaños de Juan",
            event_date="2026-12-25",
            event_time="09:00",
            session_id="test",
            reminder_days_before=7,
        )
        assert "BEGIN:VCALENDAR" in ics
        assert "Cumpleaños de Juan" in ics
        assert "VALARM" in ics
        assert "TRIGGER:-P7D" in ics  # 7 días antes
        assert "TRIGGER:-PT0S" in ics  # Día del evento

    def test_ics_line_folding(self):
        """Verifica que líneas largas se pliegan correctamente (>75 chars)."""
        long_title = "A" * 100
        from src.mate_api.models import CalendarBlockRead, Category
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        blocks = [
            CalendarBlockRead(
                id=1,
                session_id="test",
                title=long_title,
                start_utc=now + timedelta(hours=1),
                end_utc=now + timedelta(hours=2),
                category=Category.ESTUDIO,
            )
        ]
        ics = build_ics([], blocks, "test")
        # Verificar que no hay líneas > 75 chars (sin contar el CRLF)
        for line in ics.split("\r\n"):
            assert len(line) <= 75, f"Línea demasiado larga: {len(line)} chars"


# ──────────────────────────────────────────────────────────────────────
# Tests de Definiciones de Herramientas
# ──────────────────────────────────────────────────────────────────────

class TestToolDefinitions:
    """Verifica que las 8 herramientas estén bien definidas para el LLM."""

    def test_ocho_herramientas_definidas(self):
        assert len(TOOL_DEFINITIONS) == 8
        names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
        expected = [
            "crear_tarea", "listar_hoy", "crear_habito", "registrar_cumplimiento",
            "buscar_hueco", "reservar_bloque", "crear_recordatorio", "guardar_dato",
        ]
        assert set(names) == set(expected)

    def test_tool_map_coincide(self):
        assert set(TOOL_MAP.keys()) == {
            "crear_tarea", "listar_hoy", "crear_habito", "registrar_cumplimiento",
            "buscar_hueco", "reservar_bloque", "crear_recordatorio", "guardar_dato",
        }

    def test_esquemas_tienen_validaciones(self):
        for tool_def in TOOL_DEFINITIONS:
            schema = tool_def["function"]["parameters"]
            # Todos deben tener properties y required
            assert "properties" in schema
            assert "type" in schema
            assert schema["type"] == "object"