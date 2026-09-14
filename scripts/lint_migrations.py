#!/usr/bin/env python3
"""Lint de migrations novas (S34): lock_timeout e índice concorrente.

Migrations posteriores à baseline congelada (descendentes topológicos de
BASELINE_REVISION, nunca por data ou por id) precisam:

1. declarar `lock_timeout` quando executam DDL/DML que toma lock em tabela
   existente (create_index, add/alter/drop column, foreign key, CREATE/DROP
   POLICY, UPDATE/DELETE em massa), guardado por dialeto porque a suíte roda
   as migrations em SQLite;
2. criar índice em tabela já existente só com `postgresql_concurrently=True`
   (ou `CREATE INDEX CONCURRENTLY`) dentro de `autocommit_block()`, porque o
   `env.py` roda tudo numa transação única e `CONCURRENTLY` não roda em
   transação. Índice criado no mesmo arquivo que cria a tabela é livre.

Exceções justificadas: `# lint-migrations: no-lock-timeout <motivo>` e
`# lint-migrations: allow-non-concurrent-index <motivo>` no arquivo.
Uso: `python scripts/lint_migrations.py` (exit 1 com achados).
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "f6a7b8c9d0e2"  # congelada: nunca avança com o head

_LOCKING_CALLS = re.compile(
    r"op\.(create_index|drop_index|add_column|alter_column|drop_column|create_foreign_key|"
    r"drop_constraint|create_unique_constraint|create_check_constraint)\("
)
_LOCKING_SQL = re.compile(
    r"\b(CREATE\s+INDEX|DROP\s+INDEX|ALTER\s+TABLE|CREATE\s+POLICY|DROP\s+POLICY|UPDATE|DELETE)\b",
    re.IGNORECASE,
)
_CREATE_INDEX_SQL = re.compile(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?", re.IGNORECASE)


def strip_comments(source: str) -> str:
    """Remove comentários (não docstrings) para o regex não pegar texto morto."""
    out: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                continue
            out.append(token.string if token.type not in (tokenize.NEWLINE, tokenize.NL) else "\n")
    except tokenize.TokenError:
        return source
    return " ".join(out)


def _created_tables(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(str(node.args[0].value))
    return names


def _upgrade_source(source: str) -> str:
    match = re.search(r"^def upgrade\(", source, re.M)
    if not match:
        return source
    end = re.search(r"^def downgrade\(", source[match.end() :], re.M)
    return source[match.start() : match.end() + end.start()] if end else source[match.start() :]


def check_migration_source(source: str, *, name: str = "<migration>") -> list[str]:
    """Devolve a lista de achados de um arquivo de migration."""
    findings: list[str] = []
    raw = source
    clean = strip_comments(source)
    upgrade = _upgrade_source(clean)
    tree = ast.parse(source)
    created = _created_tables(tree)
    allow_no_lock = "lint-migrations: no-lock-timeout" in raw
    allow_non_concurrent = "lint-migrations: allow-non-concurrent-index" in raw

    takes_locks = bool(_LOCKING_CALLS.search(upgrade) or _LOCKING_SQL.search(upgrade))
    if takes_locks and "lock_timeout" not in clean and not allow_no_lock:
        findings.append(
            f"{name}: upgrade toma lock em tabela existente e não declara lock_timeout "
            "(SET LOCAL lock_timeout guardado por dialeto, ou marcador justificado)."
        )

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_index"
        ):
            continue
        table = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            table = str(node.args[1].value)
        for kw in node.keywords:
            if kw.arg == "table_name" and isinstance(kw.value, ast.Constant):
                table = str(kw.value.value)
        if table in created or allow_non_concurrent:
            continue
        concurrently = any(
            kw.arg == "postgresql_concurrently"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if not concurrently:
            findings.append(
                f"{name}: op.create_index em tabela existente ({table or '?'}) sem "
                "postgresql_concurrently=True."
            )
        elif "autocommit_block" not in clean:
            findings.append(
                f"{name}: índice concorrente em ({table or '?'}) fora de autocommit_block() "
                "(o env.py roda a migration numa transação única)."
            )
    for match in _CREATE_INDEX_SQL.finditer(upgrade):
        if allow_non_concurrent:
            break
        if not match.group(2):
            findings.append(f"{name}: CREATE INDEX em SQL cru sem CONCURRENTLY.")
        elif "autocommit_block" not in clean:
            findings.append(f"{name}: CREATE INDEX CONCURRENTLY fora de autocommit_block().")
    return findings


def new_migration_files() -> list[Path]:
    # O diretório `alembic/` do repositório sombreia o pacote quando o CWD é a
    # raiz (mesma dança de tests/migrations/test_single_alembic_head.py).
    original_path = list(sys.path)
    sys.path = [item for item in sys.path if item not in {"", ".", str(ROOT)}]
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    finally:
        sys.path = original_path

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    files: list[Path] = []
    for revision in script.iterate_revisions("heads", BASELINE_REVISION, inclusive=False):
        files.append(Path(revision.path))
    return files


def main() -> int:
    findings: list[str] = []
    files = new_migration_files()
    for path in files:
        findings.extend(check_migration_source(path.read_text(encoding="utf-8"), name=path.name))
    if findings:
        print("lint_migrations: achados")
        for item in findings:
            print(f"  - {item}")
        return 1
    print(f"lint_migrations: ok ({len(files)} migration(s) depois da baseline {BASELINE_REVISION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
