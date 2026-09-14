from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.matter import Matter, PracticeArea
from app.schemas.case_strategy import CaseFactCreate, CasePleadingCreate, CaseProofLinkCreate
from app.services.case_strategy_service import CaseStrategyNotFoundError, CaseStrategyService


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Estratégia")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso estratégia",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_document(db_session, matter_id) -> Document:
    document = Document(
        matter_id=matter_id,
        name="Extrato bancário.pdf",
        doc_type=DocumentType.comprovante,
        status=DocumentStatus.completo,
    )
    db_session.add(document)
    await db_session.flush()
    return document


async def _make_demand(db_session, workspace_id, client_id, matter_id=None) -> LegalDemand:
    demand = LegalDemand(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        area="civel",
        client_id=client_id,
        parties=[],
        phase=LegalDemandPhase.triagem,
        sla={},
        documents_required=[],
        matter_id=matter_id,
    )
    db_session.add(demand)
    await db_session.flush()
    return demand


async def test_add_fact_is_idempotent_by_description(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id)
    service = CaseStrategyService(db_session)

    payload = CaseFactCreate(description="O réu recebeu a notificação em 10/01/2026.")
    first = await service.add_fact(
        auth_context.workspace_id, demand.id, payload, actor_user_id=auth_context.user_id
    )
    second = await service.add_fact(
        auth_context.workspace_id, demand.id, payload, actor_user_id=auth_context.user_id
    )
    assert first.id == second.id


async def test_controverted_fact_without_proof_generates_gap_and_exception(
    db_session, auth_context
):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id)
    service = CaseStrategyService(db_session)

    fact = await service.add_fact(
        auth_context.workspace_id,
        demand.id,
        CaseFactCreate(description="Fato controvertido sem prova", is_controverted=True),
        actor_user_id=auth_context.user_id,
    )

    matrix = await service.get_matrix(auth_context.workspace_id, demand.id)
    gap_kinds = {gap.kind for gap in matrix.gaps}
    assert "fato_sem_prova" in gap_kinds
    fact_gap = next(g for g in matrix.gaps if g.kind == "fato_sem_prova")
    assert fact_gap.fact_id == fact.id
    assert fact_gap.exception_id is not None

    exception = await db_session.get(LegalException, fact_gap.exception_id)
    assert exception is not None
    assert exception.exception_kind == ExceptionKind.documento_indispensavel_ausente
    assert exception.status == ExceptionCenterStatus.aberta

    await db_session.refresh(demand)
    assert any(item.startswith(f"prova_fato:{fact.id}") for item in demand.documents_required)


async def test_adding_proof_with_tem_status_closes_the_gap(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, matter.id)
    document = await _make_document(db_session, matter.id)
    service = CaseStrategyService(db_session)

    fact = await service.add_fact(
        auth_context.workspace_id,
        demand.id,
        CaseFactCreate(description="Fato controvertido a provar", is_controverted=True),
        actor_user_id=auth_context.user_id,
    )

    matrix_before = await service.get_matrix(auth_context.workspace_id, demand.id)
    assert any(gap.kind == "fato_sem_prova" for gap in matrix_before.gaps)

    await service.add_proof(
        auth_context.workspace_id,
        demand.id,
        fact.id,
        CaseProofLinkCreate(document_id=document.id, force="robusta"),
        actor_user_id=auth_context.user_id,
    )

    matrix_after = await service.get_matrix(auth_context.workspace_id, demand.id)
    assert not any(gap.kind == "fato_sem_prova" for gap in matrix_after.gaps)
    assert len(matrix_after.proof_links) == 1
    assert matrix_after.proof_links[0].status == "tem"


async def test_proof_status_tem_requires_document(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id)
    service = CaseStrategyService(db_session)

    fact = await service.add_fact(
        auth_context.workspace_id,
        demand.id,
        CaseFactCreate(description="Fato sem documento"),
        actor_user_id=auth_context.user_id,
    )

    with pytest.raises(Exception):
        await service.add_proof(
            auth_context.workspace_id,
            demand.id,
            fact.id,
            CaseProofLinkCreate(force="mediana", status="tem"),
            actor_user_id=auth_context.user_id,
        )


async def test_pleading_without_supporting_facts_is_flagged_as_gap(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id)
    service = CaseStrategyService(db_session)

    pleading = await service.add_pleading(
        auth_context.workspace_id,
        demand.id,
        CasePleadingCreate(request_text="Condenação ao pagamento de danos morais"),
        actor_user_id=auth_context.user_id,
    )

    matrix = await service.get_matrix(auth_context.workspace_id, demand.id)
    pleading_gaps = [g for g in matrix.gaps if g.kind == "pedido_sem_fato"]
    assert len(pleading_gaps) == 1
    assert pleading_gaps[0].pleading_id == pleading.id


async def test_pleading_with_unknown_fact_id_is_rejected(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id)
    service = CaseStrategyService(db_session)

    with pytest.raises(CaseStrategyNotFoundError):
        await service.add_pleading(
            auth_context.workspace_id,
            demand.id,
            CasePleadingCreate(request_text="Pedido órfão", supporting_fact_ids=[uuid4()]),
            actor_user_id=auth_context.user_id,
        )


async def test_proof_document_from_another_matter_is_rejected(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    other_matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, matter.id)
    other_document = await _make_document(db_session, other_matter.id)
    service = CaseStrategyService(db_session)

    fact = await service.add_fact(
        auth_context.workspace_id,
        demand.id,
        CaseFactCreate(description="Fato com documento de outro caso"),
        actor_user_id=auth_context.user_id,
    )

    with pytest.raises(Exception):
        await service.add_proof(
            auth_context.workspace_id,
            demand.id,
            fact.id,
            CaseProofLinkCreate(document_id=other_document.id, force="fragil"),
            actor_user_id=auth_context.user_id,
        )
