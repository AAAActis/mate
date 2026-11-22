import aiosqlite
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

from .config import get_settings
from .models import (
    TaskRead,
    HabitRead,
    CalendarBlockRead,
    MemoryRead,
    SessionRead,
    Tone,
    Category,
)
from datetime import datetime, timezone
from .security import PiiRedactor


# ──────────────────────────────────────────────────────────────────────
# Conexión y PRAGMAs
# ──────────────────────────────────────────────────────────────────────

_DB_PATH: Optional[Path] = None


def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        settings = get_settings()
        _DB_PATH = Path(settings.DATABASE_PATH)
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


@asynccontextmanager
async def get_connection():
    db_path = get_db_path()
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    # PRAGMAs para rendimiento y consistencia
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def db_transaction():
    async with get_connection() as conn:
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


# ──────────────────────────────────────────────────────────────────────
# Inicialización de esquemas
# ──────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    ics_token TEXT NOT NULL UNIQUE,
    tone TEXT NOT NULL DEFAULT 'amable',
    created_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL DEFAULT '',
    display_name TEXT NULL,
    messages_count_today INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,              -- ya pasado por PiiRedactor, máx. 4000 caracteres
    tools_json TEXT,                   -- lista de herramientas ejecutadas en ese turno (solo assistant)
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    due_time TEXT,
    due_date TEXT,
    category TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    target_per_week INTEGER NOT NULL DEFAULT 3,
    done_this_week INTEGER NOT NULL DEFAULT 0,
    preferred_time TEXT DEFAULT '19:00',
    last_checkin_date TEXT,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS calendar_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'estudio',
    completed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    event_date TEXT,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_habits_session ON habits(session_id);
CREATE INDEX IF NOT EXISTS idx_blocks_session ON calendar_blocks(session_id, start_utc);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
"""


async def init_db() -> None:
    async with db_transaction() as conn:
        await conn.executescript(SCHEMA_SQL)
        # Idempotent migrations for new columns - check if columns exist first
        cursor = await conn.execute("PRAGMA table_info(tasks)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "due_date" not in columns:
            await conn.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
        if "category" not in columns:
            await conn.execute("ALTER TABLE tasks ADD COLUMN category TEXT")

        # Add completed column to calendar_blocks if not exists
        cursor = await conn.execute("PRAGMA table_info(calendar_blocks)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "completed" not in columns:
            await conn.execute("ALTER TABLE calendar_blocks ADD COLUMN completed INTEGER NOT NULL DEFAULT 0")


# ──────────────────────────────────────────────────────────────────────
# Helpers de tiempo
# ──────────────────────────────────────────────────────────────────────

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


def session_expires_at(hours: int = 720) -> str:
    """Duración por defecto: 720h = 30 días."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


# ──────────────────────────────────────────────────────────────────────
# Sesiones (creación, obtención, borrado, rotación ics_token)
# ──────────────────────────────────────────────────────────────────────

async def create_session(session_id: str, ics_token: str, tone: Tone = Tone.AMABLE) -> SessionRead:
    now = utc_now_iso()
    expires = session_expires_at(get_settings().SESSION_TTL_HOURS)
    async with db_transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (session_id, ics_token, tone, created_at_utc, expires_at_utc, last_seen_utc, display_name, messages_count_today)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, ics_token, tone.value, now, expires, now, None, 0),
        )
    return SessionRead(
        session_id=session_id,
        ics_token=ics_token,
        tone=tone,
        created_at_utc=parse_utc(now),
        expires_at_utc=parse_utc(expires),
        last_seen_utc=parse_utc(now),
        display_name=None,
        messages_count_today=0,
    )


async def get_session(session_id: str) -> Optional[SessionRead]:
    async with get_connection() as conn:
        row = await conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await row.fetchone()
        if not row:
            return None
        return SessionRead(
            session_id=row["session_id"],
            ics_token=row["ics_token"],
            tone=Tone(row["tone"]),
            created_at_utc=parse_utc(row["created_at_utc"]),
            expires_at_utc=parse_utc(row["expires_at_utc"]),
            last_seen_utc=parse_utc(row["last_seen_utc"]) if row["last_seen_utc"] else None,
            display_name=row["display_name"],
            messages_count_today=row["messages_count_today"],
        )


async def update_session_tone(session_id: str, tone: Tone) -> bool:
    async with db_transaction() as conn:
        cur = await conn.execute(
            "UPDATE sessions SET tone = ? WHERE session_id = ?", (tone.value, session_id)
        )
        return cur.rowcount > 0


async def rotate_ics_token(session_id: str, new_ics_token: str) -> bool:
    async with db_transaction() as conn:
        cur = await conn.execute(
            "UPDATE sessions SET ics_token = ? WHERE session_id = ?", (new_ics_token, session_id)
        )
        return cur.rowcount > 0


async def delete_session(session_id: str) -> bool:
    """Borra la sesión y TODO en cascada (tasks, habits, blocks, memories)."""
    async with db_transaction() as conn:
        cur = await conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        return cur.rowcount > 0


async def purge_expired_sessions() -> int:
    """Elimina sesiones vencidas. Lo llama scheduler.py cada 5 min."""
    now = utc_now_iso()
    async with db_transaction() as conn:
        cur = await conn.execute(
            "DELETE FROM sessions WHERE expires_at_utc < ?", (now,)
        )
        return cur.rowcount


async def update_session_last_seen(session_id: str) -> bool:
    """Actualiza last_seen_utc en la sesión (para lógica de saludo)."""
    now = utc_now_iso()
    async with db_transaction() as conn:
        cur = await conn.execute(
            "UPDATE sessions SET last_seen_utc = ? WHERE session_id = ?",
            (now, session_id),
        )
        return cur.rowcount > 0


async def update_session_expires_at(session_id: str) -> bool:
    """Renueva expires_at_utc a ahora + TTL (rolling expiration).
    Límite: como máximo una escritura por hora por sesión."""
    now = utc_now_iso()
    async with db_transaction() as conn:
        cur = await conn.execute(
            "UPDATE sessions SET expires_at_utc = ? WHERE session_id = ?",
            (now, session_id),
        )
        return cur.rowcount > 0


async def get_session_by_ics_token(ics_token: str) -> Optional[SessionRead]:
    async with get_connection() as conn:
        row = await conn.execute(
            "SELECT * FROM sessions WHERE ics_token = ?", (ics_token,)
        )
        row = await row.fetchone()
        if not row:
            return None
        return SessionRead(
            session_id=row["session_id"],
            ics_token=row["ics_token"],
            tone=Tone(row["tone"]),
            created_at_utc=parse_utc(row["created_at_utc"]),
            expires_at_utc=parse_utc(row["expires_at_utc"]),
            last_seen_utc=parse_utc(row["last_seen_utc"]) if row["last_seen_utc"] else None,
            display_name=row["display_name"],
            messages_count_today=row["messages_count_today"],
        )


# ──────────────────────────────────────────────────────────────────────
# Tasks — TODAS las queries filtran por session_id (BOLA prevention)
# ──────────────────────────────────────────────────────────────────────

async def create_task(session_id: str, title: str, due_time: Optional[str] = None, due_date: Optional[str] = None, category: Optional[str] = None) -> TaskRead:
    now = utc_now_iso()
    async with db_transaction() as conn:
        cur = await conn.execute(
            """
            INSERT INTO tasks (session_id, title, due_time, due_date, category, completed, created_at_utc)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (session_id, title, due_time, due_date, category, now),
        )
        task_id = cur.lastrowid
    return TaskRead(
        id=task_id,
        session_id=session_id,
        title=title,
        due_time=due_time,
        due_date=due_date,
        category=Category(category) if category else None,
        completed=False,
        created_at_utc=parse_utc(now),
    )


async def list_tasks_today(session_id: str, args: dict = None) -> list[TaskRead]:
    today_iso = (args or {}).get("today_iso") or datetime.now(CORDOBA_TZ).strftime("%Y-%m-%d")
    async with get_connection() as conn:
        rows = await conn.execute(
            """
            SELECT * FROM tasks
            WHERE session_id = ? AND (due_date IS NULL OR due_date = ?)
            ORDER BY due_time IS NULL, due_time
            """,
            (session_id, today_iso),
        )
        rows = await rows.fetchall()
    return [
        TaskRead(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            due_time=row["due_time"],
            due_date=row["due_date"],
            category=Category(row["category"]) if row["category"] else None,
            completed=bool(row["completed"]),
            created_at_utc=parse_utc(row["created_at_utc"]),
        )
        for row in rows
    ]


async def complete_task(session_id: str, task_id: int) -> bool:
    async with db_transaction() as conn:
        cur = await conn.execute(
            "UPDATE tasks SET completed = 1 WHERE id = ? AND session_id = ?", (task_id, session_id)
        )
        return cur.rowcount > 0


async def get_task(session_id: str, task_id: int) -> Optional[TaskRead]:
    async with get_connection() as conn:
        row = await conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND session_id = ?", (task_id, session_id)
        )
        row = await row.fetchone()
        if not row:
            return None
        return TaskRead(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            due_time=row["due_time"],
            due_date=row["due_date"],
            category=Category(row["category"]) if row["category"] else None,
            completed=bool(row["completed"]),
            created_at_utc=parse_utc(row["created_at_utc"]),
        )


# ──────────────────────────────────────────────────────────────────────
# Habits
# ──────────────────────────────────────────────────────────────────────

async def create_habit(
    session_id: str,
    name: str,
    target_per_week: int = 3,
    preferred_time: str = "19:00",
) -> HabitRead:
    now = utc_now_iso()
    async with db_transaction() as conn:
        cur = await conn.execute(
            """
            INSERT INTO habits (session_id, name, target_per_week, done_this_week, preferred_time, created_at_utc)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (session_id, name, target_per_week, preferred_time, now),
        )
        habit_id = cur.lastrowid
    return HabitRead(
        id=habit_id,
        session_id=session_id,
        name=name,
        target_per_week=target_per_week,
        done_this_week=0,
        preferred_time=preferred_time,
        last_checkin_date=None,
        created_at_utc=parse_utc(now),
    )


async def list_habits(session_id: str, args: dict = None) -> list[HabitRead]:
    today_iso = (args or {}).get("today_iso") or datetime.now(CORDOBA_TZ).strftime("%Y-%m-%d")
    async with get_connection() as conn:
        rows = await conn.execute(
            "SELECT * FROM habits WHERE session_id = ? ORDER BY created_at_utc", (session_id,)
        )
        rows = await rows.fetchall()
    return [
        HabitRead(
            id=row["id"],
            session_id=row["session_id"],
            name=row["name"],
            target_per_week=row["target_per_week"],
            done_this_week=row["done_this_week"],
            preferred_time=row["preferred_time"],
            last_checkin_date=row["last_checkin_date"],
            created_at_utc=parse_utc(row["created_at_utc"]),
        )
        for row in rows
    ]


async def complete_habit(session_id: str, habit_id: int) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    async with db_transaction() as conn:
        # Verificar que no se haya completado hoy ya
        row = await conn.execute(
            "SELECT last_checkin_date FROM habits WHERE id = ? AND session_id = ?", (habit_id, session_id)
        )
        row = await row.fetchone()
        if not row:
            return False
        if row["last_checkin_date"] == today:
            return False  # Ya completado hoy
        cur = await conn.execute(
            """
            UPDATE habits
            SET done_this_week = done_this_week + 1, last_checkin_date = ?
            WHERE id = ? AND session_id = ?
            """,
            (today, habit_id, session_id),
        )
        return cur.rowcount > 0


async def get_habit(session_id: str, habit_id: int) -> Optional[HabitRead]:
    async with get_connection() as conn:
        row = await conn.execute(
            "SELECT * FROM habits WHERE id = ? AND session_id = ?", (habit_id, session_id)
        )
        row = await row.fetchone()
        if not row:
            return None
        return HabitRead(
            id=row["id"],
            session_id=row["session_id"],
            name=row["name"],
            target_per_week=row["target_per_week"],
            done_this_week=row["done_this_week"],
            preferred_time=row["preferred_time"],
            last_checkin_date=row["last_checkin_date"],
            created_at_utc=parse_utc(row["created_at_utc"]),
        )


# ──────────────────────────────────────────────────────────────────────
# Calendar Blocks
# ──────────────────────────────────────────────────────────────────────

async def create_calendar_block(
    session_id: str,
    title: str,
    start_utc: str,
    end_utc: str,
    category: Category = Category.ESTUDIO,
) -> CalendarBlockRead:
    async with db_transaction() as conn:
        # Verificar solapamiento
        row = await conn.execute(
            """
            SELECT 1 FROM calendar_blocks
            WHERE session_id = ?
              AND start_utc < ? AND end_utc > ?
            LIMIT 1
            """,
            (session_id, end_utc, start_utc),
        )
        if await row.fetchone():
            raise ValueError("El bloque se solapa con uno existente")
        cur = await conn.execute(
            """
            INSERT INTO calendar_blocks (session_id, title, start_utc, end_utc, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, title, start_utc, end_utc, category.value),
        )
        block_id = cur.lastrowid
    return CalendarBlockRead(
        id=block_id,
        session_id=session_id,
        title=title,
        start_utc=parse_utc(start_utc),
        end_utc=parse_utc(end_utc),
        category=category,
    )


async def list_blocks_today(session_id: str, args: dict = None) -> list[CalendarBlockRead]:
    today_iso = (args or {}).get("today_iso") or datetime.now(CORDOBA_TZ).strftime("%Y-%m-%d")
    today_start = datetime.strptime(today_iso, "%Y-%m-%d").replace(
        tzinfo=CORDOBA_TZ
    ).astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    tomorrow_start = (
        datetime.strptime(today_iso, "%Y-%m-%d")
        .replace(tzinfo=CORDOBA_TZ)
        .astimezone(timezone.utc)
        + timedelta(days=1)
    ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    async with get_connection() as conn:
        rows = await conn.execute(
            """
            SELECT * FROM calendar_blocks
            WHERE session_id = ? AND start_utc >= ? AND start_utc < ?
            ORDER BY start_utc
            """,
            (session_id, today_start, tomorrow_start),
        )
        rows = await rows.fetchall()
    return [
        CalendarBlockRead(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            start_utc=parse_utc(row["start_utc"]),
            end_utc=parse_utc(row["end_utc"]),
            category=Category(row["category"]),
        )
        for row in rows
    ]


async def list_upcoming_blocks(session_id: str, limit: int = 10) -> list[CalendarBlockRead]:
    now = utc_now_iso()
    async with get_connection() as conn:
        rows = await conn.execute(
            """
            SELECT * FROM calendar_blocks
            WHERE session_id = ? AND start_utc >= ?
            ORDER BY start_utc
            LIMIT ?
            """,
            (session_id, now, limit),
        )
        rows = await rows.fetchall()
    return [
        CalendarBlockRead(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            start_utc=parse_utc(row["start_utc"]),
            end_utc=parse_utc(row["end_utc"]),
            category=Category(row["category"]),
        )
        for row in rows
    ]


CORDOBA_TZ = ZoneInfo("America/Argentina/Cordoba")

async def find_free_slots(
    session_id: str, duration_minutes: int = 60, date_iso: Optional[str] = None
) -> list[tuple[str, str]]:
    """Devuelve hasta 3 franjas libres entre 08:00 y 22:00 en hora de Córdoba,
    guardando todo en UTC y devuelve ISO con offset. Si es medianoche/mañana,
    el día se entiende en hora de Córdoba."""
    if date_iso is None:
        # Usar hoy en hora Córdoba, luego formatear a UTC
        now_cordoba = datetime.now(CORDOBA_TZ)
        date_iso = now_cordoba.strftime("%Y-%m-%d")

    # Construir los límites del día en hora Córdoba, luego a UTC
    cordoba_midnight = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=CORDOBA_TZ)
    day_start_utc = cordoba_midnight.astimezone(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()
    day_end_utc = cordoba_midnight.astimezone(timezone.utc).replace(hour=22, minute=0, second=0, microsecond=0).isoformat()

    async with get_connection() as conn:
        rows = await conn.execute(
            """
            SELECT start_utc, end_utc FROM calendar_blocks
            WHERE session_id = ? AND start_utc < ? AND end_utc > ?
            ORDER BY start_utc
            """,
            (session_id, day_end_utc, day_start_utc),
        )
        rows = await rows.fetchall()

    blocks = [(parse_utc(r["start_utc"]), parse_utc(r["end_utc"])) for r in rows]
    slots: list[tuple[str, str]] = []
    current = parse_utc(day_start_utc)
    duration = timedelta(minutes=duration_minutes)

    for start, end in blocks:
        if current + duration <= start:
            slots.append((current.isoformat(), (current + duration).isoformat()))
            if len(slots) >= 3:
                return slots
        current = max(current, end)

    if current + duration <= parse_utc(day_end_utc):
        slots.append((current.isoformat(), (current + duration).isoformat()))

    return slots[:3]


async def get_block(session_id: str, block_id: int) -> Optional[CalendarBlockRead]:
    async with get_connection() as conn:
        row = await conn.execute(
            "SELECT * FROM calendar_blocks WHERE id = ? AND session_id = ?", (block_id, session_id)
        )
        row = await row.fetchone()
        if not row:
            return None
        return CalendarBlockRead(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            start_utc=parse_utc(row["start_utc"]),
            end_utc=parse_utc(row["end_utc"]),
            category=Category(row["category"]),
        )


async def complete_block(session_id: str, block_id: int) -> bool:
    """Marca un bloque de calendario (o recordatorio) como completado."""
    async with db_transaction() as conn:
        cur = await conn.execute(
            "UPDATE calendar_blocks SET completed = 1 WHERE id = ? AND session_id = ?", (block_id, session_id)
        )
        return cur.rowcount > 0


async def delete_block(session_id: str, block_id: int) -> bool:
    """Elimina un bloque de calendario (o recordatorio) - BOLA protected."""
    async with db_transaction() as conn:
        cur = await conn.execute(
            "DELETE FROM calendar_blocks WHERE id = ? AND session_id = ?", (block_id, session_id)
        )
        return cur.rowcount > 0


async def get_block_with_completed(session_id: str, block_id: int) -> Optional[CalendarBlockRead]:
    """Obtiene un bloque incluyendo el campo completed."""
    async with get_connection() as conn:
        row = await conn.execute(
            "SELECT * FROM calendar_blocks WHERE id = ? AND session_id = ?", (block_id, session_id)
        )
        row = await row.fetchone()
        if not row:
            return None
        return CalendarBlockRead(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            start_utc=parse_utc(row["start_utc"]),
            end_utc=parse_utc(row["end_utc"]),
            category=Category(row["category"]),
            completed=bool(row["completed"]),
        )


# ──────────────────────────────────────────────────────────────────────
# Memories — Tope de 30 por sesión (FIFO)
# ──────────────────────────────────────────────────────────────────────

MAX_MEMORIES = 30


async def create_memory(
    session_id: str, fact_key: str, fact_value: str, event_date: Optional[str] = None
) -> MemoryRead:
    # Redactar PII antes de guardar
    clean_value = PiiRedactor.redact(fact_value)
    now = utc_now_iso()
    async with db_transaction() as conn:
        # Si ya hay 30, borrar la más antigua
        await conn.execute(
            """
            DELETE FROM memories
            WHERE id = (
                SELECT id FROM memories WHERE session_id = ? ORDER BY created_at_utc LIMIT 1
            ) AND (SELECT COUNT(*) FROM memories WHERE session_id = ?) >= ?
            """,
            (session_id, session_id, MAX_MEMORIES),
        )
        cur = await conn.execute(
            """
            INSERT INTO memories (session_id, fact_key, fact_value, event_date, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, fact_key, clean_value, event_date, now),
        )
        memory_id = cur.lastrowid
    return MemoryRead(
        id=memory_id,
        session_id=session_id,
        fact_key=fact_key,
        fact_value=clean_value,
        event_date=event_date,
        created_at_utc=parse_utc(now),
    )


async def list_memories(session_id: str) -> list[MemoryRead]:
    async with get_connection() as conn:
        rows = await conn.execute(
            "SELECT * FROM memories WHERE session_id = ? ORDER BY created_at_utc DESC", (session_id,)
        )
        rows = await rows.fetchall()
    return [
        MemoryRead(
            id=row["id"],
            session_id=row["session_id"],
            fact_key=row["fact_key"],
            fact_value=row["fact_value"],
            event_date=row["event_date"],
            created_at_utc=parse_utc(row["created_at_utc"]),
        )
        for row in rows
    ]


async def get_upcoming_memory_events(session_id: str, days_ahead: int = 7) -> list[MemoryRead]:
    """Memorias con event_date dentro de los próximos N días."""
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with get_connection() as conn:
        rows = await conn.execute(
            """
            SELECT * FROM memories
            WHERE session_id = ? AND event_date IS NOT NULL
              AND event_date >= ? AND event_date <= ?
            ORDER BY event_date
            """,
            (session_id, today, cutoff),
        )
        rows = await rows.fetchall()
    return [
        MemoryRead(
            id=row["id"],
            session_id=row["session_id"],
            fact_key=row["fact_key"],
            fact_value=row["fact_value"],
            event_date=row["event_date"],
            created_at_utc=parse_utc(row["created_at_utc"]),
        )
        for row in rows
    ]


# ──────────────────────────────────────────────────────────────────────
# Snapshot completo del estado de la sesión (para /api/v1/session)
# ──────────────────────────────────────────────────────────────────────

async def get_full_session_state(session_id: str) -> dict:
    session = await get_session(session_id)
    if not session:
        return {}
    tasks = await list_tasks_today(session_id)
    habits = await list_habits(session_id)
    blocks = await list_blocks_today(session_id)
    memories = await list_memories(session_id)
    settings = get_settings()
    return {
        "session": session,
        "tasks": tasks,
        "habits": habits,
        "blocks": blocks,
        "memories": memories,
        "tone": session.tone,
        "ics_url": f"/api/v1/calendar/{session.ics_token}.ics",
    }


async def list_active_sessions(conn) -> list[SessionRead]:
    """Lista todas las sesiones activas (no vencidas)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = await conn.execute(
        "SELECT * FROM sessions WHERE expires_at_utc >= ? ORDER BY created_at_utc",
        (now,),
    )
    rows = await rows.fetchall()
    return [
        SessionRead(
            session_id=row["session_id"],
            ics_token=row["ics_token"],
            tone=Tone(row["tone"]),
            created_at_utc=parse_utc(row["created_at_utc"]),
            expires_at_utc=parse_utc(row["expires_at_utc"]),
            last_seen_utc=parse_utc(row["last_seen_utc"]) if row["last_seen_utc"] else None,
            display_name=row["display_name"],
            messages_count_today=row["messages_count_today"],
        )
        for row in rows
    ]


# ──────────────────────────────────────────────────────────────────────
# Messages — Historial de chat con PiiRedactor y paginación
# ──────────────────────────────────────────────────────────────────────

MAX_MESSAGES_PER_SESSION = 100


async def create_message(
    session_id: str, role: str, content: str, tools_json: Optional[str] = None
) -> dict:
    """Guarda un mensaje en la tabla messages. El contenido ya debe estar redactado por PiiRedactor."""
    now = utc_now_iso()
    today_str = now[:10]  # YYYY-MM-DD
    # Aplicar PiiRedactor al contenido antes de guardar (defensa en profundidad)
    from .security import PiiRedactor
    clean_content = PiiRedactor.redact(content)
    async with db_transaction() as conn:
        # Resetear counter si es nuevo día (comparar con la última fecha de mensaje)
        last_msg_date = await conn.execute(
            "SELECT created_at_utc FROM messages WHERE session_id = ? ORDER BY created_at_utc DESC LIMIT 1", (session_id,)
        )
        last_msg_row = await last_msg_date.fetchone()
        if last_msg_row:
            last_msg_day = last_msg_row[0][:10]
            if last_msg_day != today_str:
                # Nuevo día: resetear counter
                await conn.execute(
                    "UPDATE sessions SET messages_count_today = 0 WHERE session_id = ?",
                    (session_id,),
                )
        # Verificar tope de 100 mensajes (FIFO): borrar el más viejo si ya hay 100
        count = await conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        )
        count_row = await count.fetchone()
        message_count = count_row[0]
        if message_count >= MAX_MESSAGES_PER_SESSION:
            await conn.execute(
                """
                DELETE FROM messages
                WHERE id = (
                    SELECT id FROM messages WHERE session_id = ?
                    ORDER BY created_at_utc LIMIT 1
                )
                """,
                (session_id,),
            )
        # Verificar cuota de 30 mensajes por día
        msg_count_today = await conn.execute(
            "SELECT messages_count_today FROM sessions WHERE session_id = ?", (session_id,)
        )
        count_row = await msg_count_today.fetchone()
        today_count = count_row[0] if count_row else 0
        if today_count >= 30:
            # Borrar el mensaje más viejo de hoy para hacer lugar
            await conn.execute(
                """
                DELETE FROM messages
                WHERE id = (
                    SELECT id FROM messages WHERE session_id = ?
                    ORDER BY created_at_utc LIMIT 1
                )
                """,
                (session_id,),
            )
            # Mantener el contador en 30 (ya borramos uno)
        else:
            # Incrementar contador diario
            await conn.execute(
                "UPDATE sessions SET messages_count_today = ? WHERE session_id = ?",
                (today_count + 1, session_id),
            )
        cur = await conn.execute(
            """
            INSERT INTO messages (session_id, role, content, tools_json, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, clean_content, tools_json, now),
        )
        message_id = cur.lastrowid
    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": clean_content,
        "tools_json": tools_json,
        "created_at_utc": now,
    }


async def get_messages(
    session_id: str, before_id: Optional[int] = None, limit: int = 30
) -> list[dict]:
    """Obtiene historial paginado de mensajes de la sesión.

    Args:
        session_id: La sesión actual
        before_id: Si se proporciona, solo mensajes con id < before_id (para paginación hacia atrás)
        limit: Máximo número de mensajes a devolver (máx. 30)
    """
    limit = min(limit, 30)  # Cap at 30 per CONTEXTO-BACKEND.md
    async with get_connection() as conn:
        if before_id is not None:
            rows = await conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ? AND id < ?
                ORDER BY created_at_utc DESC
                LIMIT ?
                """,
                (session_id, before_id, limit),
            )
        else:
            rows = await conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY created_at_utc DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
        rows = await rows.fetchall()
    return [
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "role": row["role"],
            "content": row["content"],
            "tools_json": row["tools_json"],
            "created_at_utc": row["created_at_utc"],
        }
        for row in rows
    ]