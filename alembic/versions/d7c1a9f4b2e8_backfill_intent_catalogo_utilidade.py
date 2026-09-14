"""Carimba a intenção nos templates que a própria plataforma criou do catálogo.

Revision ID: d7c1a9f4b2e8
Revises: f6a7b8c9d0e2

O enum `WhatsAppTemplateIntent` é anulável de propósito, e tanto o modelo quanto
a migration que criou a coluna dizem que template vindo da Meta NÃO pode ser
classificado por backfill especulativo. Esta migration respeita isso: só toca em
linha cuja procedência é a própria plataforma.

Duas armadilhas resolvidas aqui, ambas capazes de fazer o backfill gravar zero
linhas sem erro nenhum:

1. `whatsapp_meta_templates` tem FORCE ROW LEVEL SECURITY com predicado
   fail-closed. FORCE sujeita o PRÓPRIO DONO da tabela ao predicado, e a role de
   migração é a dona (`_assert_no_foreign_application_objects` em
   `app/db/aws_database_roles.py` garante isso em AWS). Sem workspace no contexto,
   um UPDATE do dono afeta zero linhas e não levanta nada. `SET row_security = off`
   também falha para o dono sob FORCE. O único caminho sem `set_config` por
   workspace é NO FORCE dentro da transação: o DDL aqui é transacional, então
   um rollback restaura FORCE sozinho, e o FORCE explícito ao fim cobre o
   caminho feliz. É a primeira migration do repositório a fazer backfill sob
   FORCE; as anteriores que fizeram UPDATE em tabela assim afetaram zero linhas.

2. `sync_meta_templates` substitui `raw_payload` inteiro pelo payload da Meta,
   apagando o marcador `utility_catalog`. Como o webhook de status não é
   entregue enquanto o app está despublicado, o sync manual é o único caminho
   para o status `approved` chegar ao banco: as linhas aprovadas, que são
   justamente as que o seletor usa, são as mais prováveis de já ter perdido o
   marcador. Por isso o casamento é duplo, por marcador OU por nome ancorado.

O nome não é adivinhação: `meu_executivo_{chave}_v{versão}` é gerado pelo código
da plataforma, com sufixo `_r{tentativa}` na renomeação. É procedência, igual ao
marcador. A âncora exclui clone, que recebe outro sufixo (`_v2`). O catálogo só
gera `pt_BR`, e o ramo do nome exige isso, para um template alheio que ocupe o
nome padrão em outra língua não ser carimbado.

Backfill aditivo e idempotente (`intent IS NULL`): o `downgrade` é no-op de
propósito. Reverter por nome apagaria intenção escrita depois pelo operador ou
pelo clone, e a coluna só some em `wa_picker_20260819`, que já a remove.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "d7c1a9f4b2e8"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABELA = "whatsapp_meta_templates"

# Só as chaves cuja intenção é inequívoca. As outras do catálogo ficam nulas de
# propósito: nenhuma das intenções cabe sem forçar, e forçar faz o seletor
# sugerir a mensagem errada para o cliente errado.
MAPA = {
    "lembrete_audiencia": "audiencia",
    "solicitacao_documentos": "documentos",
    "retomada_atendimento": "reativacao",
}


def padrao_de_nome(chave: str) -> str:
    """Regex do nome que a plataforma gera para a chave, com a renomeação `_r{n}`."""
    return f"^meu_executivo_{chave}_v[0-9]+(_r[0-9]+)?$"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # A versão anterior do app ainda está no ar durante a migração: o ALTER
    # pega ACCESS EXCLUSIVE, e sem teto esperaria indefinidamente por um lock.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f'ALTER TABLE "{TABELA}" NO FORCE ROW LEVEL SECURITY')
    total = 0
    for chave, intencao in MAPA.items():
        resultado = bind.exec_driver_sql(
            f"""
            UPDATE "{TABELA}"
               SET intent = %(intencao)s::wa_meta_template_intent_enum
             WHERE intent IS NULL
               AND (
                    (
                        raw_payload -> 'utility_catalog' ->> 'source' = 'platform_utility_catalog'
                        AND raw_payload -> 'utility_catalog' ->> 'key' = %(chave)s
                    )
                    OR (name ~ %(padrao)s AND language = 'pt_BR')
               )
            """,
            {"intencao": intencao, "chave": chave, "padrao": padrao_de_nome(chave)},
        )
        total += resultado.rowcount or 0
    op.execute(f'ALTER TABLE "{TABELA}" FORCE ROW LEVEL SECURITY')
    logging.getLogger("alembic.runtime.migration").info(
        "[intent-catalogo] linhas carimbadas: %s", total
    )


def downgrade() -> None:
    """No-op de propósito: ver o docstring do módulo."""
