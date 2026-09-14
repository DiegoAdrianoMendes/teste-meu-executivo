"""Add nullable intent and picker statistics indexes to WhatsApp templates."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "wa_picker_20260819"
down_revision: str | Sequence[str] | None = "zg4z7w0x1t53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

intent_enum = sa.Enum(
    "follow_up",
    "reativacao",
    "documentos",
    "audiencia",
    "cobranca",
    "proposta",
    name="wa_meta_template_intent_enum",
)


def upgrade() -> None:
    # A nullable field is intentional.  There is no safe classification for
    # templates already synchronized from Meta, so this migration never
    # backfills an intent.
    intent_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "whatsapp_meta_templates",
        sa.Column("intent", intent_enum, nullable=True),
    )
    op.add_column(
        "whatsapp_campaign_recipients",
        sa.Column(
            "template_id",
            sa.UUID(),
            sa.ForeignKey("whatsapp_meta_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "whatsapp_campaign_template_snapshots",
        sa.Column("button_parameters", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "whatsapp_campaigns",
        sa.Column("idempotency_key", sa.String(length=180), nullable=True),
    )
    op.add_column(
        "whatsapp_campaigns",
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_wa_campaigns_workspace_idempotency_key",
        "whatsapp_campaigns",
        ["workspace_id", "idempotency_key"],
    )
    op.create_index(
        "ix_whatsapp_campaign_recipients_template_id",
        "whatsapp_campaign_recipients",
        ["template_id"],
    )
    op.create_index(
        "ix_wa_campaign_recipients_workspace_template_created",
        "whatsapp_campaign_recipients",
        ["workspace_id", "template_id", "created_at"],
    )
    op.create_index(
        "ix_wa_campaign_recipients_workspace_contact_template_created",
        "whatsapp_campaign_recipients",
        ["workspace_id", "contact_id", "template_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_campaign_recipients_template_id",
        table_name="whatsapp_campaign_recipients",
    )
    op.drop_index(
        "ix_wa_campaign_recipients_workspace_contact_template_created",
        table_name="whatsapp_campaign_recipients",
    )
    op.drop_index(
        "ix_wa_campaign_recipients_workspace_template_created",
        table_name="whatsapp_campaign_recipients",
    )
    op.drop_column("whatsapp_campaign_recipients", "template_id")
    op.drop_column("whatsapp_campaign_template_snapshots", "button_parameters")
    op.drop_constraint(
        "uq_wa_campaigns_workspace_idempotency_key",
        "whatsapp_campaigns",
        type_="unique",
    )
    op.drop_column("whatsapp_campaigns", "idempotency_key")
    op.drop_column("whatsapp_campaigns", "idempotency_fingerprint")
    op.drop_column("whatsapp_meta_templates", "intent")
    intent_enum.drop(op.get_bind(), checkfirst=True)
