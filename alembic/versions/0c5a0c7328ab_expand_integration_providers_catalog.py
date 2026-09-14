"""expand integration providers (frente B — catálogo de integrações)

Revision ID: 0c5a0c7328ab
Revises: 5914cb063c3b
Create Date: 2026-07-25 18:00:00.000000

Acrescenta ao enum nativo ``integration_provider_enum`` os providers do
catálogo de automação, ads, tracking e canais (frente B): pluga, zapier,
google_ads, google_tag_manager, ga4, meta_ads, meta_messenger,
meta_instagram, whatsapp_cloud. Apenas expande os valores aceitos — nenhuma
tabela nova, nenhum dado migrado.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0c5a0c7328ab"
down_revision: Union[str, None] = "5914cb063c3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_PROVIDERS = (
    "pluga",
    "zapier",
    "google_ads",
    "google_tag_manager",
    "ga4",
    "meta_ads",
    "meta_messenger",
    "meta_instagram",
    "whatsapp_cloud",
)


def upgrade() -> None:
    for provider in NEW_PROVIDERS:
        op.execute(f"ALTER TYPE integration_provider_enum ADD VALUE IF NOT EXISTS '{provider}'")


def downgrade() -> None:
    # PostgreSQL enums do not support DROP VALUE directly.
    # Downgrade is intentionally a no-op to avoid destructive enum recreation.
    pass
