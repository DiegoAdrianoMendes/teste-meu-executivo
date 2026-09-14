"""WhatsApp 2026: identidade por BSUID e username, telefone opcional

Revision ID: b71d0e4c9f52
Revises: f2ab4c6d8e30
Create Date: 2026-07-27

Contexto
--------
Até 2026 o telefone era a identidade dentro do WhatsApp. A Meta introduziu o
BSUID (`business-scoped user ID`, campo `user_id` no webhook) e os usernames.
Verificado na documentação oficial:

- BSUIDs aparecem nos webhooks desde o início de abril de 2026, em toda
  mensagem, tenha o usuário adotado username ou não;
- o `wa_id` (telefone) passou a ser OPCIONAL: some quando o usuário adota
  username e não há interação recente nem entrada no livro de contatos;
- suportar BSUID é obrigatório para parceiros e para negócios integrados
  diretamente.

O que esta migração resolve
---------------------------
1. **P0 jurídico** — supressão e opt-in eram chaveados por telefone não-nulo.
   Cliente que oculta o número pedia descadastro e não havia onde registrar:
   falha de LGPD e de política da Meta ao mesmo tempo. Agora aceitam telefone
   OU BSUID.
2. Contato sem telefone deixa de ser impossível de criar.
3. BSUID passa a ser persistido em vez de descartado.

Todos os índices únicos viram PARCIAIS. Sem isso, dois contatos sem telefone
colidiriam no mesmo índice, e o NULL do SQL não resolve isso de forma portável
entre bancos.

O BSUID é escopado por portfólio de negócio — a conexão representa esse escopo
aqui. Chave única global seria erro de origem: o mesmo usuário tem BSUID
diferente em portfólios distintos.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b71d0e4c9f52"
down_revision: str | Sequence[str] | None = "f2ab4c6d8e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ── whatsapp_contacts ──────────────────────────────────────────────────
    op.add_column("whatsapp_contacts", sa.Column("bsuid", sa.String(length=64), nullable=True))
    op.add_column(
        "whatsapp_contacts", sa.Column("parent_bsuid", sa.String(length=64), nullable=True)
    )
    op.add_column("whatsapp_contacts", sa.Column("username", sa.String(length=64), nullable=True))
    op.create_index("ix_whatsapp_contacts_bsuid", "whatsapp_contacts", ["bsuid"])

    with op.batch_alter_table("whatsapp_contacts") as batch:
        batch.alter_column("phone", existing_type=sa.String(length=30), nullable=True)

    op.drop_index(
        "uq_whatsapp_contacts_workspace_phone_connection", table_name="whatsapp_contacts"
    )
    op.create_index(
        "uq_whatsapp_contacts_workspace_phone_connection",
        "whatsapp_contacts",
        ["workspace_id", "phone", "whatsapp_connection_id"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
        sqlite_where=sa.text("phone IS NOT NULL"),
    )
    op.create_index(
        "uq_whatsapp_contacts_workspace_bsuid_connection",
        "whatsapp_contacts",
        ["workspace_id", "bsuid", "whatsapp_connection_id"],
        unique=True,
        postgresql_where=sa.text("bsuid IS NOT NULL"),
        sqlite_where=sa.text("bsuid IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_whatsapp_contacts_tem_identificador",
        "whatsapp_contacts",
        "phone IS NOT NULL OR bsuid IS NOT NULL",
    )

    # ── whatsapp_suppression_entries (P0) ──────────────────────────────────
    op.add_column(
        "whatsapp_suppression_entries", sa.Column("bsuid", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_whatsapp_suppression_entries_bsuid", "whatsapp_suppression_entries", ["bsuid"]
    )
    with op.batch_alter_table("whatsapp_suppression_entries") as batch:
        batch.alter_column("phone", existing_type=sa.String(length=30), nullable=True)
    op.drop_constraint(
        "uq_wa_suppression_workspace_phone", "whatsapp_suppression_entries", type_="unique"
    )
    op.create_index(
        "uq_wa_suppression_workspace_phone",
        "whatsapp_suppression_entries",
        ["workspace_id", "phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
        sqlite_where=sa.text("phone IS NOT NULL"),
    )
    op.create_index(
        "uq_wa_suppression_workspace_bsuid",
        "whatsapp_suppression_entries",
        ["workspace_id", "bsuid"],
        unique=True,
        postgresql_where=sa.text("bsuid IS NOT NULL"),
        sqlite_where=sa.text("bsuid IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_wa_suppression_tem_alvo",
        "whatsapp_suppression_entries",
        "phone IS NOT NULL OR bsuid IS NOT NULL OR contact_id IS NOT NULL",
    )

    # ── whatsapp_opt_in_records ────────────────────────────────────────────
    op.add_column(
        "whatsapp_opt_in_records", sa.Column("bsuid", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_whatsapp_opt_in_records_bsuid", "whatsapp_opt_in_records", ["bsuid"])
    with op.batch_alter_table("whatsapp_opt_in_records") as batch:
        batch.alter_column("phone", existing_type=sa.String(length=30), nullable=True)
    op.drop_constraint(
        "uq_wa_optin_workspace_phone_channel", "whatsapp_opt_in_records", type_="unique"
    )
    op.create_index(
        "uq_wa_optin_workspace_phone_channel",
        "whatsapp_opt_in_records",
        ["workspace_id", "phone", "channel"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
        sqlite_where=sa.text("phone IS NOT NULL"),
    )
    op.create_index(
        "uq_wa_optin_workspace_bsuid_channel",
        "whatsapp_opt_in_records",
        ["workspace_id", "bsuid", "channel"],
        unique=True,
        postgresql_where=sa.text("bsuid IS NOT NULL"),
        sqlite_where=sa.text("bsuid IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_wa_optin_tem_alvo",
        "whatsapp_opt_in_records",
        "phone IS NOT NULL OR bsuid IS NOT NULL OR contact_id IS NOT NULL",
    )


def downgrade() -> None:
    # A volta só é possível descartando linhas que existem apenas por BSUID —
    # sem telefone elas violariam o NOT NULL restaurado. Apagar é destrutivo e
    # silencioso, então a volta REJEITA quando há dado que se perderia, em vez
    # de decidir sozinha pelo operador.
    bind = op.get_bind()
    for tabela in (
        "whatsapp_contacts",
        "whatsapp_suppression_entries",
        "whatsapp_opt_in_records",
    ):
        restantes = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {tabela} WHERE phone IS NULL")  # noqa: S608
        ).scalar_one()
        if restantes:
            raise RuntimeError(
                f"downgrade abortado: {restantes} registro(s) em {tabela} existem "
                "apenas por BSUID e seriam perdidos ao restaurar phone NOT NULL. "
                "Resolva o destino desses registros antes de reverter."
            )

    op.drop_constraint("ck_wa_optin_tem_alvo", "whatsapp_opt_in_records", type_="check")
    op.drop_index("uq_wa_optin_workspace_bsuid_channel", table_name="whatsapp_opt_in_records")
    op.drop_index("uq_wa_optin_workspace_phone_channel", table_name="whatsapp_opt_in_records")
    op.create_unique_constraint(
        "uq_wa_optin_workspace_phone_channel",
        "whatsapp_opt_in_records",
        ["workspace_id", "phone", "channel"],
    )
    with op.batch_alter_table("whatsapp_opt_in_records") as batch:
        batch.alter_column("phone", existing_type=sa.String(length=30), nullable=False)
    op.drop_index("ix_whatsapp_opt_in_records_bsuid", table_name="whatsapp_opt_in_records")
    op.drop_column("whatsapp_opt_in_records", "bsuid")

    op.drop_constraint("ck_wa_suppression_tem_alvo", "whatsapp_suppression_entries", type_="check")
    op.drop_index("uq_wa_suppression_workspace_bsuid", table_name="whatsapp_suppression_entries")
    op.drop_index("uq_wa_suppression_workspace_phone", table_name="whatsapp_suppression_entries")
    op.create_unique_constraint(
        "uq_wa_suppression_workspace_phone",
        "whatsapp_suppression_entries",
        ["workspace_id", "phone"],
    )
    with op.batch_alter_table("whatsapp_suppression_entries") as batch:
        batch.alter_column("phone", existing_type=sa.String(length=30), nullable=False)
    op.drop_index(
        "ix_whatsapp_suppression_entries_bsuid", table_name="whatsapp_suppression_entries"
    )
    op.drop_column("whatsapp_suppression_entries", "bsuid")

    op.drop_constraint("ck_whatsapp_contacts_tem_identificador", "whatsapp_contacts", type_="check")
    op.drop_index("uq_whatsapp_contacts_workspace_bsuid_connection", table_name="whatsapp_contacts")
    op.drop_index(
        "uq_whatsapp_contacts_workspace_phone_connection", table_name="whatsapp_contacts"
    )
    op.create_index(
        "uq_whatsapp_contacts_workspace_phone_connection",
        "whatsapp_contacts",
        ["workspace_id", "phone", "whatsapp_connection_id"],
        unique=True,
    )
    with op.batch_alter_table("whatsapp_contacts") as batch:
        batch.alter_column("phone", existing_type=sa.String(length=30), nullable=False)
    op.drop_index("ix_whatsapp_contacts_bsuid", table_name="whatsapp_contacts")
    op.drop_column("whatsapp_contacts", "username")
    op.drop_column("whatsapp_contacts", "parent_bsuid")
    op.drop_column("whatsapp_contacts", "bsuid")
