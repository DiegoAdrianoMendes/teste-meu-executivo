"""A migration d7c1a9f4b2e8 carimba só o que a plataforma gerou, e desce como no-op."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# O diretório `alembic/` da raiz sombreia o pacote quando a raiz está no sys.path;
# mesmo idioma de test_single_alembic_head.py.
_original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != ROOT]
try:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
finally:
    sys.path = _original_path

from app.services.whatsapp.template_management_service import normalize_template_name  # noqa: E402
from app.services.whatsapp.utility_template_catalog import UTILITY_TEMPLATE_CATALOG  # noqa: E402

ARQUIVO = ROOT / "alembic" / "versions" / "d7c1a9f4b2e8_backfill_intent_catalogo_utilidade.py"


def _modulo():
    spec = importlib.util.spec_from_file_location("migration_intent", ARQUIVO)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def test_migration_nova_revisa_a_head_anterior() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_revision("d7c1a9f4b2e8").down_revision == "f6a7b8c9d0e2"


def test_regex_casa_o_nome_gerado_e_a_renomeacao_mas_nao_o_clone() -> None:
    modulo = _modulo()
    catalogo = {definicao.key: definicao for definicao in UTILITY_TEMPLATE_CATALOG}
    for chave in modulo.MAPA:
        assert chave in catalogo, chave
        nome = catalogo[chave].meta_name
        padrao = re.compile(modulo.padrao_de_nome(chave))
        assert padrao.match(nome), nome
        assert padrao.match(f"{nome}_r2")  # renomeação da plataforma
        assert not padrao.match(normalize_template_name(f"{nome}_v2"))  # clone
        assert not padrao.match(f"x{nome}")
        assert not padrao.match(f"{nome}_r2_r3")


def test_downgrade_e_no_op() -> None:
    modulo = _modulo()
    modulo.downgrade()  # sem bind: não pode nem tentar tocar no banco
