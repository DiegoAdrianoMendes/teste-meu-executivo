from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.agent_autonomy_policy import AgentAutonomyPolicy, AgentAutonomyPolicyLevel
from app.models.client import Client
from app.models.legal_batch import (
    LegalBatchCriterion,
    LegalBatchItemState,
    LegalBatchStatus,
)
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.legal_batch import LegalBatchCreate, LegalBatchCreateFromContext, LegalBatchItemSeed
from app.services.legal_batch_service import (
    LEGAL_BATCH_ITEM_EVENT_TYPE,
    LegalBatchItemNotApprovableError,
    LegalBatchRollbackUnavailableError,
    LegalBatchService,
)


async def _make_client(db_session, workspace_id, *, name: str = "Cliente Lote") -> Client:
    client = Client(workspace_id=workspace_id, name=name)
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_legal_demand(db_session, workspace_id, client_id, **overrides) -> LegalDemand:
    defaults = dict(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        area="civel",
        client_id=client_id,
        parties=[],
        phase=LegalDemandPhase.triagem,
        sla={},
        documents_required=[],
    )
    defaults.update(overrides)
    demand = LegalDemand(**defaults)
    db_session.add(demand)
    await db_session.flush()
    return demand


def _batch_payload(**overrides) -> LegalBatchCreate:
    base = dict(
        name="Lote publicações TJDFT",
        criterion_type="tribunal",
        criterion_value={"tribunal": "TJDFT"},
        items=[],
    )
    base.update(overrides)
    return LegalBatchCreate(**base)


async def test_item_state_is_individual_not_masked_by_batch_status(db_session, auth_context):
    service = LegalBatchService(db_session)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            items=[
                LegalBatchItemSeed(
                    item_ref="pub-1", action="classificar", decision_kind="classificacao"
                ),
                LegalBatchItemSeed(
                    item_ref="pub-2", action="classificar", decision_kind="classificacao"
                ),
            ]
        ),
    )
    await db_session.commit()

    items = await service.list_items(auth_context.workspace_id, batch.id)
    assert len(items) == 2
    first, second = items

    # Bloqueia só o primeiro item — o segundo tem que continuar pendente,
    # nunca herdar o estado do primeiro.
    await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=first.id,
        user_id=auth_context.user_id,
        error_reason="Publicação ilegível — falha de OCR.",
    )
    await db_session.commit()

    refreshed_first = await db_session.get(type(first), first.id)
    refreshed_second = await db_session.get(type(second), second.id)
    assert refreshed_first.state == LegalBatchItemState.bloqueado.value
    assert refreshed_first.error_reason == "Publicação ilegível — falha de OCR."
    assert refreshed_second.state == LegalBatchItemState.pendente.value

    panel = await service.get_panel(auth_context.workspace_id, batch.id)
    assert panel.counts["bloqueado"] == 1
    assert panel.counts["pendente"] == 1
    assert panel.total_items == 2
    # Um item bloqueado já é suficiente para o lote deixar de aparentar
    # "tudo certo" — status agregado reflete a pendência real.
    assert panel.status == LegalBatchStatus.com_pendencias.value


async def test_partial_failure_is_visible_not_masked(db_session, auth_context):
    service = LegalBatchService(db_session)
    client = await _make_client(db_session, auth_context.workspace_id)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            criterion_type="cliente",
            criterion_value={"clientId": str(client.id)},
            items=[
                LegalBatchItemSeed(
                    item_ref="ok-1", action="classificar", decision_kind="classificacao"
                ),
                LegalBatchItemSeed(
                    item_ref="fail-1", action="vincular", decision_kind="vinculo"
                ),
            ],
        ),
    )
    await db_session.commit()

    items = await service.list_items(auth_context.workspace_id, batch.id)
    fail_item = next(item for item in items if item.item_ref == "fail-1")

    result = await service.process_all(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        user_id=auth_context.user_id,
        items_input={
            fail_item.id: {
                "resultPayload": {"legalDemandId": "00000000-0000-0000-0000-000000000000"}
            },
        },
    )
    await db_session.commit()

    assert len(result.processed_item_ids) == 2
    assert len(result.succeeded_item_ids) == 1
    assert result.failed_item_ids == [fail_item.id]

    panel = await service.get_panel(auth_context.workspace_id, batch.id)
    assert panel.counts["bloqueado"] == 1
    # O item que deu certo NUNCA fica escondido atrás do que falhou.
    assert panel.total_items == 2
    assert panel.status == LegalBatchStatus.com_pendencias.value

    refreshed_fail = await db_session.get(type(fail_item), fail_item.id)
    assert refreshed_fail.error_reason is not None
    assert "não encontrada" in refreshed_fail.error_reason


async def test_approve_all_never_covers_protected_decision_kinds(db_session, auth_context):
    service = LegalBatchService(db_session)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            items=[
                LegalBatchItemSeed(
                    item_ref="tese-1", action="revisar", decision_kind="tese"
                ),
                LegalBatchItemSeed(
                    item_ref="prazo-1", action="revisar", decision_kind="prazo_fatal"
                ),
                LegalBatchItemSeed(
                    item_ref="texto-1", action="gerar_rascunho", decision_kind="texto_final"
                ),
                LegalBatchItemSeed(
                    item_ref="protocolo-1", action="organizar", decision_kind="protocolo"
                ),
                LegalBatchItemSeed(
                    item_ref="classif-1", action="classificar", decision_kind="classificacao"
                ),
            ]
        ),
    )
    await db_session.commit()

    items = await service.list_items(auth_context.workspace_id, batch.id)
    for item in items:
        await service.process_item(
            workspace_id=auth_context.workspace_id,
            batch_id=batch.id,
            item_id=item.id,
            user_id=auth_context.user_id,
        )
    await db_session.commit()

    # Sem política de autonomia cadastrada -> fail-closed: TODOS pararam em
    # aguardando_humano, inclusive o item não-crítico.
    items = await service.list_items(auth_context.workspace_id, batch.id)
    assert all(item.state == LegalBatchItemState.aguardando_humano.value for item in items)

    result = await service.approve_all(
        workspace_id=auth_context.workspace_id, batch_id=batch.id, user_id=auth_context.user_id
    )
    await db_session.commit()

    approved_refs = {
        item.item_ref
        for item in await service.list_items(auth_context.workspace_id, batch.id)
        if item.id in result.approved_item_ids
    }
    skipped_refs = {
        item.item_ref
        for item in await service.list_items(auth_context.workspace_id, batch.id)
        if item.id in result.skipped_protected_item_ids
    }
    assert approved_refs == {"classif-1"}
    assert skipped_refs == {"tese-1", "prazo-1", "texto-1", "protocolo-1"}

    refreshed = {item.item_ref: item.state for item in await service.list_items(
        auth_context.workspace_id, batch.id
    )}
    assert refreshed["classif-1"] == LegalBatchItemState.concluido.value
    for ref in ("tese-1", "prazo-1", "texto-1", "protocolo-1"):
        assert refreshed[ref] == LegalBatchItemState.aguardando_humano.value

    # A REGRA DURA também vale item a item: approve_item continua sendo o
    # ÚNICO caminho para os críticos.
    tese_item = next(
        item for item in await service.list_items(auth_context.workspace_id, batch.id)
        if item.item_ref == "tese-1"
    )
    approved_tese = await service.approve_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=tese_item.id,
        user_id=auth_context.user_id,
    )
    await db_session.commit()
    assert approved_tese.state == LegalBatchItemState.concluido.value


async def test_rollback_restores_previous_state_and_is_not_destructive(db_session, auth_context):
    service = LegalBatchService(db_session)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            items=[
                LegalBatchItemSeed(
                    item_ref="item-1", action="classificar", decision_kind="classificacao"
                ),
            ]
        ),
    )
    await db_session.commit()
    item = (await service.list_items(auth_context.workspace_id, batch.id))[0]
    assert item.state == LegalBatchItemState.pendente.value

    await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=item.id,
        user_id=auth_context.user_id,
    )
    await db_session.commit()
    processed = await db_session.get(type(item), item.id)
    assert processed.state == LegalBatchItemState.aguardando_humano.value
    history_len_after_process = len(processed.state_history)

    rolled_back = await service.rollback_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=item.id,
        user_id=auth_context.user_id,
        reason="Classificação equivocada — reprocessar.",
    )
    await db_session.commit()

    assert rolled_back.state == LegalBatchItemState.pendente.value
    # Nunca destrutivo: a trilha só cresce (rollback é um append), nunca é
    # truncada/reescrita.
    assert len(rolled_back.state_history) == history_len_after_process + 1
    assert rolled_back.state_history[-1]["action"] == "rollback"
    assert rolled_back.state_history[-1]["reason"] == "Classificação equivocada — reprocessar."

    # Reverter de novo, sem transição nova no meio, não tem para onde ir.
    with pytest.raises(LegalBatchRollbackUnavailableError):
        await service.rollback_item(
            workspace_id=auth_context.workspace_id,
            batch_id=batch.id,
            item_id=item.id,
            user_id=auth_context.user_id,
        )


async def test_rollback_clears_stale_approval_when_reverting_from_concluido(
    db_session, auth_context
):
    service = LegalBatchService(db_session)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            items=[
                LegalBatchItemSeed(
                    item_ref="item-1", action="classificar", decision_kind="classificacao"
                ),
            ]
        ),
    )
    await db_session.commit()
    item = (await service.list_items(auth_context.workspace_id, batch.id))[0]
    await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=item.id,
        user_id=auth_context.user_id,
    )
    await service.approve_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=item.id,
        user_id=auth_context.user_id,
    )
    await db_session.commit()
    approved = await db_session.get(type(item), item.id)
    assert approved.state == LegalBatchItemState.concluido.value
    assert approved.approved_by_user_id == auth_context.user_id

    rolled_back = await service.rollback_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=item.id,
        user_id=auth_context.user_id,
    )
    await db_session.commit()
    assert rolled_back.state == LegalBatchItemState.aguardando_humano.value
    assert rolled_back.approved_by_user_id is None
    assert rolled_back.approved_at is None


async def test_autonomy_policy_is_respected_but_never_overrides_protected_kinds(
    db_session, auth_context
):
    policy = AgentAutonomyPolicy(
        workspace_id=auth_context.workspace_id,
        name="Classificação autônoma de publicações",
        event_type=LEGAL_BATCH_ITEM_EVENT_TYPE,
        action="classificar",
        autonomy_level=AgentAutonomyPolicyLevel.A3,
        approval_required=False,
    )
    db_session.add(policy)
    await db_session.flush()

    service = LegalBatchService(db_session)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            items=[
                LegalBatchItemSeed(
                    item_ref="classif-1", action="classificar", decision_kind="classificacao"
                ),
                LegalBatchItemSeed(
                    item_ref="tese-classif-1", action="classificar", decision_kind="tese"
                ),
                LegalBatchItemSeed(
                    item_ref="vinculo-1", action="vincular", decision_kind="vinculo"
                ),
            ]
        ),
    )
    await db_session.commit()

    all_items = await service.list_items(auth_context.workspace_id, batch.id)
    items = {item.item_ref: item for item in all_items}

    non_protected = await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=items["classif-1"].id,
        user_id=auth_context.user_id,
    )
    # A3 + approval_required=False + decisão não-crítica -> avança sozinho.
    assert non_protected.state == LegalBatchItemState.classificado.value
    assert non_protected.autonomy_level_applied == AgentAutonomyPolicyLevel.A3.value
    assert non_protected.requires_human_approval is False

    protected = await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=items["tese-classif-1"].id,
        user_id=auth_context.user_id,
    )
    # MESMA política A3 — mas decision_kind=tese é protegido: nunca avança
    # sozinho, mesmo com autonomia alta liberada para a ação.
    assert protected.state == LegalBatchItemState.aguardando_humano.value
    assert protected.requires_human_approval is True

    # Ação sem política casando (vincular) cai no default fail-closed: A4.
    no_policy = await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=items["vinculo-1"].id,
        user_id=auth_context.user_id,
        result_payload={},
    )
    assert no_policy.state == LegalBatchItemState.aguardando_humano.value
    assert no_policy.autonomy_level_applied == AgentAutonomyPolicyLevel.A4.value


async def test_create_batch_is_idempotent_by_external_ref(db_session, auth_context):
    service = LegalBatchService(db_session)
    payload = _batch_payload(
        external_ref="import:djen:2026-07-27",
        items=[
            LegalBatchItemSeed(
                item_ref="pub-1", action="classificar", decision_kind="classificacao"
            )
        ],
    )
    first = await service.create_batch(
        workspace_id=auth_context.workspace_id, user_id=auth_context.user_id, payload=payload
    )
    await db_session.commit()
    second = await service.create_batch(
        workspace_id=auth_context.workspace_id, user_id=auth_context.user_id, payload=payload
    )
    await db_session.commit()

    assert first.id == second.id
    items = await service.list_items(auth_context.workspace_id, first.id)
    assert len(items) == 1


async def test_approve_item_rejects_blocking_states(db_session, auth_context):
    service = LegalBatchService(db_session)
    batch = await service.create_batch(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_batch_payload(
            items=[
                LegalBatchItemSeed(
                    item_ref="item-1", action="classificar", decision_kind="classificacao"
                ),
            ]
        ),
    )
    await db_session.commit()
    item = (await service.list_items(auth_context.workspace_id, batch.id))[0]
    await service.process_item(
        workspace_id=auth_context.workspace_id,
        batch_id=batch.id,
        item_id=item.id,
        user_id=auth_context.user_id,
        error_reason="Falha simulada.",
    )
    await db_session.commit()

    with pytest.raises(LegalBatchItemNotApprovableError):
        await service.approve_item(
            workspace_id=auth_context.workspace_id,
            batch_id=batch.id,
            item_id=item.id,
            user_id=auth_context.user_id,
        )


# ── Lote por contexto (cliente OU demanda) ──────────────────────────────


async def test_create_batch_from_client_context_brings_correct_cases(db_session, auth_context):
    service = LegalBatchService(db_session)
    client_a = await _make_client(db_session, auth_context.workspace_id, name="Cliente A")
    client_b = await _make_client(db_session, auth_context.workspace_id, name="Cliente B")
    demand_a1 = await _make_legal_demand(db_session, auth_context.workspace_id, client_a.id)
    demand_a2 = await _make_legal_demand(db_session, auth_context.workspace_id, client_a.id)
    # Demanda de outro cliente — NUNCA pode entrar no lote do cliente A.
    await _make_legal_demand(db_session, auth_context.workspace_id, client_b.id)
    await db_session.commit()

    batch = await service.create_batch_from_context(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalBatchCreateFromContext(name="Lote cliente A", client_id=client_a.id),
    )
    await db_session.commit()

    assert batch.criterion_type == LegalBatchCriterion.cliente
    assert batch.criterion_value == {"clientId": str(client_a.id)}
    items = await service.list_items(auth_context.workspace_id, batch.id)
    assert {item.legal_demand_id for item in items} == {demand_a1.id, demand_a2.id}
    assert all(item.state == LegalBatchItemState.pendente.value for item in items)


async def test_create_batch_from_legal_demand_context_brings_only_that_case(
    db_session, auth_context
):
    service = LegalBatchService(db_session)
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_legal_demand(db_session, auth_context.workspace_id, client.id)
    # Segunda demanda do MESMO cliente — não deve entrar no lote por demanda.
    await _make_legal_demand(db_session, auth_context.workspace_id, client.id)
    await db_session.commit()

    batch = await service.create_batch_from_context(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalBatchCreateFromContext(name="Lote demanda única", legal_demand_id=demand.id),
    )
    await db_session.commit()

    assert batch.criterion_value == {"legalDemandId": str(demand.id)}
    items = await service.list_items(auth_context.workspace_id, batch.id)
    assert len(items) == 1
    assert items[0].legal_demand_id == demand.id


async def test_create_batch_from_context_is_idempotent_by_external_ref(db_session, auth_context):
    service = LegalBatchService(db_session)
    client = await _make_client(db_session, auth_context.workspace_id)
    await _make_legal_demand(db_session, auth_context.workspace_id, client.id)
    await db_session.commit()

    payload = LegalBatchCreateFromContext(
        name="Lote idempotente",
        client_id=client.id,
        external_ref=f"batch-ctx-{uuid4().hex[:8]}",
    )
    first = await service.create_batch_from_context(
        workspace_id=auth_context.workspace_id, user_id=auth_context.user_id, payload=payload
    )
    await db_session.commit()
    second = await service.create_batch_from_context(
        workspace_id=auth_context.workspace_id, user_id=auth_context.user_id, payload=payload
    )
    await db_session.commit()

    assert first.id == second.id


async def test_create_batch_from_context_requires_exactly_one_of_client_or_demand() -> None:
    with pytest.raises(ValueError):
        LegalBatchCreateFromContext(name="Lote sem contexto")
    with pytest.raises(ValueError):
        LegalBatchCreateFromContext(
            name="Lote com os dois", client_id=uuid4(), legal_demand_id=uuid4()
        )


async def test_create_batch_from_context_rejects_client_from_another_workspace(
    db_session, auth_context
):
    """Mesma proteção de tenant que os demais serviços (ex.: MeetingService
    ._lock_workspace_matters): o cliente é resolvido SEMPRE escopado ao
    workspace do chamador — RLS de aplicação equivalente à RLS de banco."""
    other_workspace = Workspace(
        name="Outro escritório",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    foreign_client = await _make_client(db_session, other_workspace.id, name="Cliente Estranho")
    await db_session.commit()

    service = LegalBatchService(db_session)
    with pytest.raises(HTTPException) as excinfo:
        await service.create_batch_from_context(
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=LegalBatchCreateFromContext(
                name="Lote cross-tenant", client_id=foreign_client.id
            ),
        )
    assert excinfo.value.status_code == 404


async def test_create_batch_from_context_rejects_demand_from_another_workspace(
    db_session, auth_context
):
    other_workspace = Workspace(
        name="Outro escritório 2",
        slug=f"outro2-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    foreign_client = await _make_client(db_session, other_workspace.id, name="Cliente Estranho 2")
    foreign_demand = await _make_legal_demand(db_session, other_workspace.id, foreign_client.id)
    await db_session.commit()

    service = LegalBatchService(db_session)
    with pytest.raises(HTTPException) as excinfo:
        await service.create_batch_from_context(
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=LegalBatchCreateFromContext(
                name="Lote cross-tenant demanda", legal_demand_id=foreign_demand.id
            ),
        )
    assert excinfo.value.status_code == 404
