"""S6-B: cada varredura periódica abre o escopo de descoberta da sua família.

Em SQLite não há RLS e a leitura já passa sem escopo, então o que discrimina é o
espião: a prova de que o escopo funciona de verdade vive no harness PostgreSQL
(`tests/db/test_legal_crm_rls_postgresql.py::test_escopo_de_descoberta_...`).
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.worker_discovery import WORKER_DISCOVERY_FAMILIES

SITIOS = [
    ("app.workers.ads_feedback_tasks", "_due_workspace_ids", "ads_feedback"),
    ("app.workers.flow_tasks", "_flow_recovery_candidates", "flow_step_recovery"),
    ("app.workers.flow_tasks", "_due_scheduled_message_workspace_ids", "scheduled_messages"),
    ("app.workers.lead_hydration_tasks", "_due_workspace_ids", "lead_hydration"),
    ("app.workers.omnichannel_action_tasks", "_due_workspace_ids", "omnichannel_action"),
    ("app.workers.omnichannel_scheduled_tasks", "_due_workspace_ids", "omnichannel_scheduled"),
    ("app.workers.support_notification_tasks", "_pending_workspace_ids", "support_notification"),
    ("app.workers.support_transcript_tasks", "_pending_workspace_ids", "support_transcript"),
    ("app.workers.support_lifecycle_tasks", "_workspace_ids", "support_lifecycle"),
    (
        "app.workers.whatsapp_campaign_tasks",
        "_due_campaign_workspace_ids",
        "whatsapp_campaign_send",
    ),
    ("app.workers.follow_up_tasks", "_due_follow_up_workspace_ids", "follow_up"),
    ("app.workers.finance_tasks", "_async_finance_recurrence_tick", "finance_recurrence"),
    ("app.workers.finance_tasks", "_async_dunning_tick", "dunning"),
    (
        "app.workers.whatsapp_campaign_tasks",
        "_sweep_orphan_campaign_claims",
        "whatsapp_campaign_claims",
    ),
]


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(("modulo", "funcao", "familia"), SITIOS)
async def test_varredura_abre_o_escopo_da_sua_familia(
    db_session, monkeypatch, modulo, funcao, familia
):
    mod = importlib.import_module(modulo)
    abertos: list[str] = []

    async def espiao(session, *, family):
        abertos.append(family)

    monkeypatch.setattr(mod, "set_worker_discovery_context", espiao)
    await getattr(mod, funcao)(session_factory=_session_factory(db_session))
    assert abertos == [familia]
    assert familia in WORKER_DISCOVERY_FAMILIES


def test_toda_familia_do_inventario_tem_varredura_com_escopo() -> None:
    """Família no inventário sem varredura espiada aqui é policy instalada sem uso
    (ou varredura nova sem escopo): as duas listas têm de coincidir."""
    convertidas = {familia for _, _, familia in SITIOS}
    assert convertidas == set(WORKER_DISCOVERY_FAMILIES)
