"""add legal citation verification and adversarial review findings (Onda 6, §9/§3 item 20)

Duas tabelas novas, ambas ligadas a `legal_demands` (Onda 2) e a
`klm_legal_artifact_versions` (Onda 2/5):

1. `legal_citation_verifications` — um veredito por citação (súmula/tema/
   REsp/lei) extraída do conteúdo de uma versão de artefato jurídico.
   `nao_verificada` é o default fail-closed (ver app/services/
   citation_verification_service.py) — nunca nasce como "verificada".
2. `legal_review_findings` — achados da revisão adversarial (placeholder,
   citação insegura, pedido sem fato/fundamento) sobre a mesma versão de
   artefato, com severidade; achado `critica` bloqueia protocolabilidade.

RLS direta por workspace_id nas duas tabelas novas, mesmo padrão de
`case_facts`/`case_proof_links`/`case_pleadings` (ver app/db/rls.py::
LEGAL_PRODUCTION_DIRECT_TENANT_TABLES). `legal_demands`/
`klm_legal_artifact_versions` já têm RLS própria — esta migração só
referencia, nunca recria essas políticas.

Revision ID: a3f9c1e6b204
Revises: d0f2a4c6b813
Create Date: 2026-07-27 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "a3f9c1e6b204"
down_revision: str | Sequence[str] | None = "d0f2a4c6b813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

CITATION_VERIFICATIONS_TABLE = "legal_citation_verifications"
REVIEW_FINDINGS_TABLE = "legal_review_findings"
DIRECT_TENANT_TABLES = (
    CITATION_VERIFICATIONS_TABLE,
    REVIEW_FINDINGS_TABLE,
)


def _workspace_predicate() -> str:
    return f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"


def _enable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = _workspace_predicate()
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    # ── legal_citation_verifications ─────────────────────────────────────
    op.create_table(
        CITATION_VERIFICATIONS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("artifact_version_id", FlexibleUUID(), nullable=False),
        sa.Column("citation_text", sa.String(length=500), nullable=False),
        sa.Column("citation_kind", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="nao_verificada",
        ),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("trail", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("checked_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["legal_demand_id"], ["legal_demands.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_version_id"],
            ["klm_legal_artifact_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["checked_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "artifact_version_id",
            "citation_text",
            name="uq_legal_citation_verification_artifact_text",
        ),
    )
    op.create_index(
        "ix_legal_citation_verification_workspace_id",
        CITATION_VERIFICATIONS_TABLE,
        ["workspace_id"],
    )
    op.create_index(
        "ix_legal_citation_verification_legal_demand_id",
        CITATION_VERIFICATIONS_TABLE,
        ["legal_demand_id"],
    )
    op.create_index(
        "ix_legal_citation_verification_artifact_version_id",
        CITATION_VERIFICATIONS_TABLE,
        ["artifact_version_id"],
    )
    op.create_index(
        "ix_legal_citation_verification_status",
        CITATION_VERIFICATIONS_TABLE,
        ["status"],
    )

    # ── legal_review_findings ────────────────────────────────────────────
    op.create_table(
        REVIEW_FINDINGS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("artifact_version_id", FlexibleUUID(), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["legal_demand_id"], ["legal_demands.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_version_id"],
            ["klm_legal_artifact_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_review_finding_workspace_id", REVIEW_FINDINGS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_legal_review_finding_legal_demand_id",
        REVIEW_FINDINGS_TABLE,
        ["legal_demand_id"],
    )
    op.create_index(
        "ix_legal_review_finding_artifact_version_id",
        REVIEW_FINDINGS_TABLE,
        ["artifact_version_id"],
    )
    op.create_index(
        "ix_legal_review_finding_severity", REVIEW_FINDINGS_TABLE, ["severity"]
    )

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index("ix_legal_review_finding_severity", table_name=REVIEW_FINDINGS_TABLE)
    op.drop_index(
        "ix_legal_review_finding_artifact_version_id", table_name=REVIEW_FINDINGS_TABLE
    )
    op.drop_index(
        "ix_legal_review_finding_legal_demand_id", table_name=REVIEW_FINDINGS_TABLE
    )
    op.drop_index("ix_legal_review_finding_workspace_id", table_name=REVIEW_FINDINGS_TABLE)
    op.drop_table(REVIEW_FINDINGS_TABLE)

    op.drop_index(
        "ix_legal_citation_verification_status", table_name=CITATION_VERIFICATIONS_TABLE
    )
    op.drop_index(
        "ix_legal_citation_verification_artifact_version_id",
        table_name=CITATION_VERIFICATIONS_TABLE,
    )
    op.drop_index(
        "ix_legal_citation_verification_legal_demand_id",
        table_name=CITATION_VERIFICATIONS_TABLE,
    )
    op.drop_index(
        "ix_legal_citation_verification_workspace_id",
        table_name=CITATION_VERIFICATIONS_TABLE,
    )
    op.drop_table(CITATION_VERIFICATIONS_TABLE)
