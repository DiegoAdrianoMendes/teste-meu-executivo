"""add DMS jurídico — document assets, referências, compartilhamento e trilha (Onda 7, §11)

Quatro tabelas novas, todas com RLS direta por workspace_id (mesmo padrão
de `legal_citation_verifications`/`legal_review_findings`, ver
app/db/rls.py::LEGAL_DMS_DIRECT_TENANT_TABLES):

1. `document_assets` — registro CANÔNICO de um conteúdo (1 por
   `content_sha256` único por workspace), referenciando a versão física
   já gravada em `storage_objects` (Onda "fundação local" — nunca
   duplicada aqui). Carrega classificação por tipo e OCR, ambos
   fail-closed honestos (`nao_classificado`/`ocr_pendente` até existir
   IA/OCR real plugado).
2. `document_asset_references` — cada ingestão (upload manual, e-mail,
   WhatsApp, espelho de sincronização) nasce uma referência, mesmo
   quando o conteúdo já existe (dedup: 1 asset + N referências). Única
   por `(workspace_id, source, external_ref)` quando `external_ref` não
   é nulo, para ingestão automatizada idempotente.
3. `document_share_links` — link de compartilhamento com expiração
   obrigatória e watermark opcional.
4. `document_share_downloads` — trilha de quem/quando baixou via link.

Também adiciona `conflito_sincronizacao_documental` ao CHECK constraint
`exception_kind_enum` de `legal_exceptions` (Onda 2) — um espelho externo
divergente do documento canônico nunca é resolvido silenciosamente, vira
item na Central de Exceções (Plano Mestre §11 item 2). SQLite não
recebe esse ALTER porque o modelo Python (`app/models/legal_exception.py`)
já inclui o novo valor: qualquer schema criado via `Base.metadata.create_all`
(testes ORM/RLS) já nasce correto; só uma instância Postgres existente
migrando a partir de uma revisão anterior precisa do ALTER explícito.

Revision ID: 4ce67f6149cd
Revises: a3f9c1e6b204
Create Date: 2026-07-27 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "4ce67f6149cd"
down_revision: str | Sequence[str] | None = "a3f9c1e6b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

ASSETS_TABLE = "document_assets"
REFERENCES_TABLE = "document_asset_references"
SHARE_LINKS_TABLE = "document_share_links"
SHARE_DOWNLOADS_TABLE = "document_share_downloads"
DIRECT_TENANT_TABLES = (
    ASSETS_TABLE,
    REFERENCES_TABLE,
    SHARE_LINKS_TABLE,
    SHARE_DOWNLOADS_TABLE,
)

_OLD_EXCEPTION_KIND_VALUES = (
    "publicacao_sem_vinculo_confiavel",
    "homonimo",
    "prazo_ambiguo",
    "conflito_calendario",
    "alteracao_tese",
    "precedente_nao_verificado",
    "documento_indispensavel_ausente",
    "procuracao_inconsistente",
    "custas_nao_comprovadas",
    "versao_modificada_apos_aprovacao",
    "acao_externa_nao_autorizada",
)
_NEW_EXCEPTION_KIND_VALUE = "conflito_sincronizacao_documental"


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
    # ── document_assets ──────────────────────────────────────────────────
    op.create_table(
        ASSETS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("storage_object_id", FlexibleUUID(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("client_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "document_type",
            sa.String(length=32),
            nullable=False,
            server_default="nao_classificado",
        ),
        sa.Column(
            "classification_method",
            sa.String(length=32),
            nullable=False,
            server_default="indisponivel",
        ),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ocr_status",
            sa.String(length=32),
            nullable=False,
            server_default="ocr_pendente",
        ),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("extracted_metadata", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "document_type IN ("
            "'peticao_inicial','procuracao','contrato','comprovante','sentenca',"
            "'recurso','correspondencia','outro','nao_classificado')",
            name="document_asset_type_enum",
        ),
        sa.CheckConstraint(
            "classification_method IN ('ia','manual','indisponivel')",
            name="document_classification_method_enum",
        ),
        sa.CheckConstraint(
            "ocr_status IN ('nao_aplicavel','ocr_pendente','concluido','indisponivel')",
            name="document_ocr_status_enum",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "storage_object_id"],
            ["storage_objects.workspace_id", "storage_objects.id"],
            name="fk_document_asset_workspace_storage_object",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_document_asset_workspace_matter",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_document_asset_workspace_client",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "content_sha256", name="uq_document_asset_workspace_content_sha256"
        ),
    )
    op.create_index("ix_document_asset_workspace_id", ASSETS_TABLE, ["workspace_id"])
    op.create_index(
        "ix_document_asset_workspace_type", ASSETS_TABLE, ["workspace_id", "document_type"]
    )
    op.create_index(
        "ix_document_asset_workspace_matter", ASSETS_TABLE, ["workspace_id", "matter_id"]
    )

    # ── document_asset_references ───────────────────────────────────────
    op.create_table(
        REFERENCES_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("document_asset_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("client_id", FlexibleUUID(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source IN ('upload_manual','email','whatsapp','sync_mirror')",
            name="document_asset_source_enum",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_asset_id"], [f"{ASSETS_TABLE}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_asset_reference_workspace_id", REFERENCES_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_document_asset_reference_asset", REFERENCES_TABLE, ["workspace_id", "document_asset_id"]
    )
    op.create_index(
        "uq_document_asset_reference_source_external",
        REFERENCES_TABLE,
        ["workspace_id", "source", "external_ref"],
        unique=True,
        postgresql_where=sa.text("external_ref IS NOT NULL"),
        sqlite_where=sa.text("external_ref IS NOT NULL"),
    )

    # ── document_share_links ────────────────────────────────────────────
    op.create_table(
        SHARE_LINKS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("document_asset_id", FlexibleUUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "watermark_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("max_downloads", sa.Integer(), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["document_asset_id"], [f"{ASSETS_TABLE}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_document_share_link_token_hash"),
    )
    op.create_index(
        "ix_document_share_link_workspace_id", SHARE_LINKS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_document_share_link_workspace_asset",
        SHARE_LINKS_TABLE,
        ["workspace_id", "document_asset_id"],
    )

    # ── document_share_downloads ────────────────────────────────────────
    op.create_table(
        SHARE_DOWNLOADS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("share_link_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("downloaded_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("downloader_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["share_link_id"], [f"{SHARE_LINKS_TABLE}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["downloaded_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_share_download_workspace_id", SHARE_DOWNLOADS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_document_share_download_link",
        SHARE_DOWNLOADS_TABLE,
        ["workspace_id", "share_link_id"],
    )

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)

    # ── legal_exceptions.exception_kind ganha o novo valor ──────────────
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("exception_kind_enum", "legal_exceptions", type_="check")
        op.create_check_constraint(
            "exception_kind_enum",
            "legal_exceptions",
            "exception_kind IN ("
            + ", ".join(
                f"'{value}'"
                for value in (*_OLD_EXCEPTION_KIND_VALUES, _NEW_EXCEPTION_KIND_VALUE)
            )
            + ")",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("exception_kind_enum", "legal_exceptions", type_="check")
        op.create_check_constraint(
            "exception_kind_enum",
            "legal_exceptions",
            "exception_kind IN ("
            + ", ".join(f"'{value}'" for value in _OLD_EXCEPTION_KIND_VALUES)
            + ")",
        )

    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index(
        "ix_document_share_download_link", table_name=SHARE_DOWNLOADS_TABLE
    )
    op.drop_index(
        "ix_document_share_download_workspace_id", table_name=SHARE_DOWNLOADS_TABLE
    )
    op.drop_table(SHARE_DOWNLOADS_TABLE)

    op.drop_index(
        "ix_document_share_link_workspace_asset", table_name=SHARE_LINKS_TABLE
    )
    op.drop_index("ix_document_share_link_workspace_id", table_name=SHARE_LINKS_TABLE)
    op.drop_table(SHARE_LINKS_TABLE)

    op.drop_index(
        "uq_document_asset_reference_source_external", table_name=REFERENCES_TABLE
    )
    op.drop_index("ix_document_asset_reference_asset", table_name=REFERENCES_TABLE)
    op.drop_index(
        "ix_document_asset_reference_workspace_id", table_name=REFERENCES_TABLE
    )
    op.drop_table(REFERENCES_TABLE)

    op.drop_index("ix_document_asset_workspace_matter", table_name=ASSETS_TABLE)
    op.drop_index("ix_document_asset_workspace_type", table_name=ASSETS_TABLE)
    op.drop_index("ix_document_asset_workspace_id", table_name=ASSETS_TABLE)
    op.drop_table(ASSETS_TABLE)
