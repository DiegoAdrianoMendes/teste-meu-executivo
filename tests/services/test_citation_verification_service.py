from __future__ import annotations

import pytest

from app.models.client import Client
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.matter import Matter, PracticeArea
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.citation_verification_service import (
    CitationLookupOutcome,
    CitationVerificationService,
    extract_citations,
)
from app.services.klm_operations_service import KlmOperationsService


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Verificação")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso verificação",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_demand(db_session, workspace_id, client_id, matter_id) -> LegalDemand:
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


async def _make_artifact(db_session, workspace_id, matter, user_id, *, sections):
    ops = KlmOperationsService(db_session)
    artifact = await ops.create_legal_artifact_version(
        workspace_id,
        user_id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind="petition",
            title="Petição — verificação de citações",
            content={"sections": sections},
        ),
    )
    await db_session.flush()
    return artifact


async def _seed(db_session, auth_context, *, sections):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, matter.id)
    artifact = await _make_artifact(
        db_session, auth_context.workspace_id, matter, auth_context.user_id, sections=sections
    )
    return demand, artifact


def test_extract_citations_finds_sumula_tema_resp_lei() -> None:
    content = {
        "sections": [
            {
                "title": "Do direito",
                "body": (
                    "Nos termos da Súmula 7 do STJ e do Tema 1000 do STF, o REsp "
                    "1.234.567/SP e a Lei 8.078/1990 aplicam-se ao caso."
                ),
            }
        ]
    }
    candidates = extract_citations(content)
    kinds = {c.kind for c in candidates}
    assert kinds == {"sumula", "tema", "resp", "lei"}


async def test_gate_closed_never_marks_verified(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        sections=[{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}],
    )

    class AlwaysFoundProvider:
        name = "fake_sempre_encontra"

        async def lookup(self, citation_text, citation_kind):
            return CitationLookupOutcome(found=True, source_reference="https://exemplo.com/fonte")

    # Gate fechado por padrão no ambiente de teste (KLM_ONLINE_APPROVED=False)
    # — mesmo com um provedor injetado que SEMPRE encontra, o serviço nunca
    # pode marcar como verificada.
    service = CitationVerificationService(db_session, provider=AlwaysFoundProvider())
    result = await service.verify_citations(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.gate_open is False
    assert len(result.verifications) == 1
    assert result.verifications[0].status == "nao_verificada"
    assert result.verifications[0].source_reference is None
    assert result.all_verified is False


async def test_citation_verified_with_open_gate_and_found_source(
    db_session, auth_context, monkeypatch
):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        sections=[{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}],
    )
    monkeypatch.setattr(
        "app.services.citation_verification_service.model_api_execution_allowed",
        lambda: True,
    )

    class FoundProvider:
        name = "fake_fonte_real"

        async def lookup(self, citation_text, citation_kind):
            return CitationLookupOutcome(
                found=True, source_reference="https://stj.jus.br/sumula/7", note="ok"
            )

    service = CitationVerificationService(db_session, provider=FoundProvider())
    result = await service.verify_citations(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.gate_open is True
    assert result.all_verified is True
    assert result.verifications[0].status == "verificada"
    assert result.verifications[0].source_reference == "https://stj.jus.br/sumula/7"


async def test_citation_not_found_with_open_gate_marks_nao_localizada(
    db_session, auth_context, monkeypatch
):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        sections=[{"title": "Do direito", "body": "Nos termos da Súmula 999999 do STJ."}],
    )
    monkeypatch.setattr(
        "app.services.citation_verification_service.model_api_execution_allowed",
        lambda: True,
    )

    class NotFoundProvider:
        name = "fake_nao_encontra"

        async def lookup(self, citation_text, citation_kind):
            return CitationLookupOutcome(found=False, note="Nenhuma fonte encontrada.")

    service = CitationVerificationService(db_session, provider=NotFoundProvider())
    result = await service.verify_citations(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.all_verified is False
    assert result.verifications[0].status == "nao_localizada"
    assert result.verifications[0].source_reference is None


async def test_provider_exception_never_marks_verified(db_session, auth_context, monkeypatch):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        sections=[{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}],
    )
    monkeypatch.setattr(
        "app.services.citation_verification_service.model_api_execution_allowed",
        lambda: True,
    )

    class ExplodingProvider:
        name = "fake_explode"

        async def lookup(self, citation_text, citation_kind):
            raise RuntimeError("fonte indisponível")

    service = CitationVerificationService(db_session, provider=ExplodingProvider())
    result = await service.verify_citations(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.verifications[0].status == "nao_verificada"


async def test_verify_citations_rejects_artifact_from_other_matter(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter_a = await _make_matter(db_session, auth_context.workspace_id, client.id)
    matter_b = await _make_matter(db_session, auth_context.workspace_id, client.id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, matter_a.id)
    artifact_b = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter_b,
        auth_context.user_id,
        sections=[{"title": "X", "body": "Sem citação."}],
    )

    service = CitationVerificationService(db_session)
    with pytest.raises(Exception):
        await service.verify_citations(
            auth_context.workspace_id, demand.id, artifact_b.id, actor_user_id=auth_context.user_id
        )
