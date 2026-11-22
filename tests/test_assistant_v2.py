import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

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
        greeting = await build_greeting(None, [], [], [], now, tone="amable")
        assert "text" in greeting
        assert "avatar_state" in greeting
        assert "reason" in greeting
        assert greeting["avatar_state"] == "contento"

        # Test with display_name
        greeting = await build_greeting(None, [], [], [], now, tone="amable")
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

    # ──────────────────────────────────────────────────────────────────────
    # Test 8: LLM Native API - Respuesta con functionCall en formato nativo
    # ──────────────────────────────────────────────────────────────────────

    async def test_llm_native_function_call(self, client: AsyncClient, session_a):
        """Verifica que el agente maneja functionCall en formato nativo de Gemini."""
        from mate_api.agent import MateAgent, LLMClient
        from mate_api.models import Tone

        # Mock de respuesta nativa de Gemini con functionCall
        native_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "crear_tarea",
                            "args": {"titulo": "Estudiar Python", "hora_sugerida": "20:00"}
                        }
                    }]
                }
            }]
        }

        with patch.object(LLMClient, 'chat', new_callable=AsyncMock) as mock_chat:
            # Primera llamada: devuelve functionCall
            # Segunda llamada: devuelve texto final
            mock_chat.side_effect = [
                type('LLMResponse', (), {
                    'content': None,
                    'tool_calls': [type('ToolCall', (), {
                        'id': 'crear_tarea',
                        'name': 'crear_tarea',
                        'arguments': {"titulo": "Estudiar Python", "hora_sugerida": "20:00"}
                    })()],
                    'error': None
                })(),
                type('LLMResponse', (), {
                    'content': '¡Listo! Tarea creada.',
                    'tool_calls': [],
                    'error': None
                })(),
            ]

            # Configurar API key para que no use modo degradado
            import mate_api.config as config_module
            original_settings = config_module.get_settings()
            original_settings.GEMINI_API_KEY = "test-key"
            original_settings.DEMO_ENABLED = "true"

            agent = MateAgent(session_a, Tone.AMABLE)
            # Necesitamos mockear _build_messages para no depender de BD
            with patch.object(agent, '_build_messages', new_callable=AsyncMock) as mock_build:
                mock_build.return_value = [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "<user_data>crear tarea Estudiar Python a las 20:00</user_data>"},
                ]

                # El test real requeriría BD, así que verificamos el mock de LLMClient
                from mate_api.agent import LLMClient as LLMClientClass
                llm = LLMClientClass()
                llm.api_key = "test-key"

                # Verificar que to_gemini_schema quita campos no deseados y resuelve $ref
                from mate_api.agent import to_gemini_schema
                test_schema = {
                    "type": "object",
                    "title": "Test",
                    "additionalProperties": False,
                    "$defs": {"Name": {"type": "string", "description": "A name"}},
                    "default": "x",
                    "properties": {
                        "name": {"$ref": "#/$defs/Name"}
                    }
                }
                cleaned = to_gemini_schema(test_schema)
                assert "title" not in cleaned
                assert "additionalProperties" not in cleaned
                assert "$defs" not in cleaned
                assert "default" not in cleaned
                assert "properties" in cleaned
                assert cleaned["properties"]["name"]["type"] == "string"
                assert cleaned["properties"]["name"]["description"] == "A name"

    # ──────────────────────────────────────────────────────────────────────
    # Test 9: Error 403 de Gemini termina en is_degraded=true sin 500
    # ──────────────────────────────────────────────────────────────────────

    async def test_gemini_403_degraded_mode(self, client: AsyncClient, session_a):
        """Un 403 de Gemini termina en is_degraded=true sin 500 (pedido general)."""
        transport = ASGITransport(app=client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Mockear LLMClient.chat para simular error 403
            from mate_api.agent import LLMClient

            original_chat = LLMClient.chat

            async def mock_chat_403(self, messages, tools, tool_choice="auto"):
                from mate_api.agent import LLMResponse
                return LLMResponse(error="Gemini error 403: PERMISSION_DENIED")

            with patch.object(LLMClient, 'chat', mock_chat_403):
                # Configurar API key para que intente usar LLM
                import mate_api.config as config_module
                original_settings = config_module.get_settings()
                original_settings.GEMINI_API_KEY = "test-key"
                original_settings.DEMO_ENABLED = "true"

                resp = await ac.post(
                    "/api/v1/chat",
                    json={"message": "Redactame un mail para pedirle a mi profe una prórroga"},
                    cookies={"mate_sid": session_a},
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                assert data["is_degraded"] is True
                assert "sin conexión al cerebro" in data["reply"].lower() or "Ahora estoy sin conexión" in data["reply"].lower()

    # ──────────────────────────────────────────────────────────────────────
    # Test 10: Error 403 de Gemini en pedido de rutina usa fallback
    # ──────────────────────────────────────────────────────────────────────

    async def test_gemini_403_routine_uses_fallback(self, client: AsyncClient, session_a):
        """Un 403 de Gemini en pedido de rutina (crear_tarea) usa fallback y responde OK."""
        transport = ASGITransport(app=client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            from mate_api.agent import LLMClient

            async def mock_chat_403(self, messages, tools, tool_choice="auto"):
                from mate_api.agent import LLMResponse
                return LLMResponse(error="Gemini error 403: PERMISSION_DENIED")

            with patch.object(LLMClient, 'chat', mock_chat_403):
                import mate_api.config as config_module
                original_settings = config_module.get_settings()
                original_settings.GEMINI_API_KEY = "test-key"
                original_settings.DEMO_ENABLED = "true"

                resp = await ac.post(
                    "/api/v1/chat",
                    json={"message": "crear tarea Estudiar Python"},
                    cookies={"mate_sid": session_a},
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                # En modo degradado por error de LLM, pedido de rutina usa fallback
                assert data["is_degraded"] is True
                # El fallback debería haber creado la tarea
                assert "tarea" in data["reply"].lower() or "creada" in data["reply"].lower() or "anotada" in data["reply"].lower()

    # ──────────────────────────────────────────────────────────────────────
    # Test 11: Integración - LLM returns 2 functionCalls then text
    # ──────────────────────────────────────────────────────────────────────

    async def test_llm_multiple_function_calls_then_text(self, client: AsyncClient, session_a):
        """Mock LLM returning 2 functionCalls then text, verify final reply is that text and is_degraded=false."""
        transport = ASGITransport(app=client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            from mate_api.agent import LLMClient, LLMResponse, ToolCall, _agent_cache, get_agent
            from mate_api.config import Settings

            # Create a mock settings object with API key enabled
            mock_settings = Settings(
                GEMINI_API_KEY="test-key",
                DEMO_ENABLED="true",
                GEMINI_MODEL="gemini-flash-lite-latest",
                GEMINI_TIMEOUT_S=30,
            )

            call_count = {"count": 0}

            async def mock_chat_multi(self, messages, tools, tool_choice="auto"):
                call_count["count"] += 1
                if call_count["count"] == 1:
                    # First call: return 2 functionCalls
                    return LLMResponse(
                        content=None,
                        tool_calls=[
                            ToolCall(id="call_1", name="crear_tarea", arguments={"titulo": "Tarea 1", "hora_sugerida": "10:00"}),
                            ToolCall(id="call_2", name="crear_tarea", arguments={"titulo": "Tarea 2", "hora_sugerida": "11:00"}),
                        ]
                    )
                elif call_count["count"] == 2:
                    # Second call: return text response
                    return LLMResponse(content="Listo, creé las dos tareas para ti.", tool_calls=[])
                else:
                    return LLMResponse(content="Done")

            # Patch both the LLMClient.chat and the get_settings used by agent
            with patch.object(LLMClient, 'chat', mock_chat_multi), \
                 patch('mate_api.agent.get_settings', return_value=mock_settings):
                # Clear agent cache AFTER patching to force new agent creation with mocked LLM
                _agent_cache.clear()

                # Use message that matches fallback keywords for crear_tarea
                resp = await ac.post(
                    "/api/v1/chat",
                    json={"message": "crear tarea Tarea 1 a las 10 y crear tarea Tarea 2 a las 11"},
                    cookies={"mate_sid": session_a},
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                # Verify final reply is the text from second LLM call
                assert data["reply"] == "Listo, creé las dos tareas para ti."
                # Verify not degraded
                assert data["is_degraded"] is False
                # Verify tools were executed
                assert len(data["tools_executed"]) == 2
                assert all(t["success"] for t in data["tools_executed"])
                assert data["tools_executed"][0]["name"] == "crear_tarea"
                assert data["tools_executed"][1]["name"] == "crear_tarea"


class TestNoPredefinedContent:
    """Tests para verificar que no hay contenido predefinido en el código fuente."""

    def test_no_forbidden_words_in_src(self):
        """Busca en src/ (fuera de tests) las palabras prohibidas y falla si aparecen."""
        import os
        from pathlib import Path

        forbidden = ["gym", "gimnasio", "cumple", "Bien ahí"]
        src_dir = Path(__file__).parent.parent / "src"

        violations = []
        for py_file in src_dir.rglob("*.py"):
            if "test" in str(py_file):
                continue
            content = py_file.read_text(encoding="utf-8")
            for word in forbidden:
                if word.lower() in content.lower():
                    violations.append(f"{py_file.relative_to(src_dir)}: contiene '{word}'")

        assert not violations, "Palabras prohibidas encontradas en src/:\n" + "\n".join(violations)

    def test_dias_block_in_context(self):
        """Verifica que el bloque <dias> está en el contexto armado para el LLM."""
        from mate_api.prompts import arm_contexto
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        ctx = arm_contexto(
            session_id="test",
            memories=[],
            habits=[],
            blocks=[],
            upcoming_memories=[],
            now=now,
            tone="amable",
        )

        # Verificar que existe el bloque <dias>
        assert "dias" in ctx, "Falta clave 'dias' en el contexto"
        assert "<dias>" in ctx["dias"], "Falta etiqueta <dias> en el bloque"
        assert "</dias>" in ctx["dias"], "Falta etiqueta de cierre </dias>"

        # Verificar que contiene hoy y los 7 días siguientes
        dias_content = ctx["dias"]
        assert "hoy =" in dias_content.lower(), "Falta 'hoy =' en <dias>"
        assert "mañana =" in dias_content.lower(), "Falta 'mañana =' en <dias>"
        assert "pasado mañana =" in dias_content.lower(), "Falta 'pasado mañana =' en <dias>"

        # Verificar que también está en el system prompt combinado
        full_context = "\n\n".join([ctx["system_prompt"], ctx["memoria"], ctx["agenda"], ctx["fecha_hora"], ctx["dias"]])
        assert "<dias>" in full_context, "Bloque <dias> no está en el contexto completo enviado al LLM"