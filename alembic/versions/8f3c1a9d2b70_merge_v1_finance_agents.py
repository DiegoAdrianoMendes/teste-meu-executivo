"""Merge the V1 finance, agent-platform and existing release branches.

Revision ID: 8f3c1a9d2b70
Revises: 100finance_dimensional, ab12cd34ef56, e8c0a2d4f617
"""

revision = "8f3c1a9d2b70"
down_revision = (
    "100finance_dimensional",
    "ab12cd34ef56",
    "e8c0a2d4f617",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge-only revision; parent revisions contain the schema changes."""


def downgrade() -> None:
    """Alembic moves back to each parent branch without changing schema here."""
