"""add immutable flow revisions and optimistic version pointers

Revision ID: d1e2f3a4b5c6
Revises: 9c0d1e2f3a4b
Create Date: 2026-07-16

This revision intentionally branches beside the OSS lineage that starts at
a0b1c2d3e4f5. Create a no-op merge with the then-current OSS/RLS descendant
(b1c2d3e4f5a6 at authoring time) after the parallel work is integrated.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "9c0d1e2f3a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOT_SCHEMA_VERSION = 1


def _serialized_datetime(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _flow_tables() -> tuple[sa.TableClause, sa.TableClause, sa.TableClause]:
    flows = sa.table(
        "flows",
        sa.column("id", FlexibleUUID()),
        sa.column("workspace_id", FlexibleUUID()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("practice_area", sa.String()),
        sa.column("is_template", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
        sa.column("trigger", sa.String()),
        sa.column("trigger_config", FlexibleJSON()),
        sa.column("nodes", FlexibleJSON()),
        sa.column("edges", FlexibleJSON()),
        sa.column("run_count", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
        sa.column("current_revision", sa.Integer()),
        sa.column("current_revision_id", FlexibleUUID()),
    )
    flow_revisions = sa.table(
        "flow_revisions",
        sa.column("id", FlexibleUUID()),
        sa.column("flow_id", FlexibleUUID()),
        sa.column("revision_number", sa.Integer()),
        sa.column("snapshot_schema_version", sa.Integer()),
        sa.column("snapshot", FlexibleJSON()),
        sa.column("snapshot_hash", sa.String()),
        sa.column("source", sa.String()),
        sa.column("created_by_user_id", FlexibleUUID()),
    )
    flow_runs = sa.table(
        "flow_runs",
        sa.column("id", FlexibleUUID()),
        sa.column("flow_id", FlexibleUUID()),
        sa.column("revision_id", FlexibleUUID()),
    )
    return flows, flow_revisions, flow_runs


def _backfill_revisions() -> None:
    bind = op.get_bind()
    flows, flow_revisions, flow_runs = _flow_tables()
    rows = bind.execute(
        sa.select(
            flows.c.id,
            flows.c.workspace_id,
            flows.c.name,
            flows.c.description,
            flows.c.practice_area,
            flows.c.is_template,
            flows.c.is_active,
            flows.c.trigger,
            flows.c.trigger_config,
            flows.c.nodes,
            flows.c.edges,
            flows.c.run_count,
            flows.c.created_at,
            flows.c.updated_at,
            flows.c.deleted_at,
        )
    ).mappings()

    for row in rows:
        revision_id = uuid.uuid4()
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "revision_id": str(revision_id),
            "revision": 1,
            "version": 1,
            "flow_id": str(row["id"]),
            "workspace_id": (str(row["workspace_id"]) if row["workspace_id"] else None),
            "name": row["name"],
            "description": row["description"],
            "practice_area": row["practice_area"],
            "is_template": bool(row["is_template"]),
            "is_active": bool(row["is_active"]),
            "trigger": row["trigger"],
            "trigger_config": row["trigger_config"],
            "nodes": row["nodes"],
            "edges": row["edges"],
            "run_count": row["run_count"] or 0,
            "created_at": _serialized_datetime(row["created_at"]),
            "updated_at": _serialized_datetime(row["updated_at"]),
            "deleted_at": _serialized_datetime(row["deleted_at"]),
        }
        bind.execute(
            flow_revisions.insert().values(
                id=revision_id,
                flow_id=row["id"],
                revision_number=1,
                snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
                snapshot=snapshot,
                snapshot_hash=_snapshot_hash(snapshot),
                source="migration_backfill",
                created_by_user_id=None,
            )
        )
        bind.execute(
            flows.update()
            .where(flows.c.id == row["id"])
            .values(current_revision=1, current_revision_id=revision_id)
        )
        bind.execute(
            flow_runs.update()
            .where(flow_runs.c.flow_id == row["id"])
            .values(revision_id=revision_id)
        )

    missing_run_revisions = bind.scalar(
        sa.select(sa.func.count()).select_from(flow_runs).where(flow_runs.c.revision_id.is_(None))
    )
    if missing_run_revisions:
        raise RuntimeError("Não foi possível vincular todos os flow_runs legados a uma revisão")


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_flow_revision_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'flow_revisions are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_flow_revisions_immutable
            BEFORE UPDATE OR DELETE ON flow_revisions
            FOR EACH ROW EXECUTE FUNCTION prevent_flow_revision_mutation()
            """
        )
        return
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_flow_revisions_no_update
            BEFORE UPDATE ON flow_revisions
            BEGIN
                SELECT RAISE(ABORT, 'flow_revisions are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_flow_revisions_no_delete
            BEFORE DELETE ON flow_revisions
            BEGIN
                SELECT RAISE(ABORT, 'flow_revisions are immutable');
            END
            """
        )
        return
    raise RuntimeError(f"Dialeto não suportado para flow revisions: {dialect}")


def _drop_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_flow_revisions_immutable ON flow_revisions")
        op.execute("DROP FUNCTION IF EXISTS prevent_flow_revision_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_flow_revisions_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_flow_revisions_no_delete")


def upgrade() -> None:
    op.create_table(
        "flow_revisions",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("flow_id", FlexibleUUID(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot_schema_version",
            sa.Integer(),
            server_default=str(SNAPSHOT_SCHEMA_VERSION),
            nullable=False,
        ),
        sa.Column("snapshot", FlexibleJSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_flow_revisions_revision_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["flow_id"],
            ["flows.id"],
            name="fk_flow_revisions_flow_id_flows",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "flow_id",
            "revision_number",
            name="uq_flow_revisions_flow_revision_number",
        ),
    )
    op.create_index(
        "ix_flow_revisions_flow_created_at",
        "flow_revisions",
        ["flow_id", "created_at"],
        unique=False,
    )

    with op.batch_alter_table("flows") as batch_op:
        batch_op.add_column(
            sa.Column(
                "current_revision",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("current_revision_id", FlexibleUUID(), nullable=True))
        batch_op.create_check_constraint(
            "ck_flows_current_revision_nonnegative",
            "current_revision >= 0",
        )
        batch_op.create_foreign_key(
            "fk_flows_current_revision_id_flow_revisions",
            "flow_revisions",
            ["current_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_flows_current_revision_id",
        "flows",
        ["current_revision_id"],
        unique=False,
    )

    with op.batch_alter_table("flow_runs") as batch_op:
        batch_op.add_column(sa.Column("revision_id", FlexibleUUID(), nullable=True))
        batch_op.create_foreign_key(
            "fk_flow_runs_revision_id_flow_revisions",
            "flow_revisions",
            ["revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    _backfill_revisions()

    with op.batch_alter_table("flow_runs") as batch_op:
        batch_op.alter_column(
            "revision_id",
            existing_type=FlexibleUUID(),
            nullable=False,
        )
    op.create_index(
        "ix_flow_runs_revision_id",
        "flow_runs",
        ["revision_id"],
        unique=False,
    )
    _create_immutability_guards()


def downgrade() -> None:
    _drop_immutability_guards()

    op.drop_index("ix_flow_runs_revision_id", table_name="flow_runs")
    with op.batch_alter_table("flow_runs") as batch_op:
        batch_op.drop_constraint(
            "fk_flow_runs_revision_id_flow_revisions",
            type_="foreignkey",
        )
        batch_op.drop_column("revision_id")

    op.drop_index("ix_flows_current_revision_id", table_name="flows")
    with op.batch_alter_table("flows") as batch_op:
        batch_op.drop_constraint(
            "fk_flows_current_revision_id_flow_revisions",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "ck_flows_current_revision_nonnegative",
            type_="check",
        )
        batch_op.drop_column("current_revision_id")
        batch_op.drop_column("current_revision")

    op.drop_index(
        "ix_flow_revisions_flow_created_at",
        table_name="flow_revisions",
    )
    op.drop_table("flow_revisions")
