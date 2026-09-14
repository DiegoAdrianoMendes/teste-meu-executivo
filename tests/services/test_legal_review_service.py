from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.agents.knowledge.revisao_senior_protocolo import (
    REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION,
)
from app.agents.stubs.klm_revisor_senior import STATUS_BLOQUEADA, STATUS_PROTOCOLAVEL
from app.models.client import Client
from app.models.legal_artifact_verification import LegalCitationVerification
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.matter import Matter, PracticeArea
from app.models.process_monitoring import (
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
    ProcessPublicationSource,
    PublicationJourneyState,
)
from app.schemas.case_strategy import CaseFactCreate, CasePleadingCreate
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.case_strategy_service import CaseStrategyService
from app.services.citation_verification_service import extract_citations
from app.services.klm_operations_service import KlmOperationsService
from app.services.legal_review_service import LegalReviewService, find_placeholders


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Revisão")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso revisão",
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


async def _make_artifact(db_session, workspace_id, matter, user_id, *, content):
    ops = KlmOperationsService(db_session)
    artifact = await ops.create_legal_artifact_version(
        workspace_id,
        user_id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind="petition",
            title="Petição — revisão adversarial",
            content=content,
        ),
    )
    await db_session.flush()
    return artifact


async def _seed(db_session, auth_context, *, content):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, matter.id)
    artifact = await _make_artifact(
        db_session, auth_context.workspace_id, matter, auth_context.user_id, content=content
    )
    return demand, artifact


def test_find_placeholders_catches_all_named_markers() -> None:
    content = {
        "sections": [
            {
                "body": (
                    "Qualifica-se o autor [●●● PREENCHER], CPF [CPF], residente em "
                    "________, conforme XXX e o anexo [...]."
                )
            }
        ]
    }
    snippets = find_placeholders(content)
    joined = " ".join(snippets).upper()
    assert "PREENCHER" in joined
    assert any("[CPF]" == s or "CPF" in s for s in snippets)
    assert any(set(s) == {"_"} for s in snippets)
    assert any("XXX" in s.upper() for s in snippets)


async def test_review_flags_placeholder_as_critical_and_blocks_protocolability(
    db_session, auth_context
):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Qualificação", "body": "Autor: [NOME DO AUTOR]."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.is_protocolable is False
    kinds = {f.kind for f in result.findings}
    assert "placeholder" in kinds
    severities = {f.severity for f in result.findings}
    assert "critica" in severities


async def test_review_flags_unverified_citation_as_critical(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.is_protocolable is False
    citation_findings = [f for f in result.findings if f.kind == "citacao_insegura"]
    assert len(citation_findings) == 1
    assert citation_findings[0].severity == "critica"


async def test_review_does_not_flag_citation_already_verified(db_session, auth_context):
    content = {"sections": [{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}]}
    demand, artifact = await _seed(db_session, auth_context, content=content)

    [candidate] = extract_citations(content)
    db_session.add(
        LegalCitationVerification(
            workspace_id=auth_context.workspace_id,
            legal_demand_id=demand.id,
            artifact_version_id=artifact.id,
            citation_text=candidate.text,
            citation_kind=candidate.kind,
            status="verificada",
            source_reference="https://stj.jus.br/sumula/7",
            trail={},
        )
    )
    await db_session.flush()

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.is_protocolable is True
    assert not [f for f in result.findings if f.kind == "citacao_insegura"]


async def test_review_flags_pedido_sem_fato_from_case_strategy_matrix(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Fundamento", "body": "Texto sem citação."}]},
    )
    await CaseStrategyService(db_session).add_pleading(
        auth_context.workspace_id,
        demand.id,
        CasePleadingCreate(
            request_text="Condenação ao pagamento de danos morais.",
            legal_basis="Art. 186 do Código Civil.",
        ),
        actor_user_id=auth_context.user_id,
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    kinds = {f.kind for f in result.findings}
    assert "pedido_sem_fato" in kinds
    severities_by_kind = {f.kind: f.severity for f in result.findings}
    assert severities_by_kind["pedido_sem_fato"] == "alta"


async def test_review_flags_pedido_sem_fundamento(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Fundamento", "body": "Texto sem citação."}]},
    )
    strategy = CaseStrategyService(db_session)
    fact = await strategy.add_fact(
        auth_context.workspace_id,
        demand.id,
        CaseFactCreate(description="Fato incontroverso.", is_controverted=False),
        actor_user_id=auth_context.user_id,
    )
    await strategy.add_pleading(
        auth_context.workspace_id,
        demand.id,
        CasePleadingCreate(
            request_text="Condenação ao pagamento de danos morais.",
            legal_basis=None,
            supporting_fact_ids=[fact.id],
        ),
        actor_user_id=auth_context.user_id,
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    kinds = {f.kind for f in result.findings}
    assert "pedido_sem_fundamento" in kinds
    assert "pedido_sem_fato" not in kinds


async def test_review_run_replaces_previous_findings(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Qualificação", "body": "Autor: [NOME DO AUTOR]."}]},
    )
    service = LegalReviewService(db_session)

    first = await service.review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )
    assert len(first.findings) == 1

    second = await service.review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )
    assert len(second.findings) == 1
    assert {f.id for f in second.findings} != {f.id for f in first.findings}

    persisted = await service.list_findings(auth_context.workspace_id, demand.id, artifact.id)
    assert len(persisted) == 1


async def test_review_advances_journey_to_revisao_juridica_when_publication_linked(
    db_session, auth_context
):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Fatos", "body": "Nada a declarar."}]},
    )
    monitored_process = MonitoredProcess(
        workspace_id=auth_context.workspace_id,
        matter_id=demand.matter_id,
        numero_cnj="0001234-37.2026.8.07.0001",
        status=MonitoredProcessStatus.ativo,
        created_by_user_id=auth_context.user_id,
    )
    db_session.add(monitored_process)
    await db_session.flush()
    publication = ProcessPublication(
        workspace_id=auth_context.workspace_id,
        monitored_process_id=monitored_process.id,
        fonte=ProcessPublicationSource.djen,
        external_id=f"ext-{uuid4().hex[:10]}",
        numero_cnj="0001234-37.2026.8.07.0001",
        conteudo="Texto da publicação.",
        published_at=datetime.now(UTC),
        journey_state=PublicationJourneyState.minuta_em_producao,
    )
    db_session.add(publication)
    await db_session.flush()

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.journey_advanced is True
    await db_session.refresh(publication)
    assert publication.journey_state == PublicationJourneyState.revisao_juridica


async def test_review_does_not_advance_journey_without_linked_publication(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Fatos", "body": "Nada a declarar."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.journey_advanced is False


async def test_review_clean_artifact_is_protocolable_with_no_findings(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Fatos", "body": "Nada a declarar."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.is_protocolable is True
    assert result.findings == []


# ── Metodologia do Revisor Sênior (13 camadas) ──────────────────────────


async def test_review_devolve_parecer_nos_tres_niveis_e_veredito(db_session, auth_context):
    """A revisão entrega o parecer executivo do revisor sênior: sempre os 3
    níveis, e o veredito derivado deles (nunca "protocolo realizado")."""
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Qualificação", "body": "Autor: [NOME DO AUTOR]."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert set(result.parecer) == {"critico", "medio", "cosmetico"}
    assert result.parecer["critico"]
    assert result.review_status == STATUS_BLOQUEADA
    assert result.is_protocolable is False
    assert result.methodology_version == REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION


async def test_citacao_nao_verificada_entra_no_nivel_critico_do_parecer(db_session, auth_context):
    """Citação sem veredito de verificação nunca aparece como verificada:
    entra como crítica no parecer e bloqueia a entrega."""
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    criticos = " ".join(result.parecer["critico"])
    assert "Súmula 7" in criticos
    assert "nunca_conferida" in criticos
    assert result.review_status == STATUS_BLOQUEADA


async def test_artefato_limpo_sai_protocolavel_entregue_ao_humano(db_session, auth_context):
    demand, artifact = await _seed(
        db_session,
        auth_context,
        content={"sections": [{"title": "Fatos", "body": "Nada a declarar."}]},
    )

    result = await LegalReviewService(db_session).review(
        auth_context.workspace_id, demand.id, artifact.id, actor_user_id=auth_context.user_id
    )

    assert result.review_status == STATUS_PROTOCOLAVEL == "protocolavel_entregue_ao_humano"
    assert all(itens == [] for itens in result.parecer.values())
