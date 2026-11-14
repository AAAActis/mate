"""
Tests de seguridad a nivel API (FastAPI + httpx):
- Aislamiento BOLA en endpoints HTTP
- CORS cerrado
- Rate Limiting
- Revocación .ics
- Borrar todo
- Cabeceras de seguridad
- Formato Problem Details (RFC 9457)
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import secrets
import tempfile
import os
import importlib


@pytest_asyncio.fixture
async def client():
    """Cliente HTTP de prueba con base de datos temporal."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Configurar entorno
    os.environ["DATABASE_PATH"] = db_path
    os.environ["REDIS_URL"] = ""
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DEMO_ENABLED"] = "false"
    # Rate limit alto para tests
    os.environ["RATE_LIMIT_REQUESTS"] = "1000"
    os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"

    import importlib
    from mate_api import database, config, main, security
    importlib.reload(config)
    importlib.reload(database)
    importlib.reload(security)
    importlib.reload(main)
    await database.init_db()
    # Resetear stores de seguridad
    await security.reset_stores()

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup
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
    return resp.cookies.get("mate_sid")


@pytest_asyncio.fixture
async def session_b(client: AsyncClient):
    """Crea sesión B y devuelve cookie."""
    # Crear nueva sesión en cliente nuevo para aislar cookies
    transport = ASGITransport(app=client._transport.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/session")
        assert resp.status_code == 200
        return resp.cookies.get("mate_sid")


# ──────────────────────────────────────────────────────────────────────
# Tests de Aislamiento BOLA a nivel HTTP
# ──────────────────────────────────────────────────────────────────────

class TestBolaHttp:
    """Verifica que la Sesión A no puede acceder a datos de la Sesión B vía HTTP."""

    async def test_complete_task_cross_session_returns_404(self, client, session_a, session_b):
        # Crear tarea en sesión B
        resp_b = await client.post(
            "/api/v1/chat",
            json={"message": "Crear tarea secreta de B"},
            cookies={"mate_sid": session_b},
        )
        assert resp_b.status_code == 200

        # Obtener ID de la tarea creada en sesión B
        state_b = await client.get("/api/v1/session", cookies={"mate_sid": session_b})
        tasks_b = state_b.json().get("tasks", [])
        assert len(tasks_b) == 1
        task_id_b = tasks_b[0]["id"]

        # Intentar completar desde sesión A
        resp_a = await client.post(
            f"/api/v1/items/task/{task_id_b}/complete",
            cookies={"mate_sid": session_a},
        )
        assert resp_a.status_code == 404
        error = resp_a.json()
        assert error["title"] == "Not Found" or "no pertenece" in error.get("detail", "").lower()

    async def test_complete_habit_cross_session_returns_404(self, client, session_a, session_b):
        # Crear hábito en sesión B
        resp_b = await client.post(
            "/api/v1/chat",
            json={"message": "Crear hábito gym 3 veces"},
            cookies={"mate_sid": session_b},
        )
        assert resp_b.status_code == 200

        state_b = await client.get("/api/v1/session", cookies={"mate_sid": session_b})
        habits_b = state_b.json().get("habits", [])
        assert len(habits_b) == 1
        habit_id_b = habits_b[0]["id"]

        # Intentar completar desde sesión A
        resp_a = await client.post(
            f"/api/v1/items/habit/{habit_id_b}/complete",
            cookies={"mate_sid": session_a},
        )
        assert resp_a.status_code == 404

    async def test_session_a_cannot_see_session_b_data(self, client, session_a, session_b):
        # Crear datos en sesión B
        await client.post("/api/v1/chat", json={"message": "Tarea de B"}, cookies={"mate_sid": session_b})

        # Verificar que A no ve nada
        state_a = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        assert len(state_a.json().get("tasks", [])) == 0


# ──────────────────────────────────────────────────────────────────────
# Tests de CORS
# ──────────────────────────────────────────────────────────────────────

class TestCors:
    """Verifica que CORS está cerrado por defecto."""

    async def test_cors_blocked_origin(self, client):
        """Origen externo no debe recibir Access-Control-Allow-Origin."""
        resp = await client.options(
            "/api/v1/session",
            headers={"Origin": "https://evil.com", "Access-Control-Request-Method": "GET"},
        )
        # No debe tener header ACAO para origen no permitido
        assert "access-control-allow-origin" not in resp.headers or resp.headers.get("access-control-allow-origin") != "https://evil.com"

    async def test_cors_allowed_origin(self, client):
        """Origen permitido debe funcionar."""
        resp = await client.options(
            "/api/v1/session",
            headers={"Origin": "http://localhost:5003", "Access-Control-Request-Method": "GET"},
        )
        # Debe permitir origen configurado
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5003" or resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# Tests de Rate Limiting
# ──────────────────────────────────────────────────────────────────────

class TestRateLimiting:
    """Verifica que el rate limiting funciona (30 req/min por IP)."""

    async def test_rate_limit_allows_within_limit(self, client):
        """Peticiones dentro del límite deben pasar."""
        for _ in range(5):
            resp = await client.get("/api/v1/session")
            assert resp.status_code == 200

    async def test_rate_limit_blocks_over_limit(self, client):
        """Exceder el límite debe devolver 429."""
        # Hacer 1005 peticiones rápidas (límite configurado en 1000 para tests)
        blocked = False
        for i in range(1005):
            resp = await client.get("/api/v1/session")
            if resp.status_code == 429:
                blocked = True
                break
        assert blocked, "Rate limiting no bloqueó después de 1000 requests"

    async def test_rate_limit_returns_problem_details(self, client):
        """429 debe usar formato Problem Details (RFC 9457)."""
        # Agotar límite
        for _ in range(1005):
            resp = await client.get("/api/v1/session")
            if resp.status_code == 429:
                error = resp.json()
                assert error["type"] == "about:blank"
                assert error["title"] == "Too Many Requests"
                assert error["status"] == 429
                assert "detail" in error
                assert "instance" in error
                assert "retry-after" in resp.headers
                return
        pytest.fail("No se alcanzó rate limit")


# ──────────────────────────────────────────────────────────────────────
# Tests de Calendario .ics y Revocación
# ──────────────────────────────────────────────────────────────────────

class TestIcsCalendar:
    """Tests de suscripción y revocación de calendario .ics."""

    async def test_get_calendar_returns_ics(self, client, session_a):
        """GET /calendar/{token}.ics devuelve text/calendar."""
        state = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        ics_url = state.json().get("ics_url")
        assert ics_url

        # Extraer token
        token = ics_url.split("/")[-1].replace(".ics", "")

        resp = await client.get(f"/api/v1/calendar/{token}.ics")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/calendar; charset=utf-8"
        assert "BEGIN:VCALENDAR" in resp.text
        assert "PRODID:-//Mate Routine Agent//ES" in resp.text

    async def test_revoke_token_invalidates_old(self, client, session_a):
        """POST /calendar/revoke invalida token anterior."""
        state = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        old_ics_url = state.json().get("ics_url")
        old_token = old_ics_url.split("/")[-1].replace(".ics", "")

        # Revocar
        resp = await client.post("/api/v1/calendar/revoke", cookies={"mate_sid": session_a})
        assert resp.status_code == 200
        new_token = resp.json()["new_ics_token"]
        new_url = resp.json()["new_ics_url"]
        assert new_token != old_token

        # Token viejo debe dar 404
        resp_old = await client.get(f"/api/v1/calendar/{old_token}.ics")
        assert resp_old.status_code == 404

        # Token nuevo debe funcionar
        resp_new = await client.get(f"/api/v1/calendar/{new_token}.ics")
        assert resp_new.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# Tests de Borrar Todo (DELETE /session)
# ──────────────────────────────────────────────────────────────────────

class TestDeleteSession:
    """Tests del botón 'Borrar todo'."""

    async def test_delete_session_removes_all_data(self, client, session_a):
        """DELETE /api/v1/session borra todo y crea sesión nueva."""
        # Crear algunos datos (usando keywords que el fallback detecta)
        await client.post("/api/v1/chat", json={"message": "Crear tarea para borrar"}, cookies={"mate_sid": session_a})
        await client.post("/api/v1/chat", json={"message": "Crear hábito gym 3 veces"}, cookies={"mate_sid": session_a})

        state_before = await client.get("/api/v1/session", cookies={"mate_sid": session_a})
        assert len(state_before.json().get("tasks", [])) > 0

        # Borrar todo
        resp = await client.delete("/api/v1/session", cookies={"mate_sid": session_a})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Nueva cookie debe estar presente
        new_session_id = resp.cookies.get("mate_sid")
        assert new_session_id
        assert new_session_id != session_a

        # Estado debe estar vacío
        state_after = await client.get("/api/v1/session", cookies={"mate_sid": new_session_id})
        assert len(state_after.json().get("tasks", [])) == 0
        assert len(state_after.json().get("habits", [])) == 0


# ──────────────────────────────────────────────────────────────────────
# Tests de Cabeceras de Seguridad
# ──────────────────────────────────────────────────────────────────────

class TestSecurityHeaders:
    """Verifica cabeceras de seguridad en respuestas."""

    async def test_csp_header(self, client):
        resp = await client.get("/api/v1/session")
        csp = resp.headers.get("content-security-policy")
        assert csp
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    async def test_nosniff_header(self, client):
        resp = await client.get("/api/v1/session")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    async def test_referrer_policy(self, client):
        resp = await client.get("/api/v1/session")
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    async def test_permissions_policy(self, client):
        resp = await client.get("/api/v1/session")
        pp = resp.headers.get("permissions-policy")
        assert pp
        assert "camera=()" in pp
        assert "microphone=()" in pp

    async def test_x_frame_options(self, client):
        resp = await client.get("/api/v1/session")
        assert resp.headers.get("x-frame-options") == "DENY"


# ──────────────────────────────────────────────────────────────────────
# Tests de Health Checks
# ──────────────────────────────────────────────────────────────────────

class TestHealthChecks:
    """Tests de sondas de salud."""

    async def test_liveness(self, client):
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"

    async def test_readiness(self, client):
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"


# ──────────────────────────────────────────────────────────────────────
# Tests de Problem Details (RFC 9457)
# ──────────────────────────────────────────────────────────────────────

class TestProblemDetails:
    """Verifica formato Problem Details en errores."""

    async def test_404_returns_problem_details(self, client, session_a):
        resp = await client.post("/api/v1/items/task/99999/complete", cookies={"mate_sid": session_a})
        assert resp.status_code == 404
        error = resp.json()
        assert "type" in error
        assert "title" in error
        assert "status" in error
        assert "detail" in error
        assert "instance" in error
        assert error["status"] == 404

    async def test_400_returns_problem_details(self, client, session_a):
        resp = await client.post("/api/v1/items/invalid/1/complete", cookies={"mate_sid": session_a})
        assert resp.status_code == 400
        error = resp.json()
        assert error["status"] == 400