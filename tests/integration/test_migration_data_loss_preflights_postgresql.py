from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def _postgres_engine() -> sa.Engine:
    raw_url = os.getenv("KLM_TEST_POSTGRES_ADMIN_URL")
    if not raw_url:
        pytest.skip("KLM_TEST_POSTGRES_ADMIN_URL is required")
    url = make_url(raw_url).set(drivername="postgresql+psycopg2")
    return sa.create_engine(url)


def _load_migration_helper(
    revision: str,
    filename: str,
    helper_name: str,
):
    path = ROOT / "alembic/versions" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    required_assignments = {
        "COMMERCIAL_TABLES",
        "PLATFORM_TASK_TABLES",
        "TASK_REMINDER_TABLES",
        "WORKSPACE_COLLABORATION_TABLES",
        "NON_RECONSTRUCTIBLE_V1_TABLES",
    }
    selected_nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name)
                and target.id in required_assignments
                for target in (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
            )
        )
        or (
            isinstance(node, ast.FunctionDef)
            and node.name == helper_name
        )
    ]
    namespace = {
        "sa": sa,
        "op": SimpleNamespace(),
    }
    exec(
        compile(
            ast.Module(body=selected_nodes, type_ignores=[]),
            str(path),
            "exec",
        ),
        namespace,
    )
    return SimpleNamespace(
        revision=revision,
        helper=namespace[helper_name],
        op_namespace=namespace,
        NON_RECONSTRUCTIBLE_V1_TABLES=namespace.get(
            "NON_RECONSTRUCTIBLE_V1_TABLES",
            (),
        ),
    )


def test_c8_postgresql_preflight_refuses_real_non_reconstructible_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_helper(
        "c8e0f2a4b615",
        "c8e0f2a4b615_add_v1_collaboration_and_commercial_control.py",
        "_require_no_non_reconstructible_v1_data_before_downgrade",
    )
    engine = _postgres_engine()
    try:
        with engine.begin() as connection:
            for table_name in migration.NON_RECONSTRUCTIBLE_V1_TABLES:
                connection.execute(
                    sa.text(f'CREATE TEMP TABLE "{table_name}" (id integer)')
                )
            connection.execute(
                sa.text("INSERT INTO commercial_offers (id) VALUES (1)")
            )
            connection.execute(
                sa.text(
                    """
                    CREATE TEMP TABLE matters (
                        id integer PRIMARY KEY,
                        workspace_id integer NOT NULL
                    );
                    CREATE TEMP TABLE workspace_memberships (
                        id integer PRIMARY KEY,
                        workspace_id integer NOT NULL,
                        user_id integer NOT NULL,
                        status text NOT NULL
                    );
                    CREATE TEMP TABLE tasks (
                        id integer PRIMARY KEY,
                        matter_id integer NOT NULL,
                        assigned_to integer,
                        review_required boolean NOT NULL DEFAULT false,
                        review_due_at timestamptz,
                        review_state text NOT NULL DEFAULT 'not_required',
                        review_round integer NOT NULL DEFAULT 0,
                        version integer NOT NULL DEFAULT 1,
                        created_by_user_id integer,
                        cancelled_at timestamptz,
                        cancelled_by_user_id integer,
                        cancellation_reason text
                    );
                    CREATE TEMP TABLE task_participants (
                        id integer PRIMARY KEY,
                        task_id integer NOT NULL,
                        workspace_id integer NOT NULL,
                        matter_id integer NOT NULL,
                        membership_id integer NOT NULL,
                        user_id integer NOT NULL,
                        role text NOT NULL,
                        state text NOT NULL,
                        required_for_completion boolean NOT NULL,
                        due_at timestamptz,
                        active boolean NOT NULL,
                        created_by_user_id integer,
                        deactivated_at timestamptz
                    );
                    CREATE TEMP TABLE meetings (
                        id integer PRIMARY KEY,
                        workspace_id integer NOT NULL,
                        responsible_user_id integer,
                        linked_task_id integer,
                        version integer NOT NULL DEFAULT 1
                    );
                    CREATE TEMP TABLE meeting_participants (
                        id integer PRIMARY KEY,
                        meeting_id integer NOT NULL,
                        workspace_id integer NOT NULL,
                        membership_id integer NOT NULL,
                        user_id integer NOT NULL,
                        role text NOT NULL,
                        rsvp text NOT NULL,
                        reminder_minutes_before integer,
                        active boolean NOT NULL,
                        removed_at timestamptz
                    );
                    CREATE TEMP TABLE storage_objects (
                        id integer PRIMARY KEY,
                        idempotency_key_hash text
                    )
                    """
                )
            )
            monkeypatch.setitem(
                migration.op_namespace,
                "op",
                SimpleNamespace(get_bind=lambda: connection),
            )

            with pytest.raises(RuntimeError, match="refused before any DROP"):
                migration.helper()

            assert connection.scalar(
                sa.text("SELECT COUNT(*) FROM commercial_offers")
            ) == 1
    finally:
        engine.dispose()


def test_e0_postgresql_preflight_preserves_required_owner_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_helper(
        "e0a2b4d6c819",
        "e0a2b4d6c819_allow_task_owner_required_for_completion.py",
        "_require_no_required_owner_before_downgrade",
    )
    engine = _postgres_engine()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    CREATE TEMP TABLE task_participants (
                        id integer PRIMARY KEY,
                        role text NOT NULL,
                        required_for_completion boolean NOT NULL
                    );
                    INSERT INTO task_participants (
                        id,
                        role,
                        required_for_completion
                    ) VALUES (1, 'owner', true)
                    """
                )
            )
            monkeypatch.setitem(
                migration.op_namespace,
                "op",
                SimpleNamespace(get_bind=lambda: connection),
            )

            with pytest.raises(
                RuntimeError,
                match="refused before replacing the check constraint",
            ):
                migration.helper()

            assert connection.scalar(
                sa.text(
                    """
                    SELECT COUNT(*)
                    FROM task_participants
                    WHERE role = 'owner' AND required_for_completion = true
                    """
                )
            ) == 1
    finally:
        engine.dispose()
