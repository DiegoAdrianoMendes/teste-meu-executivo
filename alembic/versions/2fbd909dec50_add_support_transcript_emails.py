"""F15 — cópia da conversa por e-mail ao encerrar chamados de suporte

Revision ID: 2fbd909dec50
Revises: 44cc21307259
Create Date: 2026-07-25 21:15:00.000000

Nova tabela ``support_transcript_emails``: ao contrário do outbox
operacional (``support_notification_outbox``, que nunca guarda a
descrição do chamado), esta precisa preservar o conteúdo completo da
transcrição — fail-closed com o gate de envio externo fechado significa
ficar ``pending`` com o conteúdo íntegro persistido, nunca descartado.
Self-contained: cria a tabela já com RLS habilitada usando o mesmo
predicado de acesso das demais tabelas de suporte (workspace corrente OU
contexto de plataforma ``support:*`` — ver
``f3a5c7d9e1b4_harden_platform_support_rls.py``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "2fbd909dec50"
down_revision: str | Sequence[str] | None = "44cc21307259"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "support_transcript_emails"
RLS_POLICY = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
PLATFORM_SUPPORT_SCOPE = "support"
PLATFORM_SUPPORT_ACTIONS = (
    "support:assign",
    "support:export",
    "support:manage",
    "support:respond",
    "support:triage",
    "support:view",
)


def _enum_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def _platform_support_predicate() -> str:
    tenant = (
        f"workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    allowed_actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{action}'"
        for action in PLATFORM_SUPPORT_ACTIONS
    )
    support = (
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = "
        f"'{PLATFORM_SUPPORT_SCOPE}' AND "
        f"NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') "
        f"IS NOT NULL AND "
        f"({allowed_actions})"
    )
    return f"({tenant}) OR ({support})"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = _platform_support_predicate()
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{TABLE_NAME}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{TABLE_NAME}"')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("recipient_user_id", FlexibleUUID(), nullable=False),
        sa.Column("closure_event_key", sa.String(length=120), nullable=False),
        sa.Column("final_status", sa.String(length=40), nullable=False),
        sa.Column(
            "resolved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "unsatisfied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("subject", sa.String(length=240), nullable=False),
        sa.Column(
            "transcript_snapshot",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="pending",
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
            "final_status",
            ("closed", "cancelled"),
            "ck_support_transcript_email_final_status",
        ),
        _enum_check(
            "status",
            ("pending", "sent", "failed", "disabled"),
            "support_transcript_email_status_enum",
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
            "closure_event_key",
            name="uq_support_transcript_email_closure",
        ),
    )
    op.create_index(
        "ix_support_transcript_email_ticket_id",
        TABLE_NAME,
        ["ticket_id"],
    )
    op.create_index(
        "ix_support_transcript_email_workspace_id",
        TABLE_NAME,
        ["workspace_id"],
    )
    op.create_index(
        "ix_support_transcript_email_workspace_status",
        TABLE_NAME,
        ["workspace_id", "status"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index("ix_support_transcript_email_workspace_status", table_name=TABLE_NAME)
    op.drop_index("ix_support_transcript_email_workspace_id", table_name=TABLE_NAME)
    op.drop_index("ix_support_transcript_email_ticket_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
