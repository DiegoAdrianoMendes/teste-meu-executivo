from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.client import Client
from app.models.legal_demand import LegalDemandNature, LegalDemandPhase
from app.models.matter import Matter, PracticeArea
from app.schemas.legal_demand import LegalDemandCreate, LegalDemandUpdate
from app.services.legal_demand_service import LegalDemandService


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Demanda Jurídica")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso operacional existente",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


# ── As 7 naturezas criam com os campos certos ────────────────────────────


@pytest.mark.asyncio
async def test_judicial_requires_area(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(nature=LegalDemandNature.judicial, client_id=client.id),
        )
    assert exc_info.value.status_code == 422

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.judicial, client_id=client.id, area="trabalhista"
        ),
    )
    assert demand.nature == LegalDemandNature.judicial
    assert demand.area == "trabalhista"
    assert demand.phase == LegalDemandPhase.triagem


@pytest.mark.asyncio
async def test_administrativo_requires_area(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(nature=LegalDemandNature.administrativo, client_id=client.id),
        )
    assert exc_info.value.status_code == 422

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.administrativo, client_id=client.id, area="tributario"
        ),
    )
    assert demand.nature == LegalDemandNature.administrativo


@pytest.mark.asyncio
async def test_consultivo_requires_area(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.consultivo, client_id=client.id, area="societario"
        ),
    )
    assert demand.nature == LegalDemandNature.consultivo


@pytest.mark.asyncio
async def test_contrato_requires_contract_ref(db_session, auth_context):
    from app.models.contract_billing import ContractBillingProposal

    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    proposal = ContractBillingProposal(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        items=[],
    )
    db_session.add(proposal)
    await db_session.flush()

    service = LegalDemandService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(nature=LegalDemandNature.contrato, client_id=client.id),
        )
    assert exc_info.value.status_code == 422

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.contrato,
            client_id=client.id,
            contract_ref=proposal.id,
        ),
    )
    assert demand.contract_ref == proposal.id
    assert demand.contract_proposal.id == proposal.id


@pytest.mark.asyncio
async def test_acordo_requires_value(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(nature=LegalDemandNature.acordo, client_id=client.id),
        )
    assert exc_info.value.status_code == 422

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.acordo, client_id=client.id, value=Decimal("15000.00")
        ),
    )
    assert demand.value == Decimal("15000.00")


@pytest.mark.asyncio
async def test_servico_recorrente_requires_sla(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(nature=LegalDemandNature.servico_recorrente, client_id=client.id),
        )
    assert exc_info.value.status_code == 422

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.servico_recorrente,
            client_id=client.id,
            sla={"targetDays": 5},
        ),
    )
    assert demand.sla == {"targetDays": 5}


@pytest.mark.asyncio
async def test_massificado_requires_documents_required(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(nature=LegalDemandNature.massificado, client_id=client.id),
        )
    assert exc_info.value.status_code == 422

    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.massificado,
            client_id=client.id,
            documents_required=["procuracao", "contrato_social"],
        ),
    )
    assert demand.documents_required == ["procuracao", "contrato_social"]


# ── Vínculo a matter existente não duplica dado ──────────────────────────


@pytest.mark.asyncio
async def test_linking_existing_matter_does_not_duplicate_data(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    original_demand_type = matter.demand_type

    service = LegalDemandService(db_session)
    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.judicial,
            client_id=client.id,
            area="civel",
            matter_id=matter.id,
        ),
    )

    assert demand.matter_id == matter.id
    # A demanda não copia nenhum campo de Matter — só referencia.
    assert not hasattr(demand, "demand_type")
    await db_session.refresh(matter)
    assert matter.demand_type == original_demand_type


@pytest.mark.asyncio
async def test_create_rejects_matter_from_another_workspace(db_session, auth_context):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    other_workspace = Workspace(
        name="Outro Workspace",
        slug="outro-workspace-legal-demand",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    other_client = await _make_client(db_session, other_workspace.id)
    other_matter = await _make_matter(db_session, other_workspace.id, other_client.id)

    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            auth_context.workspace_id,
            LegalDemandCreate(
                nature=LegalDemandNature.judicial,
                client_id=client.id,
                area="civel",
                matter_id=other_matter.id,
            ),
        )
    assert exc_info.value.status_code == 404


# ── Idempotência ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_is_idempotent_by_external_ref(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)

    first = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.consultivo,
            client_id=client.id,
            area="tributario",
            external_ref="import-batch-42",
        ),
    )
    second = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.consultivo,
            client_id=client.id,
            area="tributario-diferente",
            external_ref="import-batch-42",
        ),
    )
    assert first.id == second.id
    assert second.area == "tributario"  # não sobrescreveu — devolveu o existente


@pytest.mark.asyncio
async def test_transition_phase_is_idempotent_by_value(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)
    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.consultivo, client_id=client.id, area="tributario"
        ),
    )
    assert demand.phase == LegalDemandPhase.triagem

    same_phase = await service.transition_phase(
        auth_context.workspace_id, demand.id, LegalDemandPhase.triagem
    )
    assert same_phase.phase == LegalDemandPhase.triagem

    advanced = await service.transition_phase(
        auth_context.workspace_id, demand.id, LegalDemandPhase.em_andamento
    )
    assert advanced.phase == LegalDemandPhase.em_andamento


@pytest.mark.asyncio
async def test_update_does_not_change_nature_or_phase(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    service = LegalDemandService(db_session)
    demand = await service.create(
        auth_context.workspace_id,
        LegalDemandCreate(
            nature=LegalDemandNature.consultivo, client_id=client.id, area="tributario"
        ),
    )

    updated = await service.update(
        auth_context.workspace_id,
        demand.id,
        LegalDemandUpdate(strategy="Nova estratégia definida em reunião."),
    )
    assert updated.nature == LegalDemandNature.consultivo
    assert updated.phase == LegalDemandPhase.triagem
    assert updated.strategy == "Nova estratégia definida em reunião."
