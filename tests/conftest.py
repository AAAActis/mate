import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

# Configurar variable de entorno ANTES de importar módulos de la app
os.environ["DATABASE_PATH"] = ""
os.environ["REDIS_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["DEMO_ENABLED"] = "false"


@pytest_asyncio.fixture(scope="function")
async def temp_db():
    """Base de datos temporal aislada por test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    os.environ["DATABASE_PATH"] = str(db_path)

    # Reimportar módulos para que usen la nueva DB
    import importlib
    from mate_api import database, config
    importlib.reload(config)
    importlib.reload(database)
    await database.init_db()

    yield db_path

    # Cleanup
    try:
        db_path.unlink()
    except Exception:
        pass


@pytest_asyncio.fixture
async def session_a(temp_db):
    from mate_api.database import create_session
    import secrets
    sid = secrets.token_urlsafe(32)
    ics = secrets.token_urlsafe(32)
    await create_session(sid, ics)
    return sid


@pytest_asyncio.fixture
async def session_b(temp_db):
    from mate_api.database import create_session
    import secrets
    sid = secrets.token_urlsafe(32)
    ics = secrets.token_urlsafe(32)
    await create_session(sid, ics)
    return sid