"""Contexto transacional de DESCOBERTA para os beats do Celery.

As varreduras periódicas (campanhas, follow-ups, mensagens agendadas, hidratação
de leads, omnichannel, suporte, cobrança, recuperação de fluxos) precisam achar
quais workspaces têm trabalho pendente ANTES de haver um workspace no contexto.
Elas faziam isso lendo tabelas de tenant numa sessão sem contexto. Sob FORCE ROW
LEVEL SECURITY com predicado fail-closed, essa leitura devolve zero linhas sem
erro, o beat despacha nada e se registra como sucesso. Em Postgres, nenhuma
varredura periódica rodava.

O mecanismo é o mesmo do SAC da plataforma (`app/db/platform_support.py`): um
escopo nomeado, transacional, reconhecido por uma policy própria, só de SELECT.
A sessão de descoberta consegue LER as tabelas da sua família e nada mais: não
grava, não vê tabelas de outras famílias, e o contexto some no fim da transação.
O trabalho de verdade continua na task por workspace, sob `set_workspace_context`.

Toda família aqui tem de existir também em `app/db/rls.py`
(`WORKER_DISCOVERY_TABLE_FAMILIES`) e na migration que criou as policies: o
guard de conexão compara as policies do banco com o inventário a cada conexão e
recusa a plataforma inteira se divergirem. É fail-closed de propósito.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_support import PLATFORM_ACTION_SETTING, PLATFORM_SCOPE_SETTING

WORKER_DISCOVERY_SCOPE = "worker_discovery"

# família -> tabelas que a varredura de descoberta dessa família lê.
WORKER_DISCOVERY_FAMILIES: dict[str, tuple[str, ...]] = {
    "ads_feedback": ("klm_external_action_intents",),
    "dunning": ("finance_charges", "finance_entries"),
    "finance_recurrence": ("finance_recurrences",),
    "flow_step_recovery": ("flow_runs",),
    "scheduled_messages": ("scheduled_messages", "flow_runs"),
    "follow_up": ("klm_follow_up_tasks",),
    "lead_hydration": ("klm_lead_hydration_jobs",),
    "omnichannel_action": ("klm_omnichannel_action_intents",),
    "omnichannel_scheduled": ("klm_omnichannel_scheduled_messages",),
    "support_notification": ("support_notification_outbox",),
    "support_transcript": ("support_transcript_emails",),
    "support_lifecycle": ("support_tickets",),
    "whatsapp_campaign_send": ("whatsapp_campaign_send_jobs", "whatsapp_campaigns"),
    "whatsapp_campaign_claims": ("whatsapp_campaign_send_jobs",),
}


def discovery_action(family: str) -> str:
    return f"discovery:{family}"


def worker_discovery_table_families() -> dict[str, tuple[str, ...]]:
    """Inverte o mapa: tabela -> famílias que podem descobri-la, em ordem estável."""
    tabelas: dict[str, list[str]] = {}
    for family, tables in WORKER_DISCOVERY_FAMILIES.items():
        for table in tables:
            tabelas.setdefault(table, []).append(family)
    return {table: tuple(sorted(families)) for table, families in sorted(tabelas.items())}


_SET_CONTEXT_SQL = text(
    f"""
    SELECT set_config('{PLATFORM_SCOPE_SETTING}', :scope, true),
           set_config('{PLATFORM_ACTION_SETTING}', :action, true)
    """
)


class WorkerDiscoveryContextError(RuntimeError):
    """Família de descoberta que não existe no inventário."""


async def set_worker_discovery_context(session: AsyncSession, *, family: str) -> None:
    """Abre, só nesta transação, a leitura de descoberta da família.

    No SQLite (testes) não há RLS e a chamada é no-op, como `set_workspace_context`.
    """
    if family not in WORKER_DISCOVERY_FAMILIES:
        raise WorkerDiscoveryContextError(f"família de descoberta desconhecida: {family}")
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await session.execute(
        _SET_CONTEXT_SQL,
        {"scope": WORKER_DISCOVERY_SCOPE, "action": discovery_action(family)},
    )


__all__ = [
    "WORKER_DISCOVERY_FAMILIES",
    "WORKER_DISCOVERY_SCOPE",
    "WorkerDiscoveryContextError",
    "discovery_action",
    "set_worker_discovery_context",
    "worker_discovery_table_families",
]
