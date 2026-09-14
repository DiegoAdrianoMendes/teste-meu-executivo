"""Módulo processual contratável à parte — monitoramento DJEN/DataJud.

Revision ID: b6d8f0a2c415
Revises: a1f3b5c7d9e2
Create Date: 2026-07-26 12:00:00.000000

Add-on pago, NÃO core: `EntitlementKey.monitored_processes` (novo valor no
enum de entitlement, extensão da constraint fechada nas 4 tabelas de
entitlement — mesmo padrão de f2d4e6a8b013), `monitored_processes` (processo
cadastrado, quota por workspace) e `process_publications` (publicação/
andamento ingerido de forma idempotente, com prazo sugerido nunca fatal sem
confirmação humana).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "b6d8f0a2c415"
down_revision: str | Sequence[str] | None = "a1f3b5c7d9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
RLS_TABLES = ("monitored_processes", "process_publications")

ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "audio_minutes",
    "storage_bytes",
    "files",
    "users",
    "whatsapp_numbers",
    "agents",
    "automations",
    "digital_signature",
    "calendar",
    "email",
    "webhooks",
    "advanced_audit",
    "success_manager",
    "legal_areas",
    "monitored_processes",
)

LEGACY_ENTITLEMENT_VALUES = tuple(
    value for value in ENTITLEMENT_VALUES if value != "monitored_processes"
)

# Mesmo catálogo de tabelas fechadas por CHECK CONSTRAINT em torno de
# `entitlement_key_enum` (native_enum=False) — inclui workspace_entitlement_overrides,
# que nasceu depois de f2d4e6a8b013 e por isso não fazia parte daquela lista.
ENTITLEMENT_TABLES = (
    "plan_entitlements",
    "workspace_monthly_entitlement_usage",
    "entitlement_usage_reservations",
    "workspace_entitlement_overrides",
)

SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _entitlement_check(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"entitlement IN ({allowed})"


def _replace_entitlement_checks(values: tuple[str, ...]) -> None:
    expression = _entitlement_check(values)
    if op.get_bind().dialect.name == "sqlite":
        for table_name in ENTITLEMENT_TABLES:
            with op.batch_alter_table(
                table_name,
                naming_convention=SQLITE_NAMING_CONVENTION,
                recreate="always",
            ) as batch:
                batch.drop_constraint("entitlement_key_enum", type_="check")
                batch.create_check_constraint("entitlement_key_enum", expression)
        return

    for table_name in ENTITLEMENT_TABLES:
        op.drop_constraint("entitlement_key_enum", table_name, type_="check")
        op.create_check_constraint(
            "entitlement_key_enum",
            table_name,
            expression,
        )


def _require_no_monitored_process_entitlement_rows() -> None:
    bind = op.get_bind()
    violations = {
        table_name: int(
            bind.execute(
                sa.text(
                    f'SELECT COUNT(*) FROM "{table_name}" '
                    "WHERE entitlement = 'monitored_processes'"
                )
            ).scalar_one()
        )
        for table_name in ENTITLEMENT_TABLES
    }
    violations = {name: count for name, count in violations.items() if count}
    if violations:
        detail = ", ".join(
            f"{table_name}={count}" for table_name, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Cannot downgrade while monitored_processes entitlements are persisted: "
            + detail
        )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in RLS_TABLES:
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
    for table_name in RLS_TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    _replace_entitlement_checks(ENTITLEMENT_VALUES)

    op.create_table(
        "monitored_processes",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("numero_cnj", sa.String(length=25), nullable=False),
        sa.Column("tribunal", sa.String(length=40), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ativo", "pausado", "erro",
                name="monitored_process_status_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="ativo",
        ),
        sa.Column("oab_vinculada", sa.String(length=20), nullable=True),
        sa.Column("responsible_user_id", FlexibleUUID(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("last_movement_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_monitored_process_workspace_matter",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "numero_cnj", name="uq_monitored_process_workspace_numero_cnj"
        ),
    )
    op.create_index("ix_monitored_process_workspace_id", "monitored_processes", ["workspace_id"])
    op.create_index("ix_monitored_process_matter_id", "monitored_processes", ["matter_id"])
    op.create_index(
        "ix_monitored_process_workspace_status", "monitored_processes", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_monitored_process_responsible_user_id", "monitored_processes", ["responsible_user_id"]
    )

    op.create_table(
        "process_publications",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("monitored_process_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "fonte",
            sa.Enum(
                "djen", "datajud",
                name="process_publication_source_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("numero_cnj", sa.String(length=25), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tipo", sa.String(length=80), nullable=True),
        sa.Column("urgencia", sa.String(length=20), nullable=True),
        sa.Column("dias", sa.Integer(), nullable=True),
        sa.Column(
            "tipo_contagem",
            sa.Enum(
                "dias_uteis", "dias_corridos",
                name="process_deadline_counting_type_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("data_fatal", sa.Date(), nullable=True),
        sa.Column("memoria_calculo", FlexibleJSON(), nullable=True),
        sa.Column(
            "prazo_status",
            sa.Enum(
                "prazo_sugerido", "confirmado", "dispensado",
                name="process_deadline_status_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("confirmed_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["monitored_process_id"], ["monitored_processes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "fonte",
            "external_id",
            name="uq_process_publication_workspace_fonte_external_id",
        ),
        sa.CheckConstraint(
            "(prazo_status IS NULL) = (dias IS NULL)",
            name="ck_process_publication_prazo_requires_dias",
        ),
        sa.CheckConstraint(
            "prazo_status <> 'confirmado' OR "
            "(confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_process_publication_confirmed_requires_actor",
        ),
        sa.CheckConstraint(
            "data_fatal IS NULL OR dias IS NOT NULL",
            name="ck_process_publication_data_fatal_requires_dias",
        ),
    )
    op.create_index("ix_process_publication_workspace_id", "process_publications", ["workspace_id"])
    op.create_index("ix_process_publication_fonte", "process_publications", ["fonte"])
    op.create_index(
        "ix_process_publication_monitored_process", "process_publications", ["monitored_process_id"]
    )
    op.create_index(
        "ix_process_publication_workspace_urgencia",
        "process_publications",
        ["workspace_id", "urgencia"],
    )
    op.create_index(
        "ix_process_publication_workspace_prazo_status",
        "process_publications",
        ["workspace_id", "prazo_status"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_process_publication_workspace_prazo_status", table_name="process_publications")
    op.drop_index("ix_process_publication_workspace_urgencia", table_name="process_publications")
    op.drop_index("ix_process_publication_monitored_process", table_name="process_publications")
    op.drop_index("ix_process_publication_fonte", table_name="process_publications")
    op.drop_index("ix_process_publication_workspace_id", table_name="process_publications")
    op.drop_table("process_publications")

    op.drop_index("ix_monitored_process_responsible_user_id", table_name="monitored_processes")
    op.drop_index("ix_monitored_process_workspace_status", table_name="monitored_processes")
    op.drop_index("ix_monitored_process_matter_id", table_name="monitored_processes")
    op.drop_index("ix_monitored_process_workspace_id", table_name="monitored_processes")
    op.drop_table("monitored_processes")

    _require_no_monitored_process_entitlement_rows()
    _replace_entitlement_checks(LEGACY_ENTITLEMENT_VALUES)
    # Os enums locais desta migração nascem com native_enum=False (CHECK
    # constraint por coluna, não CREATE TYPE) — dropar a tabela já basta,
    # sem DROP TYPE residual.
