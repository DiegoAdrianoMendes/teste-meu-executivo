from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic/versions/uv4p7l9m0h53_add_flexible_task_context.py"


def test_task_context_backfill_temporarily_disables_and_restores_rls() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    disable_tasks = source.index('ALTER TABLE "tasks" DISABLE ROW LEVEL SECURITY')
    disable_matters = source.index('ALTER TABLE "matters" DISABLE ROW LEVEL SECURITY')
    backfill = source.index("UPDATE tasks SET workspace_id")
    validate = source.index("SELECT count(*) FROM tasks WHERE workspace_id IS NULL")
    not_null = source.index('alter_column("workspace_id", existing_type=uuid_type, nullable=False)')
    enable_tasks = source.index("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")
    force_tasks = source.index("ALTER TABLE tasks FORCE ROW LEVEL SECURITY")
    enable_matters = source.index("ALTER TABLE matters ENABLE ROW LEVEL SECURITY")
    force_matters = source.index("ALTER TABLE matters FORCE ROW LEVEL SECURITY")

    assert (
        disable_tasks
        < disable_matters
        < backfill
        < validate
        < not_null
        < enable_tasks
        < force_tasks
        < enable_matters
        < force_matters
    )
