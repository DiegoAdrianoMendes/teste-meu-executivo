"""Service: editor jurídico — geração a partir do modelo do escritório +
variáveis do Caso 360 (Onda 6, §9 itens 1-2).

REUSA (não recria):
  * `KlmOperationsService.create_legal_artifact_version`/`LegalArtifactVersion`
    (Onda 2/5, app/models/klm_operations.py e app/services/klm_operations_service.py)
    — toda versão gerada aqui é uma `LegalArtifactVersion` normal, imutável,
    com o mesmo `evidence_sha256` e a mesma trava de concorrência
    (`expected_version`); este módulo nunca insere linha fora desse
    caminho.
  * `PlaybookService.resolve_office_template` (Onda 12,
    app/services/playbook_service.py) — resolução fail-closed do modelo
    timbrado do escritório. Sem modelo publicado para o tipo, a geração
    PARA com `PlaybookOfficeTemplateMissingError`; nunca cai num modelo
    genérico.
  * `Case360Service.get_case_360` (Onda 5, app/services/case_360_service.py)
    — variáveis do caso vêm do mesmo agregado usado pela tela Caso 360, não
    de uma consulta paralela.

Limitação conhecida desta rodada (documentada, não escondida):
  `LegalArtifactVersion.matter_id` continua NOT NULL (FK, Onda 2) — uma
  `LegalDemand` sem `Matter` operacional vinculado não consegue gerar
  artefato ainda. `generate_artifact` falha explicitamente
  (`LegalEditorMissingMatterError`) nesse caso, em vez de inventar um
  Matter ou deixar a FK estourar um erro genérico do banco.

Esqueleto de seções (§9 item 2, "gerar seção ou artefato integral"):
  Quando o workspace já publicou um `PlaybookDocumentTemplate` (Onda 12,
  `document_type` igual ao `kind`) no playbook efetivo, este serviço usa as
  seções e `required_variables` DESSE modelo — dado do próprio escritório,
  não inventado aqui. Só quando não há nenhum configurado, cai num
  esqueleto mínimo interno (`_FALLBACK_SKELETON_BY_KIND`), sempre marcado
  `used_fallback_skeleton=True` e com marcador explícito de redação
  jurídica pendente — isto é ANDAIME estrutural para provar o mecanismo de
  geração, não texto jurídico de verdade (isso é trabalho dos agentes da
  matéria, `01-…` a `59-…`, revisados por `58 revisor-senior-protocolo`,
  fora do escopo deste serviço de infraestrutura).

Checklist de protocolabilidade (§9 item 2, "placeholder, procuração,
custas, citações — marca pendências, não protocola"): nunca promove uma
versão a `protocolavel` sozinho; só sinaliza. Implementa a regra global do
escritório (nunca entregar com placeholder) na origem: toda variável
opcional ausente é omitida via os `..._sufixo` derivados (string vazia, sem
vírgula/frase pendurada) em vez de deixar `[CAMPO]`; só variáveis
OBRIGATÓRIAS ausentes ficam como `{{var}}` visível e entram em
`missing_variables`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_party import CasePartyPole
from app.models.document import DocumentStatus, DocumentType
from app.models.finance import FinanceEntryStatus
from app.models.klm_operations import LegalArtifactVersion
from app.models.playbook import PlaybookScopeDimension
from app.schemas.klm_operations import (
    LEGAL_ARTIFACT_OFFICE_TEMPLATE_KINDS,
    LegalArtifactGenerateRequest,
    LegalArtifactVersionCreate,
)
from app.services.case_360_service import Case360NotFoundError, Case360Result, Case360Service
from app.services.klm_operations_service import KlmOperationsService
from app.services.playbook_service import PlaybookService

_VARIABLE_PATTERN = re.compile(r"{{\s*([a-z][a-z0-9_.-]{1,119})\s*}}")

PENDING_LEGAL_DRAFTING_MARKER = "[REDAÇÃO JURÍDICA PENDENTE]"

# LegalArtifactKind (schemas/klm_operations.py) usa exatamente os mesmos
# literais de PlaybookDocumentTemplate.document_type para os 4 tipos
# geráveis pelo editor — não é coincidência, é o mesmo vocabulário de
# domínio; só o office_template (modelo timbrado, Onda 12) usa chave em
# português, daí este mapa.
KIND_TO_OFFICE_TEMPLATE_KEY: dict[str, str] = {
    "proposal": "proposta",
    "contract": "contrato_honorarios",
    "power_of_attorney": "procuracao",
    "petition": "peticao_inicial",
}


class LegalEditorUnsupportedKindError(ValueError):
    """`kind` não é um documento timbrado gerável pelo editor (ex.: 'client_update' é
    mensagem, não documento)."""


class LegalEditorMissingMatterError(ValueError):
    """A LegalDemand não tem Matter (caso operacional) vinculado — ver limitação
    documentada no topo deste módulo."""


class LegalEditorArtifactNotFoundError(LookupError):
    """artifact_id/section_id informado não existe neste workspace."""


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    body_template: str
    required_variables: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedSection:
    id: str
    title: str
    body: str


@dataclass(frozen=True)
class ChecklistResult:
    has_placeholder: bool
    missing_variables: list[str]
    has_procuracao: bool
    has_custas_pagas: Optional[bool]
    pending_items: list[dict[str, str]]
    protocolability_state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "hasPlaceholder": self.has_placeholder,
            "missingVariables": self.missing_variables,
            "hasProcuracao": self.has_procuracao,
            "hasCustasPagas": self.has_custas_pagas,
            "pendingItems": self.pending_items,
            "protocolabilityState": self.protocolability_state,
        }


@dataclass(frozen=True)
class GeneratedArtifactPreview:
    """Resultado puro (sem persistência) da geração — usado tanto para
    montar o payload que vai persistido quanto para uma futura pré-visualização
    no editor."""

    sections: list[RenderedSection]
    variables: dict[str, str]
    used_fallback_skeleton: bool
    checklist: ChecklistResult


# ── Esqueleto mínimo (só usado sem PlaybookDocumentTemplate configurado) ──
_FALLBACK_SKELETON_BY_KIND: dict[str, list[SectionSpec]] = {
    "petition": [
        SectionSpec(
            "qualificacao",
            "Qualificação",
            "{{cliente_nome}}{{cliente_documento_sufixo}}, em face de "
            "{{parte_contraria_nome}}{{tribunal_sufixo}}{{numero_processo_sufixo}}.",
            required_variables=("cliente_nome", "parte_contraria_nome"),
        ),
        SectionSpec(
            "conteudo",
            "Dos fatos e do direito",
            f"{PENDING_LEGAL_DRAFTING_MARKER} Caso de {{{{legal_demand_area}}}}"
            "{{valor_causa_sufixo}}.",
            required_variables=("legal_demand_area",),
        ),
    ],
    "power_of_attorney": [
        SectionSpec(
            "outorgante",
            "Outorgante",
            "{{cliente_nome}}{{cliente_documento_sufixo}}.",
            required_variables=("cliente_nome",),
        ),
        SectionSpec(
            "conteudo",
            "Poderes outorgados",
            f"{PENDING_LEGAL_DRAFTING_MARKER} Poderes para o foro em geral referentes a "
            "{{legal_demand_area}}.",
            required_variables=("legal_demand_area",),
        ),
    ],
    "contract": [
        SectionSpec(
            "partes",
            "Partes",
            "CONTRATANTE: {{cliente_nome}}{{cliente_documento_sufixo}}.",
            required_variables=("cliente_nome",),
        ),
        SectionSpec(
            "conteudo",
            "Objeto e honorários",
            f"{PENDING_LEGAL_DRAFTING_MARKER} Prestação de serviços jurídicos em "
            "{{legal_demand_area}}{{valor_causa_sufixo}}.",
            required_variables=("legal_demand_area",),
        ),
    ],
    "proposal": [
        SectionSpec(
            "destinatario",
            "Destinatário",
            "{{cliente_nome}}{{cliente_documento_sufixo}}.",
            required_variables=("cliente_nome",),
        ),
        SectionSpec(
            "conteudo",
            "Escopo da proposta",
            f"{PENDING_LEGAL_DRAFTING_MARKER} Atuação em {{{{legal_demand_area}}}}"
            "{{valor_causa_sufixo}}.",
            required_variables=("legal_demand_area",),
        ),
    ],
}


def _fmt_currency(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"R$ {float(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except (TypeError, ValueError):
        return None


def build_case_variables(result: Case360Result) -> dict[str, str]:
    """Achata o agregado Caso 360 num dicionário `{{var}}` → string.

    Regra global do escritório (nunca placeholder em final): toda variável
    OPCIONAL ausente entra como uma variante `..._sufixo` já formatada
    (vírgula/frase incluída) que resolve para string vazia quando o dado
    não existe — nunca deixa `, CPF ` pendurado. Só as variáveis de base
    (`cliente_nome`, `legal_demand_area`) são "obrigatórias": se ausentes,
    ficam de fora do dict e o `{{var}}` correspondente permanece visível no
    corpo, entrando em `missing_variables`.
    """
    variables: dict[str, str] = {}

    variables["cliente_nome"] = result.client.name
    if result.client.cpf:
        variables["cliente_documento_sufixo"] = f", CPF {result.client.cpf}"
    else:
        variables["cliente_documento_sufixo"] = ""

    area_label = result.demand.area or result.demand.nature.value
    if area_label:
        variables["legal_demand_area"] = area_label

    opposing_party = next(
        (party for party in result.case_parties if party.pole == CasePartyPole.passivo),
        None,
    ) or next(
        (party for party in result.case_parties if party.pole != CasePartyPole.ativo),
        None,
    )
    variables["parte_contraria_nome"] = opposing_party.name if opposing_party else ""

    first_process = result.processes[0] if result.processes else None
    variables["tribunal_sufixo"] = (
        f" ({first_process.tribunal})" if first_process and first_process.tribunal else ""
    )
    variables["numero_processo_sufixo"] = (
        f", processo {first_process.numero_cnj}" if first_process else ""
    )

    valor_fmt = _fmt_currency(result.demand.value)
    variables["valor_causa_sufixo"] = f", no valor de {valor_fmt}" if valor_fmt else ""

    variables["responsavel_sufixo"] = (
        f", sob responsabilidade de {result.responsible_user.name}"
        if result.responsible_user
        else ""
    )

    return variables


def _render_body(template: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    missing: list[str] = []

    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables:
            return variables[name]
        missing.append(name)
        return match.group(0)

    rendered = _VARIABLE_PATTERN.sub(_substitute, template)
    return rendered, missing


def render_sections(
    skeleton: list[SectionSpec], variables: dict[str, str]
) -> tuple[list[RenderedSection], list[str]]:
    rendered_sections: list[RenderedSection] = []
    all_missing: list[str] = []
    for spec in skeleton:
        body, missing = _render_body(spec.body_template, variables)
        all_missing.extend(name for name in missing if name not in all_missing)
        rendered_sections.append(RenderedSection(id=spec.id, title=spec.title, body=body))
    return rendered_sections, all_missing


def compute_checklist(
    *,
    sections: list[RenderedSection],
    missing_variables: list[str],
    used_fallback_skeleton: bool,
    documents: list[Any],
    finance_entries: list[Any],
    kind: str,
    sources: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> ChecklistResult:
    has_placeholder = bool(missing_variables) or any(
        _VARIABLE_PATTERN.search(section.body) or PENDING_LEGAL_DRAFTING_MARKER in section.body
        for section in sections
    )
    has_procuracao = any(
        getattr(document, "doc_type", None) == DocumentType.procuracao
        and getattr(document, "status", None) == DocumentStatus.completo
        for document in documents
    )
    has_custas_pagas: Optional[bool] = None
    if kind == "petition":
        has_custas_pagas = any(
            "custas" in (getattr(entry, "title", "") or "").lower()
            or "custas" in [str(tag).lower() for tag in (getattr(entry, "tags", None) or [])]
            for entry in finance_entries
            if getattr(entry, "status", None) == FinanceEntryStatus.paid
        )

    pending_items: list[dict[str, str]] = []
    if has_placeholder:
        pending_items.append(
            {
                "item": "placeholder",
                "detail": (
                    "Há redação jurídica pendente ou variável obrigatória sem valor "
                    "(esqueleto interno)" if used_fallback_skeleton else
                    "Há variável obrigatória sem valor no conteúdo gerado."
                ),
            }
        )
    if not has_procuracao:
        pending_items.append(
            {
                "item": "procuracao",
                "detail": "Procuração não encontrada (ou não validada) nos documentos do caso.",
            }
        )
    if has_custas_pagas is False:
        pending_items.append(
            {
                "item": "custas",
                "detail": "Nenhum lançamento financeiro de custas pago foi encontrado para o caso.",
            }
        )
    citation_labels = {str(source.get("label") or source.get("id") or "") for source in sources}
    uncited = [
        citation
        for citation in citations
        if str(citation.get("sourceLabel") or citation.get("source") or "") not in citation_labels
    ]
    if uncited:
        pending_items.append(
            {
                "item": "citacoes",
                "detail": f"{len(uncited)} citação(ões) sem fonte correspondente em `sources`.",
            }
        )

    if has_placeholder:
        state = "bloqueado"
    elif pending_items:
        state = "pendente"
    else:
        state = "protocolavel"

    return ChecklistResult(
        has_placeholder=has_placeholder,
        missing_variables=missing_variables,
        has_procuracao=has_procuracao,
        has_custas_pagas=has_custas_pagas,
        pending_items=pending_items,
        protocolability_state=state,
    )


async def _resolve_document_skeleton(
    playbook_service: PlaybookService,
    *,
    workspace_id: UUID,
    kind: str,
    area: Optional[str],
) -> tuple[list[SectionSpec], bool]:
    """Usa o `PlaybookDocumentTemplate` do próprio workspace quando existir
    (mesmo `document_type` do `kind`); cai no esqueleto interno só quando não
    há nenhum publicado — sempre sinalizado (`used_fallback_skeleton`)."""
    try:
        effective = await playbook_service.effective(
            workspace_id=workspace_id,
            selectors={PlaybookScopeDimension.area: area} if area else {},
        )
    except Exception:  # noqa: BLE001 — PlaybookNotFoundError ou similar: cai no fallback
        effective = None

    if effective is not None:
        for template in effective.content.document_templates:
            if template.document_type == kind and template.active:
                specs = [
                    SectionSpec(
                        id=section.id,
                        title=section.title,
                        body_template=section.body,
                        required_variables=tuple(template.required_variables),
                    )
                    for section in template.sections
                ]
                if specs:
                    return specs, False

    return list(_FALLBACK_SKELETON_BY_KIND[kind]), True


async def generate_artifact(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    payload: LegalArtifactGenerateRequest,
) -> LegalArtifactVersion:
    """Gera o artefato integral a partir do office_template do escritório
    (Onda 12) preenchendo variáveis do Caso 360 (Onda 5) — §9 item 2.

    Fail-closed: sem office_template publicado para `kind`, propaga
    `PlaybookOfficeTemplateMissingError` (nunca cai num modelo genérico).
    Sem Matter vinculado à LegalDemand, levanta
    `LegalEditorMissingMatterError` (limitação documentada no topo deste
    módulo). Nunca marca `protocolavel` sozinho — sempre roda o checklist e
    grava o estado resultante.
    """
    if payload.kind not in LEGAL_ARTIFACT_OFFICE_TEMPLATE_KINDS:
        raise LegalEditorUnsupportedKindError(
            f"'{payload.kind}' não é um documento timbrado gerável pelo editor jurídico."
        )

    case_360 = Case360Service(db)
    try:
        result = await case_360.get_case_360(workspace_id, payload.legal_demand_id)
    except Case360NotFoundError as exc:
        raise LookupError("Demanda jurídica (Caso 360) não encontrada no workspace.") from exc

    if result.matter is None:
        raise LegalEditorMissingMatterError(
            "Esta demanda não tem um Caso operacional (Matter) vinculado; vincule um "
            "Matter à LegalDemand antes de gerar artefatos pelo editor jurídico."
        )

    office_template_key = payload.office_template_key or KIND_TO_OFFICE_TEMPLATE_KEY[payload.kind]
    playbook_service = PlaybookService(db)
    office_template = await playbook_service.resolve_office_template(
        workspace_id=workspace_id,
        key=office_template_key,  # type: ignore[arg-type]
        selectors={PlaybookScopeDimension.area: result.demand.area}
        if result.demand.area
        else {},
    )

    skeleton, used_fallback_skeleton = await _resolve_document_skeleton(
        playbook_service,
        workspace_id=workspace_id,
        kind=payload.kind,
        area=result.demand.area,
    )

    variables = build_case_variables(result)
    rendered_sections, missing_variables = render_sections(skeleton, variables)

    checklist = compute_checklist(
        sections=rendered_sections,
        missing_variables=missing_variables,
        used_fallback_skeleton=used_fallback_skeleton,
        documents=result.documents,
        finance_entries=result.finance_entries,
        kind=payload.kind,
        sources=[],
        citations=[],
    )

    content: dict[str, Any] = {
        "sections": [
            {"id": section.id, "title": section.title, "body": section.body}
            for section in rendered_sections
        ],
        "variables": variables,
        "sources": [],
        "citations": [],
        "comments": [],
        "checklist": checklist.as_dict(),
        "usedFallbackSkeleton": used_fallback_skeleton,
    }

    ops = KlmOperationsService(db)
    create_payload = LegalArtifactVersionCreate(
        matter_id=result.matter.id,
        kind=payload.kind,
        title=payload.title,
        content=content,
        artifact_key=payload.artifact_key,
        expected_version=payload.expected_version,
        legal_demand_id=payload.legal_demand_id,
        author_type=payload.author_type,
        author_agent_key=payload.author_agent_key,
        office_template_key=office_template.key,
        office_template_object_id=office_template.document_object_id,
        office_template_object_version=office_template.document_object_version,
        protocolability_state=checklist.protocolability_state,
    )
    return await ops.create_legal_artifact_version(workspace_id, user_id, create_payload)


async def recompute_checklist(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    artifact_id: UUID,
) -> ChecklistResult:
    """Recalcula o checklist de protocolabilidade da versão MAIS RECENTE
    já persistida, sem criar versão nova (§9 item 3, ação `checklist`).

    Usa o Caso 360 atual (documentos/financeiro podem ter mudado desde a
    geração) contra o conteúdo já gravado — nunca reescreve `content`
    (imutável); só recalcula o resumo e, se ele girar, isso fica visível na
    resposta (a persistência do novo `protocolability_state` acontece só na
    PRÓXIMA versão gerada/reimportada, mantendo a versão atual imutável).
    """
    ops = KlmOperationsService(db)
    artifact = await ops.get_legal_artifact(workspace_id, artifact_id)
    if artifact is None:
        raise LegalEditorArtifactNotFoundError("Documento jurídico não encontrado no workspace.")

    sections = [
        RenderedSection(id=s.get("id", ""), title=s.get("title", ""), body=s.get("body", ""))
        for s in (artifact.content or {}).get("sections") or []
    ]
    missing_variables = sorted(
        {name for section in sections for name in _VARIABLE_PATTERN.findall(section.body)}
    )

    documents: list[Any] = []
    finance_entries: list[Any] = []
    if artifact.legal_demand_id is not None:
        try:
            result = await Case360Service(db).get_case_360(workspace_id, artifact.legal_demand_id)
        except Case360NotFoundError:
            result = None
        if result is not None:
            documents = result.documents
            finance_entries = result.finance_entries

    return compute_checklist(
        sections=sections,
        missing_variables=missing_variables,
        used_fallback_skeleton=bool((artifact.content or {}).get("usedFallbackSkeleton")),
        documents=documents,
        finance_entries=finance_entries,
        kind=artifact.kind,
        sources=(artifact.content or {}).get("sources") or [],
        citations=(artifact.content or {}).get("citations") or [],
    )


async def generate_section(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    artifact_id: UUID,
    section_id: str,
    expected_version: int,
) -> LegalArtifactVersion:
    """Regenera UMA seção de uma versão existente a partir das variáveis do
    Caso 360 atuais (§9 item 2, "gerar seção") — NUNCA sobrescreve: sempre
    cria uma versão nova com a mesma trava de concorrência
    (`expected_version`) já usada pelo round-trip `.docx`.

    As demais seções/variáveis/fontes/citações/comentários da versão são
    preservados; o checklist é recalculado sobre o documento inteiro (não só
    a seção regenerada), porque protocolabilidade é uma propriedade do
    documento, não de uma seção isolada.
    """
    ops = KlmOperationsService(db)
    latest = await ops.get_legal_artifact(workspace_id, artifact_id)
    if latest is None:
        raise LegalEditorArtifactNotFoundError("Documento jurídico não encontrado no workspace.")
    if latest.legal_demand_id is None:
        raise LegalEditorArtifactNotFoundError(
            "Este documento não está vinculado a uma LegalDemand; gere-o pelo editor "
            "jurídico primeiro (generate_artifact) antes de regenerar uma seção."
        )
    if latest.kind not in LEGAL_ARTIFACT_OFFICE_TEMPLATE_KINDS:
        raise LegalEditorUnsupportedKindError(
            f"'{latest.kind}' não é um documento gerável pelo editor jurídico."
        )

    case_360 = Case360Service(db)
    try:
        result = await case_360.get_case_360(workspace_id, latest.legal_demand_id)
    except Case360NotFoundError as exc:
        raise LookupError("Demanda jurídica (Caso 360) não encontrada no workspace.") from exc

    skeleton, used_fallback_skeleton = await _resolve_document_skeleton(
        PlaybookService(db),
        workspace_id=workspace_id,
        kind=latest.kind,
        area=result.demand.area,
    )
    spec = next((candidate for candidate in skeleton if candidate.id == section_id), None)
    if spec is None:
        raise LegalEditorArtifactNotFoundError(
            f"Seção '{section_id}' não existe no modelo de '{latest.kind}'."
        )

    variables = build_case_variables(result)
    new_body, _section_missing = _render_body(spec.body_template, variables)

    existing_sections = list((latest.content or {}).get("sections") or [])
    updated_sections: list[dict[str, Any]] = []
    replaced = False
    for existing in existing_sections:
        if existing.get("id") == section_id:
            updated_sections.append({"id": spec.id, "title": spec.title, "body": new_body})
            replaced = True
        else:
            updated_sections.append(existing)
    if not replaced:
        updated_sections.append({"id": spec.id, "title": spec.title, "body": new_body})

    rendered_all = [
        RenderedSection(id=s.get("id", ""), title=s.get("title", ""), body=s.get("body", ""))
        for s in updated_sections
    ]
    missing_all = sorted(
        {name for section in rendered_all for name in _VARIABLE_PATTERN.findall(section.body)}
    )
    checklist = compute_checklist(
        sections=rendered_all,
        missing_variables=missing_all,
        used_fallback_skeleton=used_fallback_skeleton
        or bool((latest.content or {}).get("usedFallbackSkeleton")),
        documents=result.documents,
        finance_entries=result.finance_entries,
        kind=latest.kind,
        sources=(latest.content or {}).get("sources") or [],
        citations=(latest.content or {}).get("citations") or [],
    )

    new_content: dict[str, Any] = dict(latest.content or {})
    new_content["sections"] = updated_sections
    new_content["variables"] = variables
    new_content["checklist"] = checklist.as_dict()

    create_payload = LegalArtifactVersionCreate(
        matter_id=latest.matter_id,
        kind=latest.kind,
        title=latest.title,
        content=new_content,
        artifact_key=latest.artifact_key,
        expected_version=expected_version,
        legal_demand_id=latest.legal_demand_id,
        author_type=latest.author_type,
        author_agent_key=latest.author_agent_key,
        office_template_key=latest.office_template_key,
        office_template_object_id=latest.office_template_object_id,
        office_template_object_version=latest.office_template_object_version,
        protocolability_state=checklist.protocolability_state,
    )
    return await ops.create_legal_artifact_version(workspace_id, user_id, create_payload)
