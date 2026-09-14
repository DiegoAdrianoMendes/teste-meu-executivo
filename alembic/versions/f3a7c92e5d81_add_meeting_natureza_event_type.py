"""Add meeting natureza + event_type for the unified agenda.

Read-model `GET /agenda` (Sprint B) mixes comercial (client/funnel)
meetings with jurídico events (audiência, sustentação, prazo interno,
revisão) inside the SAME `meetings` table, so conflict detection (same
responsible person can't double-book) already works across both natures
without a UNION at query time.

CHECK constraint (`native_enum=False`), same pattern already used by
`app/models/process_monitoring.py` and by
`d3f7a9c1b2e4_add_publication_journey_state.py` — avoids `ALTER TYPE` on
PostgreSQL and needs `op.batch_alter_table(recreate="always")` on SQLite
(plain `ADD COLUMN` cannot attach a CHECK constraint there).

`natureza` defaults to `comercial` — every existing row (booking
público/interno, sempre ligado a captação de cliente) keeps its current
meaning. `event_type` defaults to `reuniao` — every existing row keeps
representing a plain meeting. Both are backward compatible, no backfill
needed beyond the server_default.

Revision ID: f3a7c92e5d81
Revises: d7e9f1a3c5b6
Create Date: 2026-07-27 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a7c92e5d81"
down_revision = "d7e9f1a3c5b6"
branch_labels = None
depends_on = None


NATUREZA_VALUES = ("comercial", "juridico")
EVENT_TYPE_VALUES = (
    "reuniao",
    "audiencia",
    "sustentacao",
    "prazo_interno",
    "prazo_fatal",
    "revisao",
    "personalizado",
)

NATUREZA_CHECK_NAME = "meeting_natureza_enum"
EVENT_TYPE_CHECK_NAME = "meeting_event_type_enum"

SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _check_expr(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "natureza",
            sa.String(length=20),
            nullable=False,
            server_default="comercial",
        ),
    )
    op.add_column(
        "meetings",
        sa.Column(
            "event_type",
            sa.String(length=20),
            nullable=False,
            server_default="reuniao",
        ),
    )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "meetings",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch:
            batch.create_check_constraint(
                NATUREZA_CHECK_NAME, _check_expr("natureza", NATUREZA_VALUES)
            )
            batch.create_check_constraint(
                EVENT_TYPE_CHECK_NAME, _check_expr("event_type", EVENT_TYPE_VALUES)
            )
    else:
        op.create_check_constraint(
            NATUREZA_CHECK_NAME, "meetings", _check_expr("natureza", NATUREZA_VALUES)
        )
        op.create_check_constraint(
            EVENT_TYPE_CHECK_NAME, "meetings", _check_expr("event_type", EVENT_TYPE_VALUES)
        )

    op.create_index(op.f("ix_meetings_natureza"), "meetings", ["natureza"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_meetings_natureza"), table_name="meetings")

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "meetings",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch:
            batch.drop_constraint(EVENT_TYPE_CHECK_NAME, type_="check")
            batch.drop_constraint(NATUREZA_CHECK_NAME, type_="check")
            batch.drop_column("event_type")
            batch.drop_column("natureza")
    else:
        op.drop_constraint(EVENT_TYPE_CHECK_NAME, "meetings", type_="check")
        op.drop_constraint(NATUREZA_CHECK_NAME, "meetings", type_="check")
        op.drop_column("meetings", "event_type")
        op.drop_column("meetings", "natureza")
