import pytest
import pytest_asyncio

from mate_api.database import (
    create_task,
    list_tasks_today,
    complete_task,
    get_task,
    create_habit,
    list_habits,
    complete_habit,
    get_habit,
    create_calendar_block,
    list_blocks_today,
    get_block,
    create_memory,
    list_memories,
    delete_session,
)
from mate_api.models import Category


class TestBolaIsolation:
    """Pruebas de aislamiento estricto por session_id (OWASP API1 - BOLA)."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_sessions(self, session_a, session_b):
        self.session_a = session_a
        self.session_b = session_b

    # ── Tasks ──

    async def test_task_created_in_session_a_not_visible_in_session_b(self):
        await create_task(self.session_a, "Tarea secreta de A", "10:00")
        tasks_a = await list_tasks_today(self.session_a)
        tasks_b = await list_tasks_today(self.session_b)
        assert len(tasks_a) == 1
        assert len(tasks_b) == 0

    async def test_complete_task_from_session_b_fails_with_404_logic(self):
        task = await create_task(self.session_a, "Tarea de A", "10:00")
        # Sesión B intenta completar la tarea de A usando su ID numérico
        result = await complete_task(self.session_b, task.id)
        assert result is False  # 0 filas afectadas → 404 en API

        # Verificar que la tarea de A sigue sin completar
        task_a = await get_task(self.session_a, task.id)
        assert task_a.completed is False

    async def test_get_task_cross_session_returns_none(self):
        task = await create_task(self.session_a, "Tarea de A")
        task_b = await get_task(self.session_b, task.id)
        assert task_b is None

    # ── Habits ──

    async def test_habit_created_in_session_a_not_visible_in_session_b(self):
        await create_habit(self.session_a, "Gym A", 3, "19:00")
        habits_a = await list_habits(self.session_a)
        habits_b = await list_habits(self.session_b)
        assert len(habits_a) == 1
        assert len(habits_b) == 0

    async def test_complete_habit_cross_session_fails(self):
        habit = await create_habit(self.session_a, "Gym A", 3, "19:00")
        result = await complete_habit(self.session_b, habit.id)
        assert result is False

        habit_a = await get_habit(self.session_a, habit.id)
        assert habit_a.done_this_week == 0

    async def test_get_habit_cross_session_returns_none(self):
        habit = await create_habit(self.session_a, "Gym A")
        habit_b = await get_habit(self.session_b, habit.id)
        assert habit_b is None

    # ── Calendar Blocks ──

    async def test_block_created_in_session_a_not_visible_in_session_b(self):
        await create_calendar_block(
            self.session_a, "Estudio A", "2026-11-14T16:00:00+00:00", "2026-11-14T17:00:00+00:00"
        )
        blocks_a = await list_blocks_today(self.session_a)
        blocks_b = await list_blocks_today(self.session_b)
        assert len(blocks_a) == 1
        assert len(blocks_b) == 0

    async def test_get_block_cross_session_returns_none(self):
        block = await create_calendar_block(
            self.session_a, "Estudio A", "2026-11-14T16:00:00+00:00", "2026-11-14T17:00:00+00:00"
        )
        block_b = await get_block(self.session_b, block.id)
        assert block_b is None

    # ── Memories ──

    async def test_memory_created_in_session_a_not_visible_in_session_b(self):
        await create_memory(self.session_a, "clave_a", "valor secreto de A")
        memories_a = await list_memories(self.session_a)
        memories_b = await list_memories(self.session_b)
        assert len(memories_a) == 1
        assert len(memories_b) == 0

    # ── Delete Session (Cascada) ──

    async def test_delete_session_removes_all_data_cascade(self):
        await create_task(self.session_a, "Tarea", "10:00")
        await create_habit(self.session_a, "Hábito", 3, "19:00")
        await create_calendar_block(
            self.session_a, "Bloque", "2026-11-14T16:00:00+00:00", "2026-11-14T17:00:00+00:00"
        )
        await create_memory(self.session_a, "clave", "valor")

        result = await delete_session(self.session_a)
        assert result is True

        # Verificar que todo se borró
        assert await list_tasks_today(self.session_a) == []
        assert await list_habits(self.session_a) == []
        assert await list_blocks_today(self.session_a) == []
        assert await list_memories(self.session_a) == []

    # ── Sesiones independientes no se afectan mutuamente ──

    async def test_sessions_are_independent(self):
        await create_task(self.session_a, "Tarea A")
        await create_task(self.session_b, "Tarea B")
        await create_task(self.session_b, "Tarea B 2")

        assert len(await list_tasks_today(self.session_a)) == 1
        assert len(await list_tasks_today(self.session_b)) == 2

        # Borrar A no afecta a B
        await delete_session(self.session_a)
        assert len(await list_tasks_today(self.session_b)) == 2