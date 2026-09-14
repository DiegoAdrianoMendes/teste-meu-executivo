"""Enforce tenant-bound references and immutable evidence ledgers.

Revision ID: d4f6a8c0e215
Revises: c9e2f4a6b803
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "d4f6a8c0e215"
down_revision: str | Sequence[str] | None = "c9e2f4a6b803"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
EVIDENCE_SCHEMA_VERSION = 1
SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# table, child column, parent table, on delete, composite constraint name
TENANT_FOREIGN_KEYS = (
    (
        "klm_omnichannel_threads",
        "matter_id",
        "matters",
        "RESTRICT",
        "fk_klm_omnichannel_thread_workspace_matter",
    ),
    (
        "klm_omnichannel_threads",
        "client_id",
        "clients",
        "RESTRICT",
        "fk_klm_omnichannel_thread_workspace_client",
    ),
    (
        "klm_omnichannel_messages",
        "thread_id",
        "klm_omnichannel_threads",
        "CASCADE",
        "fk_klm_omnichannel_message_workspace_thread",
    ),
    (
        "klm_omnichannel_messages",
        "matter_id",
        "matters",
        "RESTRICT",
        "fk_klm_omnichannel_message_workspace_matter",
    ),
    (
        "klm_follow_up_tasks",
        "matter_id",
        "matters",
        "CASCADE",
        "fk_klm_follow_up_workspace_matter",
    ),
    (
        "klm_follow_up_tasks",
        "thread_id",
        "klm_omnichannel_threads",
        "CASCADE",
        "fk_klm_follow_up_workspace_thread",
    ),
    (
        "klm_acquisition_events",
        "matter_id",
        "matters",
        "RESTRICT",
        "fk_klm_acquisition_workspace_matter",
    ),
    (
        "klm_acquisition_events",
        "client_id",
        "clients",
        "RESTRICT",
        "fk_klm_acquisition_workspace_client",
    ),
    (
        "klm_legal_artifact_versions",
        "matter_id",
        "matters",
        "CASCADE",
        "fk_klm_legal_artifact_workspace_matter",
    ),
    (
        "klm_process_monitoring_records",
        "matter_id",
        "matters",
        "CASCADE",
        "fk_klm_process_workspace_matter",
    ),
    (
        "klm_external_action_intents",
        "matter_id",
        "matters",
        "RESTRICT",
        "fk_klm_external_intent_workspace_matter",
    ),
    (
        "oss_native_commands",
        "state_id",
        "oss_native_capability_states",
        "RESTRICT",
        "fk_oss_native_command_workspace_state",
    ),
    (
        "oss_native_records",
        "last_command_id",
        "oss_native_commands",
        "RESTRICT",
        "fk_oss_native_record_workspace_last_command",
    ),
    (
        "oss_native_audit_events",
        "state_id",
        "oss_native_capability_states",
        "RESTRICT",
        "fk_oss_native_audit_workspace_state",
    ),
    (
        "oss_native_audit_events",
        "command_id",
        "oss_native_commands",
        "RESTRICT",
        "fk_oss_native_audit_workspace_command",
    ),
)

PARENT_UNIQUES = {
    "klm_omnichannel_threads": (
        (
            "uq_klm_omnichannel_thread_workspace_id",
            ("workspace_id", "id"),
        ),
    ),
    "klm_legal_artifact_versions": (
        (
            "uq_klm_legal_artifact_version_workspace_id",
            ("workspace_id", "id"),
        ),
        (
            "uq_klm_legal_artifact_version_state_parent",
            ("workspace_id", "id", "matter_id"),
        ),
    ),
    "oss_native_capability_states": (
        ("uq_oss_native_state_workspace_id", ("workspace_id", "id")),
    ),
    "oss_native_commands": (
        ("uq_oss_native_command_workspace_id", ("workspace_id", "id")),
    ),
}

TABLE_ORDER = (
    "klm_omnichannel_threads",
    "klm_omnichannel_messages",
    "klm_follow_up_tasks",
    "klm_acquisition_events",
    "klm_process_monitoring_records",
    "klm_external_action_intents",
    "oss_native_capability_states",
    "oss_native_commands",
    "oss_native_records",
    "oss_native_audit_events",
)

IMMUTABLE_TABLES = (
    "klm_audit_events",
    "oss_native_audit_events",
    "oss_function_unit_events",
    "klm_legal_artifact_versions",
)

LEGAL_MUTABLE_INDEXES = (
    "ix_klm_legal_artifact_workspace_status",
    "ix_klm_legal_artifact_approval_item_id",
)


def _specs_for(table_name: str):
    return tuple(spec for spec in TENANT_FOREIGN_KEYS if spec[0] == table_name)


def _foreign_key_name(
    table_name: str,
    constrained_column: str,
    referred_table: str,
) -> str:
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys(table_name):
        if (
            foreign_key["constrained_columns"] == [constrained_column]
            and foreign_key["referred_table"] == referred_table
        ):
            name = foreign_key.get("name")
            if name:
                return str(name)
            if op.get_bind().dialect.name == "sqlite":
                return f"fk_{table_name}_{constrained_column}_{referred_table}"
    raise RuntimeError(
        f"Expected legacy foreign key not found: {table_name}.{constrained_column} "
        f"-> {referred_table}.id"
    )


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(
        index.get("name") == index_name
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    )


def _cross_tenant_violations() -> dict[str, int]:
    bind = op.get_bind()
    violations: dict[str, int] = {}
    for table_name, child_column, parent_table, _ondelete, _name in TENANT_FOREIGN_KEYS:
        statement = sa.text(
            f'''
            SELECT COUNT(*)
            FROM "{table_name}" AS child
            LEFT JOIN "{parent_table}" AS parent
              ON parent.id = child."{child_column}"
            WHERE child."{child_column}" IS NOT NULL
              AND (parent.id IS NULL OR child.workspace_id <> parent.workspace_id)
            '''
        )
        violations[f"{table_name}.{child_column}"] = int(
            bind.execute(statement).scalar_one()
        )

    approval_statement = sa.text(
        """
        SELECT COUNT(*)
        FROM klm_legal_artifact_versions AS artifact
        LEFT JOIN approval_queue_items AS approval
          ON approval.id = artifact.approval_item_id
        LEFT JOIN matters AS approval_matter
          ON approval_matter.id = approval.matter_id
        WHERE artifact.approval_item_id IS NOT NULL
          AND (
              approval.id IS NULL
              OR approval_matter.id IS NULL
              OR artifact.matter_id <> approval.matter_id
              OR artifact.workspace_id <> approval_matter.workspace_id
          )
        """
    )
    violations["klm_legal_artifact_versions.approval_item_id"] = int(
        bind.execute(approval_statement).scalar_one()
    )
    return violations


def _require_tenant_consistency() -> None:
    violations = {
        label: count
        for label, count in _cross_tenant_violations().items()
        if count > 0
    }
    if violations:
        details = ", ".join(
            f"{label}={count}" for label, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Tenant evidence migration blocked by cross-workspace or orphan rows: "
            f"{details}"
        )


def _require_unique_native_approval_consumption() -> None:
    bind = op.get_bind()
    missing_entity_ids = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM klm_audit_events
                WHERE event_type = 'native_capability_human_approval_consumed'
                  AND entity_id IS NULL
                """
            )
        ).scalar_one()
    )
    duplicate_keys = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT workspace_id, entity_type, entity_id
                    FROM klm_audit_events
                    WHERE event_type = 'native_capability_human_approval_consumed'
                    GROUP BY workspace_id, entity_type, entity_id
                    HAVING COUNT(*) > 1
                ) AS duplicate_consumptions
                """
            )
        ).scalar_one()
    )
    if missing_entity_ids or duplicate_keys:
        raise RuntimeError(
            "Tenant evidence migration blocked by invalid native approval consumption "
            f"events: missing_entity_id={missing_entity_ids}, "
            f"duplicate_keys={duplicate_keys}"
        )


def _create_native_approval_consumption_guard() -> None:
    predicate = sa.text(
        "event_type = 'native_capability_human_approval_consumed'"
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "klm_audit_events",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_check_constraint(
                "ck_klm_audit_consumed_approval_entity",
                "event_type <> 'native_capability_human_approval_consumed' "
                "OR entity_id IS NOT NULL",
            )
    else:
        op.create_check_constraint(
            "ck_klm_audit_consumed_approval_entity",
            "klm_audit_events",
            "event_type <> 'native_capability_human_approval_consumed' "
            "OR entity_id IS NOT NULL",
        )
    op.create_index(
        "uq_klm_audit_native_approval_consumed_once",
        "klm_audit_events",
        ["workspace_id", "entity_type", "entity_id"],
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def _drop_native_approval_consumption_guard() -> None:
    op.drop_index(
        "uq_klm_audit_native_approval_consumed_once",
        table_name="klm_audit_events",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "klm_audit_events",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "ck_klm_audit_consumed_approval_entity",
                type_="check",
            )
        return
    op.drop_constraint(
        "ck_klm_audit_consumed_approval_entity",
        "klm_audit_events",
        type_="check",
    )


def _json_value(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _datetime_value(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _canonical_evidence(row: Mapping[str, object]) -> bytes:
    payload = {
        "artifactKey": str(row["artifact_key"]),
        "content": _json_value(row["content"]),
        "createdByUserId": str(row["created_by_user_id"]),
        "evidenceSchemaVersion": EVIDENCE_SCHEMA_VERSION,
        "kind": row["kind"],
        "matterId": str(row["matter_id"]),
        "sourceSnapshot": _json_value(row["source_snapshot"]),
        "title": row["title"],
        "version": row["version"],
        "workspaceId": str(row["workspace_id"]),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _legal_artifact_rows() -> list[dict[str, object]]:
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT id, workspace_id, matter_id, artifact_key, version, kind, title,
                   content, source_snapshot, created_by_user_id, status,
                   approval_item_id, created_at, updated_at
            FROM klm_legal_artifact_versions
            ORDER BY id
            """
        )
    )
    return [dict(row) for row in rows.mappings()]


def _upgrade_fk_table(table_name: str) -> None:
    specs = _specs_for(table_name)
    uniques = PARENT_UNIQUES.get(table_name, ())
    if op.get_bind().dialect.name == "sqlite":
        legacy_names = {
            child_column: _foreign_key_name(table_name, child_column, parent_table)
            for _, child_column, parent_table, _, _ in specs
        }
        with op.batch_alter_table(
            table_name,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            for _, child_column, _parent, _ondelete, _name in specs:
                batch_op.drop_constraint(
                    legacy_names[child_column],
                    type_="foreignkey",
                )
            for unique_name, columns in uniques:
                batch_op.create_unique_constraint(unique_name, list(columns))
            for _, child_column, parent_table, ondelete, constraint_name in specs:
                batch_op.create_foreign_key(
                    constraint_name,
                    parent_table,
                    ["workspace_id", child_column],
                    ["workspace_id", "id"],
                    ondelete=ondelete,
                )
        return

    legacy_names = {
        child_column: _foreign_key_name(table_name, child_column, parent_table)
        for _, child_column, parent_table, _, _ in specs
    }
    for _, child_column, _parent, _ondelete, _name in specs:
        op.drop_constraint(legacy_names[child_column], table_name, type_="foreignkey")
    for unique_name, columns in uniques:
        op.create_unique_constraint(unique_name, table_name, list(columns))
    for _, child_column, parent_table, ondelete, constraint_name in specs:
        op.create_foreign_key(
            constraint_name,
            table_name,
            parent_table,
            ["workspace_id", child_column],
            ["workspace_id", "id"],
            ondelete=ondelete,
        )


def _downgrade_fk_table(table_name: str) -> None:
    specs = _specs_for(table_name)
    uniques = PARENT_UNIQUES.get(table_name, ())
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            for _, _child, _parent, _ondelete, constraint_name in specs:
                batch_op.drop_constraint(constraint_name, type_="foreignkey")
            for _, child_column, parent_table, ondelete, constraint_name in specs:
                batch_op.create_foreign_key(
                    f"{constraint_name}_legacy",
                    parent_table,
                    [child_column],
                    ["id"],
                    ondelete=("SET NULL" if ondelete == "RESTRICT" else ondelete),
                )
            for unique_name, _columns in reversed(uniques):
                batch_op.drop_constraint(unique_name, type_="unique")
        return

    for _, _child, _parent, _ondelete, constraint_name in specs:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")
    for _, child_column, parent_table, ondelete, constraint_name in specs:
        op.create_foreign_key(
            f"{constraint_name}_legacy",
            table_name,
            parent_table,
            [child_column],
            ["id"],
            ondelete=("SET NULL" if ondelete == "RESTRICT" else ondelete),
        )
    for unique_name, _columns in reversed(uniques):
        op.drop_constraint(unique_name, table_name, type_="unique")


def _upgrade_legal_artifact_versions(rows: list[dict[str, object]]) -> None:
    table_name = "klm_legal_artifact_versions"
    matter_fk = _foreign_key_name(table_name, "matter_id", "matters")
    approval_fk = _foreign_key_name(
        table_name,
        "approval_item_id",
        "approval_queue_items",
    )
    for index_name in LEGAL_MUTABLE_INDEXES:
        if _index_exists(table_name, index_name):
            op.drop_index(index_name, table_name=table_name)

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(matter_fk, type_="foreignkey")
            batch_op.drop_constraint(approval_fk, type_="foreignkey")
            for unique_name, columns in PARENT_UNIQUES[table_name]:
                batch_op.create_unique_constraint(unique_name, list(columns))
            batch_op.create_foreign_key(
                "fk_klm_legal_artifact_workspace_matter",
                "matters",
                ["workspace_id", "matter_id"],
                ["workspace_id", "id"],
                ondelete="CASCADE",
            )
            batch_op.add_column(
                sa.Column("evidence_schema_version", sa.Integer(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("evidence_sha256", sa.String(length=64), nullable=True)
            )
            batch_op.drop_column("status")
            batch_op.drop_column("approval_item_id")
            batch_op.drop_column("updated_at")
    else:
        op.drop_constraint(matter_fk, table_name, type_="foreignkey")
        op.drop_constraint(approval_fk, table_name, type_="foreignkey")
        for unique_name, columns in PARENT_UNIQUES[table_name]:
            op.create_unique_constraint(unique_name, table_name, list(columns))
        op.create_foreign_key(
            "fk_klm_legal_artifact_workspace_matter",
            table_name,
            "matters",
            ["workspace_id", "matter_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        op.add_column(
            table_name,
            sa.Column("evidence_schema_version", sa.Integer(), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        )
        op.drop_column(table_name, "status")
        op.drop_column(table_name, "approval_item_id")
        op.drop_column(table_name, "updated_at")

    bind = op.get_bind()
    for row in rows:
        bind.execute(
            sa.text(
                """
                UPDATE klm_legal_artifact_versions
                SET evidence_schema_version = :schema_version,
                    evidence_sha256 = :digest
                WHERE id = :artifact_id
                """
            ),
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "digest": hashlib.sha256(_canonical_evidence(row)).hexdigest(),
                "artifact_id": row["id"],
            },
        )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.alter_column(
                "evidence_schema_version",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch_op.alter_column(
                "evidence_sha256",
                existing_type=sa.String(length=64),
                nullable=False,
            )
            batch_op.create_check_constraint(
                "ck_klm_legal_artifact_evidence_schema_v1",
                "evidence_schema_version = 1",
            )
            batch_op.create_check_constraint(
                "ck_klm_legal_artifact_evidence_sha256_length",
                "length(evidence_sha256) = 64",
            )
    else:
        op.alter_column(
            table_name,
            "evidence_schema_version",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.alter_column(
            table_name,
            "evidence_sha256",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        op.create_check_constraint(
            "ck_klm_legal_artifact_evidence_schema_v1",
            table_name,
            "evidence_schema_version = 1",
        )
        op.create_check_constraint(
            "ck_klm_legal_artifact_evidence_sha256_length",
            table_name,
            "length(evidence_sha256) = 64",
        )


def _add_approval_parent_unique() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "approval_queue_items",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_unique_constraint(
                "uq_approval_queue_item_id_matter",
                ["id", "matter_id"],
            )
        return
    op.create_unique_constraint(
        "uq_approval_queue_item_id_matter",
        "approval_queue_items",
        ["id", "matter_id"],
    )


def _drop_approval_parent_unique() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "approval_queue_items",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "uq_approval_queue_item_id_matter",
                type_="unique",
            )
        return
    op.drop_constraint(
        "uq_approval_queue_item_id_matter",
        "approval_queue_items",
        type_="unique",
    )


def _create_legal_artifact_states(rows: list[dict[str, object]]) -> None:
    op.create_table(
        "klm_legal_artifact_states",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("artifact_version_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("approval_item_id", FlexibleUUID(), nullable=True),
        sa.Column("approval_matter_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "artifact_version_id", "matter_id"],
            [
                "klm_legal_artifact_versions.workspace_id",
                "klm_legal_artifact_versions.id",
                "klm_legal_artifact_versions.matter_id",
            ],
            name="fk_klm_legal_artifact_state_workspace_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_item_id", "approval_matter_id"],
            ["approval_queue_items.id", "approval_queue_items.matter_id"],
            name="fk_klm_legal_artifact_state_approval_matter",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "artifact_version_id",
            name="uq_klm_legal_artifact_state_version",
        ),
        sa.CheckConstraint(
            "(approval_item_id IS NULL AND approval_matter_id IS NULL) OR "
            "(approval_item_id IS NOT NULL AND approval_matter_id IS NOT NULL)",
            name="ck_klm_legal_artifact_state_approval_pair",
        ),
        sa.CheckConstraint(
            "approval_matter_id IS NULL OR approval_matter_id = matter_id",
            name="ck_klm_legal_artifact_state_approval_matter",
        ),
    )
    op.create_index(
        "ix_klm_legal_artifact_states_workspace_id",
        "klm_legal_artifact_states",
        ["workspace_id"],
    )
    op.create_index(
        "ix_klm_legal_artifact_states_artifact_version_id",
        "klm_legal_artifact_states",
        ["artifact_version_id"],
    )
    op.create_index(
        "ix_klm_legal_artifact_states_status",
        "klm_legal_artifact_states",
        ["status"],
    )
    op.create_index(
        "ix_klm_legal_artifact_states_approval_item_id",
        "klm_legal_artifact_states",
        ["approval_item_id"],
    )

    state_table = sa.table(
        "klm_legal_artifact_states",
        sa.column("workspace_id", FlexibleUUID()),
        sa.column("artifact_version_id", FlexibleUUID()),
        sa.column("matter_id", FlexibleUUID()),
        sa.column("status", sa.String(length=40)),
        sa.column("approval_item_id", FlexibleUUID()),
        sa.column("approval_matter_id", FlexibleUUID()),
        sa.column("id", FlexibleUUID()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    state_rows = [
        {
            "workspace_id": row["workspace_id"],
            "artifact_version_id": row["id"],
            "matter_id": row["matter_id"],
            "status": row["status"],
            "approval_item_id": row["approval_item_id"],
            "approval_matter_id": (
                row["matter_id"] if row["approval_item_id"] is not None else None
            ),
            "id": uuid4(),
            "created_at": _datetime_value(row["created_at"]),
            "updated_at": _datetime_value(row["updated_at"]),
        }
        for row in rows
    ]
    if state_rows:
        op.get_bind().execute(state_table.insert(), state_rows)

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        )
        op.execute('ALTER TABLE "klm_legal_artifact_states" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "klm_legal_artifact_states" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY "workspace_current_id" ON "klm_legal_artifact_states" '
            f"AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"
        )


def _create_immutable_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_oss_function_unit_events_immutable "
            "ON oss_function_unit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_oss_function_unit_event_mutation()")
        op.execute(
            """
            CREATE FUNCTION klm_reject_immutable_evidence_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is immutable; % is forbidden', TG_TABLE_NAME, TG_OP
                    USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f'''
                CREATE TRIGGER trg_{table_name}_immutable_row
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_immutable_evidence_mutation()
                '''
            )
            op.execute(
                f'''
                CREATE TRIGGER trg_{table_name}_immutable_truncate
                BEFORE TRUNCATE ON "{table_name}"
                FOR EACH STATEMENT EXECUTE FUNCTION klm_reject_immutable_evidence_mutation()
                '''
            )
        return

    for table_name in IMMUTABLE_TABLES:
        message = f"{table_name} is immutable"
        for operation in ("update", "delete"):
            op.execute(
                f'''
                CREATE TRIGGER "trg_{table_name}_immutable_{operation}"
                BEFORE {operation.upper()} ON "{table_name}"
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                '''
            )


def _drop_immutable_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(IMMUTABLE_TABLES):
            op.execute(
                f'DROP TRIGGER IF EXISTS trg_{table_name}_immutable_truncate '
                f'ON "{table_name}"'
            )
            op.execute(
                f'DROP TRIGGER IF EXISTS trg_{table_name}_immutable_row '
                f'ON "{table_name}"'
            )
        op.execute("DROP FUNCTION IF EXISTS klm_reject_immutable_evidence_mutation()")
        return

    for table_name in reversed(IMMUTABLE_TABLES):
        for operation in ("delete", "update"):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"'
            )


def _restore_function_unit_guard() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION reject_oss_function_unit_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'oss_function_unit_events are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_oss_function_unit_events_immutable
        BEFORE UPDATE OR DELETE ON oss_function_unit_events
        FOR EACH ROW EXECUTE FUNCTION reject_oss_function_unit_event_mutation()
        """
    )


def _require_downgrade_state_projection() -> list[dict[str, object]]:
    bind = op.get_bind()
    missing = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM klm_legal_artifact_versions AS artifact
                LEFT JOIN klm_legal_artifact_states AS state
                  ON state.workspace_id = artifact.workspace_id
                 AND state.artifact_version_id = artifact.id
                 AND state.matter_id = artifact.matter_id
                WHERE state.id IS NULL
                """
            )
        ).scalar_one()
    )
    if missing:
        raise RuntimeError(
            "Cannot downgrade legal artifact state projection: "
            f"versions_without_state={missing}"
        )
    rows = bind.execute(
        sa.text(
            """
            SELECT artifact_version_id, status, approval_item_id, updated_at
            FROM klm_legal_artifact_states
            ORDER BY artifact_version_id
            """
        )
    )
    return [dict(row) for row in rows.mappings()]


def _drop_legal_artifact_states() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "workspace_current_id" '
            'ON "klm_legal_artifact_states"'
        )
        op.execute(
            'ALTER TABLE "klm_legal_artifact_states" NO FORCE ROW LEVEL SECURITY'
        )
        op.execute(
            'ALTER TABLE "klm_legal_artifact_states" DISABLE ROW LEVEL SECURITY'
        )
    op.drop_index(
        "ix_klm_legal_artifact_states_approval_item_id",
        table_name="klm_legal_artifact_states",
    )
    op.drop_index(
        "ix_klm_legal_artifact_states_status",
        table_name="klm_legal_artifact_states",
    )
    op.drop_index(
        "ix_klm_legal_artifact_states_artifact_version_id",
        table_name="klm_legal_artifact_states",
    )
    op.drop_index(
        "ix_klm_legal_artifact_states_workspace_id",
        table_name="klm_legal_artifact_states",
    )
    op.drop_table("klm_legal_artifact_states")


def _downgrade_legal_artifact_versions(
    state_rows: list[dict[str, object]],
) -> None:
    table_name = "klm_legal_artifact_versions"
    op.add_column(
        table_name,
        sa.Column("status", sa.String(length=40), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("approval_item_id", FlexibleUUID(), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind = op.get_bind()
    for row in state_rows:
        bind.execute(
            sa.text(
                """
                UPDATE klm_legal_artifact_versions
                SET status = :status,
                    approval_item_id = :approval_item_id,
                    updated_at = :updated_at
                WHERE id = :artifact_id
                """
            ),
            {
                "status": row["status"],
                "approval_item_id": row["approval_item_id"],
                "updated_at": _datetime_value(row["updated_at"]),
                "artifact_id": row["artifact_version_id"],
            },
        )

    _drop_legal_artifact_states()
    _drop_approval_parent_unique()

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_klm_legal_artifact_workspace_matter",
                type_="foreignkey",
            )
            batch_op.create_foreign_key(
                "fk_klm_legal_artifact_versions_matter_id_matters",
                "matters",
                ["matter_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_foreign_key(
                "fk_klm_legal_artifact_approval_item",
                "approval_queue_items",
                ["approval_item_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.drop_constraint(
                "ck_klm_legal_artifact_evidence_sha256_length",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_klm_legal_artifact_evidence_schema_v1",
                type_="check",
            )
            batch_op.drop_column("evidence_sha256")
            batch_op.drop_column("evidence_schema_version")
            batch_op.alter_column(
                "status",
                existing_type=sa.String(length=40),
                nullable=False,
                server_default="draft",
            )
            batch_op.alter_column(
                "updated_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
            for unique_name, _columns in reversed(PARENT_UNIQUES[table_name]):
                batch_op.drop_constraint(unique_name, type_="unique")
    else:
        op.drop_constraint(
            "fk_klm_legal_artifact_workspace_matter",
            table_name,
            type_="foreignkey",
        )
        op.create_foreign_key(
            "fk_klm_legal_artifact_versions_matter_id_matters",
            table_name,
            "matters",
            ["matter_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_foreign_key(
            "fk_klm_legal_artifact_approval_item",
            table_name,
            "approval_queue_items",
            ["approval_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.drop_constraint(
            "ck_klm_legal_artifact_evidence_sha256_length",
            table_name,
            type_="check",
        )
        op.drop_constraint(
            "ck_klm_legal_artifact_evidence_schema_v1",
            table_name,
            type_="check",
        )
        op.drop_column(table_name, "evidence_sha256")
        op.drop_column(table_name, "evidence_schema_version")
        op.alter_column(
            table_name,
            "status",
            existing_type=sa.String(length=40),
            nullable=False,
            server_default="draft",
        )
        op.alter_column(
            table_name,
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
        for unique_name, _columns in reversed(PARENT_UNIQUES[table_name]):
            op.drop_constraint(unique_name, table_name, type_="unique")

    op.create_index(
        "ix_klm_legal_artifact_workspace_status",
        table_name,
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_klm_legal_artifact_approval_item_id",
        table_name,
        ["approval_item_id"],
    )


def upgrade() -> None:
    _require_tenant_consistency()
    _require_unique_native_approval_consumption()
    legal_rows = _legal_artifact_rows()

    for table_name in TABLE_ORDER:
        _upgrade_fk_table(table_name)
    _upgrade_legal_artifact_versions(legal_rows)
    _add_approval_parent_unique()
    _create_legal_artifact_states(legal_rows)
    _create_native_approval_consumption_guard()
    _create_immutable_guards()


def downgrade() -> None:
    state_rows = _require_downgrade_state_projection()
    _drop_immutable_guards()
    _drop_native_approval_consumption_guard()
    _downgrade_legal_artifact_versions(state_rows)

    for table_name in reversed(TABLE_ORDER):
        _downgrade_fk_table(table_name)
    _restore_function_unit_guard()
