"""S34: migrations novas precisam de lock_timeout e índice concorrente."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lint_migrations import check_migration_source, main  # noqa: E402

CABECALHO = '''
revision = "abc"
down_revision = "def"
from alembic import op
import sqlalchemy as sa
'''


def test_index_in_new_table_is_free_and_lock_timeout_not_required():
    fonte = CABECALHO + '''
def upgrade():
    op.create_table("t", sa.Column("id", sa.Integer))
    op.create_index("ix_t", "t", ["id"])

def downgrade():
    pass
'''
    assert check_migration_source(fonte) == []


def test_index_in_existing_table_requires_concurrently_and_autocommit_block():
    fonte = CABECALHO + '''
def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_index("ix_x", "existente", ["id"])

def downgrade():
    pass
'''
    achados = check_migration_source(fonte)
    assert any("sem postgresql_concurrently" in a for a in achados)

    corrigida = CABECALHO + '''
def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    with op.get_context().autocommit_block():
        op.create_index("ix_x", "existente", ["id"], postgresql_concurrently=True)

def downgrade():
    pass
'''
    assert check_migration_source(corrigida) == []


def test_lock_timeout_required_for_ddl_and_raw_sql_but_comments_do_not_count():
    fonte = CABECALHO + '''
def upgrade():
    # CREATE INDEX aqui é só comentário
    op.execute("CREATE POLICY p ON t USING (true)")

def downgrade():
    pass
'''
    achados = check_migration_source(fonte)
    assert len(achados) == 1 and "lock_timeout" in achados[0]

    marcador = "# lint-migrations: no-lock-timeout tabela recém-criada no mesmo deploy\n"
    justificada = fonte.replace("def upgrade():", marcador + "def upgrade():")
    assert check_migration_source(justificada) == []


def test_repository_migrations_after_baseline_pass():
    assert main() == 0
