"""add native tenant-scoped support center

Revision ID: e2f4a6b8c0d3
Revises: d1e3f5a7b9c2
Create Date: 2026-07-24 12:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "e2f4a6b8c0d3"
down_revision: str | Sequence[str] | None = "d1e3f5a7b9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TENANT_TABLES = (
    "support_tickets",
    "support_ticket_collaborators",
    "support_ticket_messages",
    "support_ticket_attachments",
    "support_ticket_events",
    "support_notification_outbox",
)
IMMUTABLE_TABLES = ("support_ticket_messages", "support_ticket_events")


def _enum_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        f"workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in reversed(TENANT_TABLES):
        op.execute(
            f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"'
        )
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION klm_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable_truncate"
                BEFORE TRUNCATE ON "{table_name}"
                FOR EACH STATEMENT EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
        return

    if dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS
                    "trg_{table_name}_immutable_{operation}"
                    BEFORE {operation.upper()} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END
                    '''
                )


def _disable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in reversed(IMMUTABLE_TABLES):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_truncate" '
                f'ON "{table_name}"'
            )
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" '
                f'ON "{table_name}"'
            )
        return

    if dialect == "sqlite":
        for table_name in reversed(IMMUTABLE_TABLES):
            for operation in ("update", "delete"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS '
                    f'"trg_{table_name}_immutable_{operation}"'
                )


def _json_value(value: Any) -> Any:
    if value in (None, "", []):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {"legacyValue": str(value)}


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc)


def _legacy_status(value: Any) -> str:
    return {
        "received": "new",
        "analyzing": "in_progress",
        "patch_ready": "validating",
        "validating": "validating",
        "resolved": "resolved",
        "rejected": "cancelled",
    }.get(str(getattr(value, "value", value)), "new")


def _migrate_legacy_technical_requests() -> None:
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"

    def bind_uuid(value: uuid.UUID) -> uuid.UUID | str:
        return str(value) if sqlite else value

    inspector = sa.inspect(bind)
    if "klm_technical_requests" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT id, workspace_id, created_by_user_id, assigned_to_user_id,
                   title, description, route, severity, status, evidence,
                   resolution, created_at, updated_at
            FROM klm_technical_requests
            """
        )
    ).mappings()

    tickets = sa.table(
        "support_tickets",
        sa.column("id"),
        sa.column("public_number"),
        sa.column("workspace_id"),
        sa.column("reporter_user_id"),
        sa.column("assigned_to_user_id"),
        sa.column("ticket_type"),
        sa.column("priority"),
        sa.column("severity"),
        sa.column("queue"),
        sa.column("status"),
        sa.column("subject"),
        sa.column("description"),
        sa.column("route"),
        sa.column("technical_context", FlexibleJSON()),
        sa.column("error_fingerprint"),
        sa.column("legacy_technical_request_id"),
        sa.column("idempotency_key"),
        sa.column("version"),
        sa.column("first_response_due_at"),
        sa.column("resolution_due_at"),
        sa.column("first_responded_at"),
        sa.column("resolved_at"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    events = sa.table(
        "support_ticket_events",
        sa.column("id"),
        sa.column("ticket_id"),
        sa.column("workspace_id"),
        sa.column("actor_user_id"),
        sa.column("event_type"),
        sa.column("summary"),
        sa.column("before_data", FlexibleJSON()),
        sa.column("after_data", FlexibleJSON()),
        sa.column("created_at"),
    )

    for row in rows:
        legacy_id = str(row["id"])
        created_at = _as_utc(row["created_at"])
        status = _legacy_status(row["status"])
        severity = str(getattr(row["severity"], "value", row["severity"]))
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"
        ticket_id = uuid.uuid5(uuid.NAMESPACE_URL, f"support:{legacy_id}")
        stored_ticket_id = bind_uuid(ticket_id)
        public_number = f"SAC-{created_at:%Y%m%d}-{legacy_id.replace('-', '')[:8].upper()}"
        evidence = _json_value(row["evidence"])
        technical_context = {
            "legacyEvidenceCount": len(evidence) if isinstance(evidence, list) else 1,
            "migratedFrom": "klm_technical_requests",
        }
        fingerprint_source = "|".join(
            (
                str(row["workspace_id"]),
                str(row["route"] or ""),
                str(row["title"] or ""),
            )
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        first_due = created_at + timedelta(
            hours=1 if severity == "critical" else 4
        )
        resolution_due = created_at + timedelta(
            hours=8 if severity == "critical" else 48
        )
        resolved_at = created_at if status == "resolved" else None

        bind.execute(
            sa.insert(tickets).values(
                id=stored_ticket_id,
                public_number=public_number,
                workspace_id=row["workspace_id"],
                reporter_user_id=row["created_by_user_id"],
                assigned_to_user_id=row["assigned_to_user_id"],
                ticket_type="problem",
                priority="urgent" if severity == "critical" else "normal",
                severity=severity,
                queue="technical",
                status=status,
                subject=row["title"],
                description=row["description"],
                route=row["route"],
                technical_context=technical_context,
                error_fingerprint=fingerprint,
                legacy_technical_request_id=row["id"],
                idempotency_key=f"legacy:{legacy_id}",
                version=1,
                first_response_due_at=first_due,
                resolution_due_at=resolution_due,
                first_responded_at=created_at
                if status in {"in_progress", "validating", "resolved"}
                else None,
                resolved_at=resolved_at,
                created_at=created_at,
                updated_at=_as_utc(row["updated_at"]),
            )
        )
        bind.execute(
            sa.insert(events).values(
                id=bind_uuid(uuid.uuid4()),
                ticket_id=stored_ticket_id,
                workspace_id=row["workspace_id"],
                actor_user_id=row["created_by_user_id"],
                event_type="LEGACY_TECHNICAL_REQUEST_MIGRATED",
                summary="Solicitação técnica migrada para o núcleo nativo de suporte.",
                before_data={
                    "legacyId": legacy_id,
                    "status": str(getattr(row["status"], "value", row["status"])),
                    "resolution": row["resolution"],
                    "evidence": evidence,
                },
                after_data={"publicNumber": public_number, "status": status},
                created_at=created_at,
            )
        )


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("public_number", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("reporter_user_id", FlexibleUUID(), nullable=False),
        sa.Column("assigned_to_user_id", FlexibleUUID(), nullable=True),
        sa.Column("ticket_type", sa.String(length=40), nullable=False),
        sa.Column(
            "priority",
            sa.String(length=40),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "severity",
            sa.String(length=40),
            nullable=False,
            server_default="medium",
        ),
        sa.Column("queue", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="new",
        ),
        sa.Column("subject", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("route", sa.String(length=500), nullable=True),
        sa.Column(
            "technical_context",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("error_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("linked_error_log_id", FlexibleUUID(), nullable=True),
        sa.Column("linked_incident_id", FlexibleUUID(), nullable=True),
        sa.Column("linked_release_id", FlexibleUUID(), nullable=True),
        sa.Column("support_access_request_id", FlexibleUUID(), nullable=True),
        sa.Column("duplicate_of_ticket_id", FlexibleUUID(), nullable=True),
        sa.Column("legacy_technical_request_id", FlexibleUUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("satisfaction_score", sa.Integer(), nullable=True),
        sa.Column("satisfaction_comment", sa.Text(), nullable=True),
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
        _enum_check(
            "ticket_type",
            ("problem", "instability", "suggestion"),
            "support_ticket_type_enum",
        ),
        _enum_check(
            "priority",
            ("low", "normal", "high", "urgent"),
            "support_ticket_priority_enum",
        ),
        _enum_check(
            "severity",
            ("low", "medium", "high", "critical"),
            "support_ticket_severity_enum",
        ),
        _enum_check(
            "queue",
            ("support", "technical"),
            "support_ticket_queue_enum",
        ),
        _enum_check(
            "status",
            (
                "new",
                "triage",
                "in_progress",
                "waiting_customer",
                "waiting_internal",
                "validating",
                "resolved",
                "closed",
                "cancelled",
            ),
            "support_ticket_status_enum",
        ),
        sa.CheckConstraint("version > 0", name="ck_support_ticket_version_positive"),
        sa.CheckConstraint(
            "satisfaction_score IS NULL OR "
            "(satisfaction_score >= 1 AND satisfaction_score <= 5)",
            name="ck_support_ticket_satisfaction_range",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reporter_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["linked_error_log_id"], ["error_logs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_ticket_id"], ["support_tickets.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["legacy_technical_request_id"],
            ["klm_technical_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_number", name="uq_support_ticket_public_number"),
        sa.UniqueConstraint(
            "workspace_id",
            "reporter_user_id",
            "idempotency_key",
            name="uq_support_ticket_idempotency",
        ),
        sa.UniqueConstraint(
            "legacy_technical_request_id",
            name="uq_support_ticket_legacy_request",
        ),
    )
    op.create_index(
        "ix_support_ticket_workspace_status_created",
        "support_tickets",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_support_ticket_queue_severity_due",
        "support_tickets",
        ["queue", "severity", "first_response_due_at"],
    )
    op.create_index(
        "ix_support_ticket_error_fingerprint",
        "support_tickets",
        ["workspace_id", "error_fingerprint"],
    )
    for column in (
        "workspace_id",
        "reporter_user_id",
        "assigned_to_user_id",
        "ticket_type",
        "priority",
        "severity",
        "queue",
        "status",
        "first_response_due_at",
        "resolution_due_at",
    ):
        op.create_index(
            f"ix_support_tickets_{column}",
            "support_tickets",
            [column],
        )

    op.create_table(
        "support_ticket_collaborators",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("invited_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "ticket_id", "user_id", name="uq_support_ticket_collaborator"
        ),
    )
    op.create_index(
        "ix_support_ticket_collaborators_ticket_id",
        "support_ticket_collaborators",
        ["ticket_id"],
    )
    op.create_index(
        "ix_support_ticket_collaborators_workspace_id",
        "support_ticket_collaborators",
        ["workspace_id"],
    )

    op.create_table(
        "support_ticket_messages",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("author_user_id", FlexibleUUID(), nullable=True),
        sa.Column("author_label", sa.String(length=200), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=40),
            nullable=False,
            server_default="public",
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _enum_check(
            "visibility",
            ("public", "internal"),
            "support_message_visibility_enum",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "ticket_id",
            "author_user_id",
            "idempotency_key",
            name="uq_support_ticket_message_idempotency",
        ),
    )
    op.create_index(
        "ix_support_ticket_message_ticket_created",
        "support_ticket_messages",
        ["ticket_id", "created_at"],
    )
    op.create_index(
        "ix_support_ticket_messages_workspace_id",
        "support_ticket_messages",
        ["workspace_id"],
    )

    op.create_table(
        "support_ticket_attachments",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("message_id", FlexibleUUID(), nullable=True),
        sa.Column("storage_object_version_id", FlexibleUUID(), nullable=False),
        sa.Column("uploaded_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["support_ticket_messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["storage_object_version_id"], ["storage_objects.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "ticket_id",
            "storage_object_version_id",
            name="uq_support_ticket_attachment_object",
        ),
    )
    op.create_index(
        "ix_support_ticket_attachments_ticket_id",
        "support_ticket_attachments",
        ["ticket_id"],
    )
    op.create_index(
        "ix_support_ticket_attachments_workspace_id",
        "support_ticket_attachments",
        ["workspace_id"],
    )

    op.create_table(
        "support_ticket_events",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column(
            "before_data",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "after_data",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_support_ticket_event_ticket_created",
        "support_ticket_events",
        ["ticket_id", "created_at"],
    )
    op.create_index(
        "ix_support_ticket_events_workspace_id",
        "support_ticket_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_support_ticket_events_event_type",
        "support_ticket_events",
        ["event_type"],
    )

    op.create_table(
        "support_saved_filters",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("owner_user_id", FlexibleUUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "filters",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
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
            ["owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "name",
            name="uq_support_saved_filter_owner_name",
        ),
    )
    op.create_index(
        "ix_support_saved_filters_owner_user_id",
        "support_saved_filters",
        ["owner_user_id"],
    )

    op.create_table(
        "support_notification_outbox",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("recipient_user_id", FlexibleUUID(), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("event_key", sa.String(length=120), nullable=False),
        sa.Column(
            "payload",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
        _enum_check(
            "channel",
            ("email", "whatsapp"),
            "support_delivery_channel_enum",
        ),
        _enum_check(
            "status",
            ("pending", "sent", "failed", "disabled"),
            "support_delivery_status_enum",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "ticket_id",
            "recipient_user_id",
            "channel",
            "event_key",
            name="uq_support_notification_delivery",
        ),
    )
    op.create_index(
        "ix_support_notification_outbox_ticket_id",
        "support_notification_outbox",
        ["ticket_id"],
    )
    op.create_index(
        "ix_support_notification_outbox_workspace_id",
        "support_notification_outbox",
        ["workspace_id"],
    )

    _migrate_legacy_technical_requests()
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()

    op.drop_index(
        "ix_support_notification_outbox_workspace_id",
        table_name="support_notification_outbox",
    )
    op.drop_index(
        "ix_support_notification_outbox_ticket_id",
        table_name="support_notification_outbox",
    )
    op.drop_table("support_notification_outbox")
    op.drop_index(
        "ix_support_saved_filters_owner_user_id",
        table_name="support_saved_filters",
    )
    op.drop_table("support_saved_filters")
    op.drop_index(
        "ix_support_ticket_events_event_type",
        table_name="support_ticket_events",
    )
    op.drop_index(
        "ix_support_ticket_events_workspace_id",
        table_name="support_ticket_events",
    )
    op.drop_index(
        "ix_support_ticket_event_ticket_created",
        table_name="support_ticket_events",
    )
    op.drop_table("support_ticket_events")
    op.drop_index(
        "ix_support_ticket_attachments_workspace_id",
        table_name="support_ticket_attachments",
    )
    op.drop_index(
        "ix_support_ticket_attachments_ticket_id",
        table_name="support_ticket_attachments",
    )
    op.drop_table("support_ticket_attachments")
    op.drop_index(
        "ix_support_ticket_messages_workspace_id",
        table_name="support_ticket_messages",
    )
    op.drop_index(
        "ix_support_ticket_message_ticket_created",
        table_name="support_ticket_messages",
    )
    op.drop_table("support_ticket_messages")
    op.drop_index(
        "ix_support_ticket_collaborators_workspace_id",
        table_name="support_ticket_collaborators",
    )
    op.drop_index(
        "ix_support_ticket_collaborators_ticket_id",
        table_name="support_ticket_collaborators",
    )
    op.drop_table("support_ticket_collaborators")
    for column in reversed(
        (
            "workspace_id",
            "reporter_user_id",
            "assigned_to_user_id",
            "ticket_type",
            "priority",
            "severity",
            "queue",
            "status",
            "first_response_due_at",
            "resolution_due_at",
        )
    ):
        op.drop_index(
            f"ix_support_tickets_{column}",
            table_name="support_tickets",
        )
    op.drop_index(
        "ix_support_ticket_error_fingerprint",
        table_name="support_tickets",
    )
    op.drop_index(
        "ix_support_ticket_queue_severity_due",
        table_name="support_tickets",
    )
    op.drop_index(
        "ix_support_ticket_workspace_status_created",
        table_name="support_tickets",
    )
    op.drop_table("support_tickets")
