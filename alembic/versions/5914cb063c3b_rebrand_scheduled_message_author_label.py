"""rebrand scheduled message author_label default

Rebrand KLM -> Meu Executivo: o server_default da coluna
`klm_omnichannel_scheduled_messages.author_label` estava fixado em
"Agendamento KLM" no banco. Alterar só o default no modelo Python não
muda o DEFAULT já compilado na DDL, então esta migration realinha o
server_default ao novo valor. Aditiva e reversível; não toca linhas
existentes.

Revision ID: 5914cb063c3b
Revises: 0d2e2d15c0a9
Create Date: 2026-07-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5914cb063c3b"
down_revision: str | Sequence[str] | None = "0d2e2d15c0a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "klm_omnichannel_scheduled_messages"
_COLUMN = "author_label"
_OLD_DEFAULT = "Agendamento KLM"
_NEW_DEFAULT = "Agendamento Meu Executivo"


def upgrade() -> None:
    op.alter_column(
        _TABLE,
        _COLUMN,
        server_default=_NEW_DEFAULT,
        existing_type=sa.String(length=180),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        _TABLE,
        _COLUMN,
        server_default=_OLD_DEFAULT,
        existing_type=sa.String(length=180),
        existing_nullable=False,
    )
