"""Policy SELECT de descoberta para os beats, ligada ao escopo `worker_discovery`.

Revision ID: e3f1a7c9d2b5
Revises: d7c1a9f4b2e8

As varreduras periódicas do Celery (campanhas, follow-ups, mensagens agendadas,
hidratação de leads, omnichannel, suporte, cobrança, recuperação de fluxos)
descobrem quais workspaces têm trabalho pendente lendo tabelas de tenant ANTES
de haver um workspace no contexto. Sob FORCE ROW LEVEL SECURITY com o predicado
fail-closed, essa leitura devolve zero linhas sem erro, e em Postgres nenhuma
varredura despachava nada. Provado no gate PostgreSQL 17 do projeto com o papel
do app (S6 do registro de defeitos).

Esta migration cria, em cada tabela lida por uma varredura, uma policy
PERMISSIVE FOR SELECT que reconhece o escopo transacional `worker_discovery`
com a ação `discovery:<família>` (`app/db/worker_discovery.py`). Só leitura:
gravar continua exigindo `app.current_workspace_id`. O mapa tabela -> famílias
abaixo é um retrato do inventário em `app/db/rls.py`
(`WORKER_DISCOVERY_TABLE_FAMILIES`); o guard de conexão compara as policies do
banco com o inventário a cada conexão, então os dois têm de andar juntos, e um
teste afirma que este retrato é igual ao inventário.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e3f1a7c9d2b5"
down_revision: str | Sequence[str] | None = "d7c1a9f4b2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "klm_worker_discovery_select"
SCOPE_SETTING = "app.current_platform_scope"
ACTION_SETTING = "app.current_platform_action"
SCOPE = "worker_discovery"

# tabela -> famílias de descoberta que podem lê-la (retrato de app/db/rls.py)
TABLE_FAMILIES: dict[str, tuple[str, ...]] = {
    "finance_charges": ("dunning",),
    "finance_entries": ("dunning",),
    "finance_recurrences": ("finance_recurrence",),
    "flow_runs": ("flow_step_recovery", "scheduled_messages"),
    "klm_external_action_intents": ("ads_feedback",),
    "klm_follow_up_tasks": ("follow_up",),
    "klm_lead_hydration_jobs": ("lead_hydration",),
    "klm_omnichannel_action_intents": ("omnichannel_action",),
    "klm_omnichannel_scheduled_messages": ("omnichannel_scheduled",),
    "scheduled_messages": ("scheduled_messages",),
    "support_notification_outbox": ("support_notification",),
    "support_tickets": ("support_lifecycle",),
    "support_transcript_emails": ("support_transcript",),
    "whatsapp_campaign_send_jobs": ("whatsapp_campaign_claims", "whatsapp_campaign_send"),
    "whatsapp_campaigns": ("whatsapp_campaign_send",),
}


def predicate(families: tuple[str, ...]) -> str:
    actions = " OR ".join(
        f"current_setting('{ACTION_SETTING}', true) = 'discovery:{family}'" for family in families
    )
    return f"current_setting('{SCOPE_SETTING}', true) = '{SCOPE}' AND ({actions})"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table_name, families in TABLE_FAMILIES.items():
        op.execute(
            f'CREATE POLICY "{POLICY_NAME}" ON "{table_name}" '
            f"AS PERMISSIVE FOR SELECT TO PUBLIC USING ({predicate(families)})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table_name in reversed(TABLE_FAMILIES):
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
