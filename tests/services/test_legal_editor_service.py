"""Testes: editor jurídico — geração a partir do office_template + Caso 360
(Onda 6, §9 itens 1-2). Ver app/services/legal_editor_service.py.
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.case_party import CaseParty, CasePartyPole
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.matter import Matter, PracticeArea
from app.schemas.klm_operations import LegalArtifactGenerateRequest
from app.schemas.playbook import (
    PlaybookDocumentSection,
    PlaybookDocumentTemplate,
    PlaybookDocumentVariable,
    PlaybookDraftWrite,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.legal_editor_service import (
    LegalEditorArtifactNotFoundError,
    LegalEditorMissingMatterError,
    LegalEditorUnsupportedKindError,
    PENDING_LEGAL_DRAFTING_MARKER,
    build_case_variables,
    compute_checklist,
    generate_artifact,
    generate_section,
    recompute_checklist,
)
from app.services.playbook_service import PlaybookOfficeTemplateMissingError, PlaybookService
from tests.services.test_playbook_service import _create_data, _publish, _store_office_document


async def _make_client(db_session: AsyncSession, workspace_id, *, name: str = "Cliente Editor") -> Client:
    client = Client(workspace_id=workspace_id, name=name, cpf="123.456.789-00")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session: AsyncSession, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Editor jurídico — teste",
        case_number=f"000{uuid4().hex[:1]}222-37.2026.8.07.0001",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_demand(
    db_session: AsyncSession,
    workspace_id,
    *,
    with_matter: bool = True,
    with_opposing_party: bool = True,
    with_procuracao: bool = False,
    with_custas_paid: bool = False,
) -> LegalDemand:
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id) if with_matter else None

    demand = LegalDemand(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        # `area=None` de propósito: o editor resolve o playbook com selector
        # vazio, o que casa com qualquer playbook publicado no teste sem
        # depender do `PlaybookScope.areas` configurado em `_create_data()`.
        area=None,
        client_id=client.id,
        parties=[],
        phase=LegalDemandPhase.em_andamento,
        risk="medio",
        sla={},
        documents_required=[],
        matter_id=matter.id if matter else None,
    )
    db_session.add(demand)
    await db_session.flush()

    if with_opposing_party:
        db_session.add(
            CaseParty(
                workspace_id=workspace_id,
                legal_demand_id=demand.id,
                name="Contraparte Ltda",
                pole=CasePartyPole.passivo,
                role="réu",
                qualification={},
            )
        )

    if matter is not None and with_procuracao:
        db_session.add(
            Document(
                matter_id=matter.id,
                name="Procuração.pdf",
                doc_type=DocumentType.procuracao,
                status=DocumentStatus.completo,
            )
        )
    if matter is not None and with_custas_paid:
        db_session.add(
            FinanceEntry(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Custas processuais",
                entry_type=FinanceEntryType.expense,
                status=FinanceEntryStatus.paid,
                amount=250,
            )
        )
    await db_session.flush()
    return demand


_TEST_VARIABLE_PATTERN = re.compile(r"{{\s*([a-z][a-z0-9_.-]{1,119})\s*}}")


def _variables_for(template: PlaybookDocumentTemplate) -> list[PlaybookDocumentVariable]:
    """Registra no catálogo `document_variables` (exigido pelo validador de
    `PlaybookContent`) toda variável `{{var}}` referenciada pelo template de
    teste — o playbook nunca aceita um template referenciando variável não
    catalogada, e este helper só existe para não duplicar esse cadastro em
    cada teste."""
    rendered = "\n".join(
        [template.name] + [section.title for section in template.sections]
        + [section.body for section in template.sections]
    )
    names = set(_TEST_VARIABLE_PATTERN.findall(rendered)) | set(template.required_variables)
    return [
        PlaybookDocumentVariable(
            key=name, label=name, source="case", source_path=f"case360.{name}"
        )
        for name in sorted(names)
    ]


async def _publish_playbook_with_office_template(
    db_session: AsyncSession,
    auth_context: AuthContext,
    *,
    office_key: str = "peticao_inicial",
    document_template: PlaybookDocumentTemplate | None = None,
):
    storage_object = await _store_office_document(
        db_session, workspace_id=auth_context.workspace_id, filename=f"{office_key}-klm.docx"
    )
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    content = created.content.model_copy(deep=True)
    for template in content.office_templates:
        if template.key == office_key:
            template.document_object_id = storage_object.object_id
            template.document_object_version = storage_object.version
            template.document_filename = storage_object.original_filename
    # O playbook padrão (`_create_data()`) já traz `document_templates` do
    # vertical saúde (ex.: "petition-health-draft", status "approved") —
    # `structural_coverage` (app/services/playbook_service.py) exige que os
    # 4 tipos canônicos continuem cobertos por um template ativo+aprovado
    # para a publicação sair do `in_review`, então NUNCA removemos os
    # padrões. Quando o teste passa `document_template`, ele entra na
    # FRENTE da lista: `_resolve_document_skeleton` pega o primeiro
    # `document_type` casado, então o do teste (ainda que "draft") vence a
    # resolução do editor sem quebrar a cobertura estrutural do playbook.
    if document_template is not None:
        content.document_templates = [document_template, *content.document_templates]
        content.document_variables = list(content.document_variables) + _variables_for(
            document_template
        )

    await service.save_draft(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookDraftWrite(
            name=created.name,
            description=created.description,
            scope=created.scope,
            content=content,
            reason="Envio do modelo timbrado para o editor jurídico.",
            expected_etag=created.etag,
        ),
        expected_etag=f'"{created.etag}"',
    )
    await _publish(
        service,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
    )
    return storage_object


# ── Fail-closed: sem office_template publicado ──────────────────────────


@pytest.mark.asyncio
async def test_generate_artifact_is_fail_closed_without_office_template(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    demand = await _make_demand(db_session, auth_context.workspace_id)
    await db_session.commit()

    with pytest.raises(PlaybookOfficeTemplateMissingError, match="Envie o modelo timbrado"):
        await generate_artifact(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=LegalArtifactGenerateRequest(
                legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
            ),
        )


@pytest.mark.asyncio
async def test_generate_artifact_requires_a_matter_linked_to_the_demand(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _publish_playbook_with_office_template(db_session, auth_context)
    demand = await _make_demand(db_session, auth_context.workspace_id, with_matter=False)
    await db_session.commit()

    with pytest.raises(LegalEditorMissingMatterError):
        await generate_artifact(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=LegalArtifactGenerateRequest(
                legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
            ),
        )


@pytest.mark.asyncio
async def test_generate_artifact_rejects_unsupported_kind(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    demand = await _make_demand(db_session, auth_context.workspace_id)
    await db_session.commit()

    with pytest.raises(LegalEditorUnsupportedKindError):
        await generate_artifact(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=LegalArtifactGenerateRequest(
                legal_demand_id=demand.id, kind="client_update", title="Mensagem — teste"
            ),
        )


# ── Esqueleto interno (fallback) — testado isoladamente, sem depender do
# ciclo de vida do playbook: `structural_coverage` (Onda 12) exige que os 4
# tipos canônicos de documento sempre tenham um `document_type` ativo+
# aprovado para publicar, então nenhum workspace publicado de verdade fica
# sem NENHUM `PlaybookDocumentTemplate` para "petition"/"contract"/etc — o
# esqueleto interno só entra quando o workspace configurou um office_
# template mas ainda não configurou o CORPO daquele tipo (nenhum
# document_template com aquele document_type). Testar isso via publicação
# real do playbook é inviável (o gate de cobertura estrutural entraria em
# conflito), então o mecanismo é validado direto na função pura.


def test_fallback_skeleton_renders_variables_and_flags_pending_drafting() -> None:
    from app.services.legal_editor_service import _FALLBACK_SKELETON_BY_KIND, render_sections

    variables = {
        "cliente_nome": "Cliente Editor",
        "cliente_documento_sufixo": ", CPF 123.456.789-00",
        "parte_contraria_nome": "Contraparte Ltda",
        "tribunal_sufixo": "",
        "numero_processo_sufixo": "",
        "valor_causa_sufixo": "",
        "legal_demand_area": "trabalhista",
    }
    sections, missing = render_sections(_FALLBACK_SKELETON_BY_KIND["petition"], variables)

    assert [section.id for section in sections] == ["qualificacao", "conteudo"]
    assert "Contraparte Ltda" in sections[0].body
    assert missing == []
    assert PENDING_LEGAL_DRAFTING_MARKER in sections[1].body

    checklist = compute_checklist(
        sections=sections,
        missing_variables=missing,
        used_fallback_skeleton=True,
        documents=[],
        finance_entries=[],
        kind="petition",
        sources=[],
        citations=[],
    )
    # Mesmo com todas as variáveis preenchidas, o marcador de redação
    # jurídica pendente nunca deixa o checklist chegar a "protocolavel" —
    # é ANDAIME estrutural, não texto jurídico de verdade.
    assert checklist.has_placeholder is True
    assert checklist.protocolability_state == "bloqueado"


@pytest.mark.asyncio
async def test_generate_artifact_uses_workspace_document_template_even_with_unmatched_vocabulary(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """Sem nenhum override de teste, o playbook padrão (`_create_data()`) já
    publica um `document_template` de 'petition' — vocabulário de outro
    vertical (`{{client.name}}` etc., não `{{cliente_nome}}`). O editor usa
    o modelo do WORKSPACE (nunca ignora o que já está publicado), e como
    nenhuma dessas variáveis bate com `build_case_variables`, tudo fica
    visivelmente pendente — nunca escondido."""
    await _publish_playbook_with_office_template(db_session, auth_context)
    demand = await _make_demand(db_session, auth_context.workspace_id, with_procuracao=True)
    await db_session.commit()

    artifact = await generate_artifact(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalArtifactGenerateRequest(
            legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
        ),
    )
    await db_session.commit()

    assert artifact.legal_demand_id == demand.id
    assert artifact.office_template_key == "peticao_inicial"
    assert artifact.office_template_object_id is not None
    assert artifact.author_type == "human"
    assert artifact.content["usedFallbackSkeleton"] is False
    sections = artifact.content["sections"]
    assert [section["id"] for section in sections] == [
        "facts",
        "denial",
        "law",
        "requests",
        "review",
    ]
    assert "{{case.executive_summary}}" in sections[0]["body"]

    checklist = artifact.content["checklist"]
    assert checklist["hasPlaceholder"] is True
    assert "case.executive_summary" in checklist["missingVariables"]
    assert checklist["protocolabilityState"] == "bloqueado"
    assert artifact.protocolability_state == "bloqueado"


@pytest.mark.asyncio
async def test_generate_artifact_agent_author_requires_agent_key(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LegalArtifactGenerateRequest(
            legal_demand_id=uuid4(),
            kind="petition",
            title="Petição inicial — teste",
            author_type="agent",
        )


# ── Geração com PlaybookDocumentTemplate do próprio workspace ───────────


def _fully_resolvable_petition_template() -> PlaybookDocumentTemplate:
    return PlaybookDocumentTemplate(
        id="peticao-padrao",
        name="Petição inicial padrão",
        document_type="petition",
        sections=[
            PlaybookDocumentSection(
                id="qualificacao",
                title="Qualificação",
                body="{{cliente_nome}}{{cliente_documento_sufixo}}, em face de "
                "{{parte_contraria_nome}}.",
            ),
            PlaybookDocumentSection(
                id="pedido",
                title="Pedido",
                body="Requer-se o processamento referente a "
                "{{legal_demand_area}}{{valor_causa_sufixo}}.",
            ),
        ],
        required_variables=["cliente_nome", "legal_demand_area", "parte_contraria_nome"],
        active=True,
    )


@pytest.mark.asyncio
async def test_generate_artifact_prefers_a_prepended_workspace_document_template(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """`structural_coverage` (Onda 12) exige um `document_template` ativo+
    aprovado por tipo canônico para publicar — o template do teste entra na
    FRENTE da lista (ver `_publish_playbook_with_office_template`), então
    `_resolve_document_skeleton` o escolhe antes do padrão do vertical
    saúde, sem quebrar a cobertura estrutural do playbook publicado."""
    await _publish_playbook_with_office_template(
        db_session,
        auth_context,
        document_template=_fully_resolvable_petition_template(),
    )
    demand = await _make_demand(db_session, auth_context.workspace_id, with_procuracao=True)
    await db_session.commit()

    artifact = await generate_artifact(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalArtifactGenerateRequest(
            legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
        ),
    )
    await db_session.commit()

    assert artifact.content["usedFallbackSkeleton"] is False
    sections = artifact.content["sections"]
    assert [section["id"] for section in sections] == ["qualificacao", "pedido"]
    assert "Cliente Editor" in sections[0]["body"]
    assert "Contraparte Ltda" in sections[0]["body"]
    assert "{{" not in sections[0]["body"]
    assert "{{" not in sections[1]["body"]

    checklist = artifact.content["checklist"]
    assert checklist["hasPlaceholder"] is False
    # Modelo do workspace + tudo preenchido + procuração presente + kind
    # petition sem custas pagas → pendente (custas), nunca "protocolavel" à
    # revelia.
    assert checklist["protocolabilityState"] == "pendente"
    assert any(item["item"] == "custas" for item in checklist["pendingItems"])


@pytest.mark.asyncio
async def test_generate_artifact_reaches_protocolavel_when_nothing_is_pending(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _publish_playbook_with_office_template(
        db_session,
        auth_context,
        office_key="contrato_honorarios",
        document_template=PlaybookDocumentTemplate(
            id="contrato-padrao",
            name="Contrato de honorários padrão",
            document_type="contract",
            sections=[
                PlaybookDocumentSection(
                    id="corpo",
                    title="Corpo",
                    body="CONTRATANTE: {{cliente_nome}}{{cliente_documento_sufixo}}. "
                    "Objeto: {{legal_demand_area}}.",
                )
            ],
            required_variables=["cliente_nome", "legal_demand_area"],
            active=True,
        ),
    )
    demand = await _make_demand(db_session, auth_context.workspace_id, with_procuracao=True)
    await db_session.commit()

    artifact = await generate_artifact(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalArtifactGenerateRequest(
            legal_demand_id=demand.id, kind="contract", title="Contrato — teste"
        ),
    )
    await db_session.commit()

    # kind='contract' não passa pelo checklist de custas (só relevante para
    # 'petition') e a procuração está presente → nada pendente.
    assert artifact.content["checklist"]["protocolabilityState"] == "protocolavel"
    assert artifact.protocolability_state == "protocolavel"


# ── Regeneração de UMA seção (nunca sobrescreve) ────────────────────────


@pytest.mark.asyncio
async def test_generate_section_creates_a_new_version_without_losing_other_sections(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _publish_playbook_with_office_template(
        db_session, auth_context, document_template=_fully_resolvable_petition_template()
    )
    demand = await _make_demand(db_session, auth_context.workspace_id)
    await db_session.commit()

    v1 = await generate_artifact(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalArtifactGenerateRequest(
            legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
        ),
    )
    await db_session.commit()

    v2 = await generate_section(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        artifact_id=v1.id,
        section_id="qualificacao",
        expected_version=v1.version,
    )
    await db_session.commit()

    assert v2.version == v1.version + 1
    assert v2.artifact_key == v1.artifact_key
    assert v2.legal_demand_id == demand.id
    assert v2.office_template_key == v1.office_template_key
    sections = v2.content["sections"]
    assert [section["id"] for section in sections] == ["qualificacao", "pedido"]
    # A seção não regenerada permanece intacta.
    assert sections[1]["body"] == v1.content["sections"][1]["body"]

    # A versão anterior nunca é sobrescrita (append-only).
    reloaded_v1 = await KlmOperationsService(db_session).get_legal_artifact(
        auth_context.workspace_id, v1.id
    )
    assert reloaded_v1.content == v1.content


@pytest.mark.asyncio
async def test_generate_section_rejects_unknown_section_id(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _publish_playbook_with_office_template(
        db_session, auth_context, document_template=_fully_resolvable_petition_template()
    )
    demand = await _make_demand(db_session, auth_context.workspace_id)
    await db_session.commit()

    artifact = await generate_artifact(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalArtifactGenerateRequest(
            legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
        ),
    )
    await db_session.commit()

    with pytest.raises(LegalEditorArtifactNotFoundError):
        await generate_section(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            artifact_id=artifact.id,
            section_id="secao-inexistente",
            expected_version=artifact.version,
        )


# ── Checklist recalculado sem criar versão nova ─────────────────────────


@pytest.mark.asyncio
async def test_recompute_checklist_reflects_current_case_state_without_new_version(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _publish_playbook_with_office_template(
        db_session,
        auth_context,
        document_template=_fully_resolvable_petition_template(),
    )
    demand = await _make_demand(db_session, auth_context.workspace_id, with_procuracao=False)
    await db_session.commit()

    artifact = await generate_artifact(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=LegalArtifactGenerateRequest(
            legal_demand_id=demand.id, kind="petition", title="Petição inicial — teste"
        ),
    )
    await db_session.commit()
    assert artifact.content["checklist"]["hasProcuracao"] is False

    # Procuração chega depois da geração — o checklist recalculado reflete
    # isso sem tocar na versão persistida (imutável).
    matter_id = demand.matter_id
    db_session.add(
        Document(
            matter_id=matter_id,
            name="Procuração tardia.pdf",
            doc_type=DocumentType.procuracao,
            status=DocumentStatus.completo,
        )
    )
    await db_session.commit()

    checklist = await recompute_checklist(
        db_session, workspace_id=auth_context.workspace_id, artifact_id=artifact.id
    )
    assert checklist.has_procuracao is True

    unchanged = await KlmOperationsService(db_session).get_legal_artifact(
        auth_context.workspace_id, artifact.id
    )
    assert unchanged.content["checklist"]["hasProcuracao"] is False
    assert unchanged.protocolability_state == artifact.protocolability_state


# ── Unidade: variáveis do Caso 360 e checklist ──────────────────────────


@pytest.mark.asyncio
async def test_build_case_variables_omits_optional_data_gracefully(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    from app.services.case_360_service import Case360Service

    demand = await _make_demand(db_session, auth_context.workspace_id, with_opposing_party=False)
    await db_session.commit()

    result = await Case360Service(db_session).get_case_360(auth_context.workspace_id, demand.id)
    variables = build_case_variables(result)

    assert variables["cliente_nome"] == "Cliente Editor"
    assert variables["cliente_documento_sufixo"] == ", CPF 123.456.789-00"
    assert variables["parte_contraria_nome"] == ""
    assert variables["tribunal_sufixo"] == ""
    assert variables["numero_processo_sufixo"] == ""
    assert variables["valor_causa_sufixo"] == ""
    assert variables["responsavel_sufixo"] == ""


def test_compute_checklist_flags_uncited_citations() -> None:
    from app.services.legal_editor_service import RenderedSection

    checklist = compute_checklist(
        sections=[RenderedSection(id="corpo", title="Corpo", body="Texto final, sem pendência.")],
        missing_variables=[],
        used_fallback_skeleton=False,
        documents=[],
        finance_entries=[],
        kind="contract",
        sources=[{"label": "fonte-a"}],
        citations=[{"sourceLabel": "fonte-inexistente"}],
    )

    assert checklist.has_placeholder is False
    assert any(item["item"] == "citacoes" for item in checklist.pending_items)
    assert checklist.protocolability_state == "pendente"
