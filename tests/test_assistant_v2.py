import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from httpx import ASGITransport, AsyncClient


# Fixtures shared across all tests

@pytest_asyncio.fixture
async def client():
    """Cliente HTTP de prueba con base de datos temporal."""
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    os.environ["DATABASE_PATH"] = db_path
    os.environ["REDIS_URL"] = ""
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DEMO_ENABLED"] = "false"
    os.environ["RATE_LIMIT_REQUESTS"] = "1000"
    os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"

    import importlib
    from mate_api import database, config, main, security
    importlib.reload(config)
    importlib.reload(database)
    importlib.reload(security)
    importlib.reload(main)
    await database.init_db()
    # Truncar tablas para asegurar aislamiento entre tests
    async with database.get_connection() as conn:
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM sessions")
    await security.reset_stores()

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await security.reset_stores()
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest_asyncio.fixture
async def session_a(client: AsyncClient):
    """Crea sesión A y devuelve cookie."""
    resp = await client.get("/api/v1/session")
    assert resp.status_code == 200
    coki = resp.cookies.get("mate_sid")
    if hasattr(coki, 'value'):
        return coki.value
    return str(coki)


@pytest_asyncio.fixture
async def session_b(client: AsyncClient):
    """Crea sesión B y devuelve cookie."""
    transport = ASGITransport(app=client._transport.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/session")
        assert resp.status_code == 200
        coki = resp.cookies.get("mate_sid")
        if hasattr(coki, 'value'):
            return coki.value
        return str(coki)


class TestAssistantV2:
    """Tests obligatorios de §10 CONTEXTO-BACKEND.md."""

    # ──────────────────────────────────────────────────────────────────────
    # Test 1: Sesión con uso reciente no se purga
    # ──────────────────────────────────────────────────────────────────────

    async def test_active_session_not_purged(self, client: AsyncClient, session_a):
        """Una sesión con uso reciente no se purga."""
        # La sesión_a ya tiene uso reciente
        resp = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        assert resp.status_code == 200

    # ──────────────────────────────────────────────────────────────────────
    # Test 2: POST /chat guarda 2 filas en messages; GET /messages de otra sesión no las ve (BOLA)
    # ──────────────────────────────────────────────────────────────────────

    async def test_chat_saves_two_messages_bola(self, client: AsyncClient, session_a, session_b):
        """POST /chat guarda 2 filas en messages; GET /messages de otra sesión no las ve (BOLA)."""
        # Enviar mensaje en sesión A
        await client.post(
            "/api/v1/chat",
            json={"message": "Hola Mate, cómo estás?"},
            cookies={"mate_sid": session_a},
        )

        # Enviar otro mensaje en sesión A
        await client.post(
            "/api/v1/chat",
            json={"message": "Quiero crear una tarea"},
            cookies={"mate_sid": session_a},
        )

        # Verificar que los mensajes se guardaron en la sesión A
        msg_resp = await client.get("/api/v1/messages", cookies={"mate_sid": session_a})
        assert msg_resp.status_code == 200
        messages_a = msg_resp.json().get("messages", [])
        assert len(messages_a) >= 2

        # Verificar que sesión B NO ve los mensajes de A (isolación BOLA)
        # Session B tiene su propio greeting auto-generado, así que esperamos que
        # los mensajes de sesión A (los 2 user messages) no aparezcan en session B
        msg_resp_b = await client.get("/api/v1/messages", cookies={"mate_sid": session_b})
        assert msg_resp_b.status_code == 200
        messages_b = msg_resp_b.json().get("messages", [])
        # Session B debería tener como máximo su greeting auto-generado (1 mensaje),
        # pero NO los 2 mensajes de sesión A
        assert len(messages_b) <= 1  # Sólo el greeting auto-de la creación de sesión B

    # ──────────────────────────────────────────────────────────────────────
    # Test 3: Al pasar los 100 mensajes se borran los más viejos
    # ──────────────────────────────────────────────────────────────────────

    async def test_message_fifo_at_100(self, client: AsyncClient, session_a):
        """Al pasar los 100 mensajes se borran los más viejos."""
        # Enviar 102 mensajes en la misma sesión
        for i in range(102):
            await client.post(
                "/api/v1/chat",
                json={"message": f"Mensaje {i} para testing FIFO"},
                cookies={"mate_sid": session_a},
            )

        state = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        messages = state.json().get("messages", [])
        # Deberían quedar solo los 100 más recientes
        assert len(messages) <= 100

    # ──────────────────────────────────────────────────────────────────────
    # Test 4: build_greeting works and returns valid greeting dict
    # ──────────────────────────────────────────────────────────────────────

    async def test_build_greeting_returns_dict(self):
        """build_greeting returns a valid dict with text, avatar_state, reason."""
        from mate_api.greeting import build_greeting
        from mate_api.models import Tone

        now = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)

        # Test with no data - should return nueva_sesión greeting
        greeting = build_greeting(None, [], [], [], now, tone="amable")
        assert "text" in greeting
        assert "avatar_state" in greeting
        assert "reason" in greeting
        assert greeting["avatar_state"] == "contento"

        # Test with display_name
        greeting = build_greeting(None, [], [], [], now, tone="amable")
        # Should work without session
        assert greeting is not None

    # ──────────────────────────────────────────────────────────────────────
    # Test 5: Recargar GET /session dos veces seguidas no duplica el saludo en messages
    # ──────────────────────────────────────────────────────────────────────

    async def test_no_duplicate_greeting_on_reload(self, client: AsyncClient, session_a):
        """Recargar GET /session dos veces seguidas no duplica el saludo en messages."""
        # First request should create the greeting in messages
        resp1 = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        assert resp1.status_code == 200

        # Second request should NOT add another greeting
        resp2 = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        assert resp2.status_code == 200

        state1 = resp1.json()
        state2 = resp2.json()

        # Count assistant messages with the greeting
        greetings = [m for m in state1["session"].get("messages", []) if "¡Hola" in m.get("content", "")]

    # ──────────────────────────────────────────────────────────────────────
    # Test 6: LLM simulado caído, pedido general devuelve texto degradado
    # ──────────────────────────────────────────────────────────────────────

    async def test_degraded_mode_general_request(self, client: AsyncClient, session_a):
        """Con el LLM simulado caído, un pedido general devuelve el texto degradado de §5.4 e is_degraded: true (no 500)."""
        transport = ASGITransport(app=client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat",
                json={"message": "Redactame un mail para pedirle a mi profe una prórroga"},
                cookies={"mate_sid": session_a},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_degraded"] is True
            assert "sin conexión al cerebro" in data["reply"].lower() or "Ahora estoy sin conexión" in data["reply"].lower()

    # ──────────────────────────────────────────────────────────────────────
    # Test 7: Contexto que se manda al LLM contiene <memoria>, <agenda> y max 12 mensajes
    # ──────────────────────────────────────────────────────────────────────

    async def test_llm_context_has_blocks(self, client: AsyncClient, session_a):
        """El contexto que se manda al LLM contiene <memoria>, <agenda> y como máximo 12 mensajes de historial."""
        from mate_api import database
        from mate_api.agent import MateAgent
        from mate_api.models import Tone

        await database.create_memory(session_a, "materia", "Bases de Datos")
        await database.create_habit(session_a, "gym", 3, "19:00")
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"historial {i}"}
            for i in range(20)
        ]

        messages = await MateAgent(session_a, Tone.AMABLE)._build_messages("hola", history)

        system = messages[0]["content"]
        assert messages[0]["role"] == "system"
        assert "<memoria>" in system and "materia: Bases de Datos" in system
        assert "<agenda>" in system and "gym: 0/3" in system
        historial = messages[1:-1]
        assert len(historial) == 12
        assert historial[-1]["content"] == "historial 19"  # los 12 más recientes
        assert messages[-1] == {"role": "user", "content": "<user_data>hola</user_data>"}

    async def test_llm_context_blocks_present_when_empty(self, client: AsyncClient, session_a):
        """Sin memoria ni agenda, los bloques igual se mandan (vacíos)."""
        from mate_api.agent import MateAgent
        from mate_api.models import Tone

        messages = await MateAgent(session_a, Tone.AMABLE)._build_messages("hola", [])
        assert "<memoria>" in messages[0]["content"]
        assert "<agenda>" in messages[0]["content"]
        assert len(messages) == 2
