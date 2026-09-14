"""
Service: LegalReviewService — revisão adversarial de artefato jurídico
(Onda 6, §9/§3 item 20).

Cruza o `content` de uma `LegalArtifactVersion` com:

  1. placeholder — mesma varredura da regra crítica global do usuário
     (`feedback_nunca_entregar_com_placeholder`): `[●●● PREENCHER]`,
     `[CPF]`, `XXX`, `[...]`, `____` nunca podem sobreviver num artefato
     "pronto". Achado sempre `critica`.
  2. citação insegura — cruza com `LegalCitationVerification` (Onda 6,
     app/services/citation_verification_service.py): toda citação
     extraída do conteúdo que não esteja com status 'verificada' (inclusive
     a que nunca foi conferida) é um achado `critica`.
  3. pedido sem fato de sustentação — reusa
     `CaseStrategyService.get_matrix` (Onda 5, Plano Mestre §3 item 8) em
     vez de reimplementar a lacuna; achado `alta`.
  4. pedido sem fundamento — `CasePleading.legal_basis` vazio é o sinal
     concreto de "afirmação sem fonte" dentro da própria matriz fato-
     prova-pedido (em vez de heurística livre sobre texto corrido); achado
     `media`.

Metodologia (Revisor Sênior de Protocolo): a base
`app/agents/knowledge/revisao_senior_protocolo.py` e o agente
`app/agents/stubs/klm_revisor_senior.py` (encarnação do `revisor_juridico`
do registro) definem as 13 camadas da revisão de banca e o parecer
executivo nos 3 níveis. Este serviço é o ponto de entrada determinístico
dessa metodologia: as severidades técnicas dos achados são agrupadas por
`parecer_por_nivel` (crítico / médio / cosmético) e o veredito binário sai
de `veredito_status`. "protocolável" aqui significa exclusivamente
"entregue ao humano com checklist" — o protocolo é humano (ver
`app/services/protocol_folder_service.py`) e nenhum agente o executa.

Um achado `critica` bloqueia protocolabilidade: `is_protocolable` (no
resultado e no achado persistido) fica `False` e `ApprovalService.resolve`
(Onda 2) rejeita aprovar o `legal_artifact:*` correspondente. Este serviço
deliberadamente NÃO escreve em `LegalArtifactVersion.protocolability_state`
— a tabela é `register_append_only_model` (Onda 2) e todo UPDATE via ORM é
rejeitado por `reject_mutation` (app/db/append_only.py); essa coluna,
adicionada por outra frente (migração d0f2a4c6b813, sem serviço próprio
ainda), herda a mesma trava e por isso não serve como gate mutável. O
veredito vive nas linhas de `LegalReviewFinding` (tabela própria, mutável)
e é consultado sob demanda.

Cada chamada SUBSTITUI o lote de achados anterior para a mesma
`artifact_version_id` — a revisão é sempre sobre o estado ATUAL do
artefato + da matriz, nunca soma achado obsoleto.

Registro na jornada (Onda 4, Plano Mestre §6): quando a demanda tem um
`MonitoredProcess`/`ProcessPublication` vinculado e a jornada está em
'minuta_em_producao', a revisão tenta avançá-la para 'revisao_juridica'
via `LegalJourneyService` (mesma porta única de avanço, nunca escreve
`journey_state` direto). É best-effort: nem toda demanda tem uma
publicação-jornada associada (consultivo/contrato não têm), e qualquer
erro de domínio da jornada é absorvido — a revisão do artefato nunca falha
por causa de um avanço de jornada opcional.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.knowledge.revisao_senior_protocolo import (
    CAMADAS_REVISAO,
    REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION,
)
from app.agents.stubs.klm_revisor_senior import (
    STATUS_BLOQUEADA,
    parecer_por_nivel,
    veredito_status,
)
from app.models.klm_operations import KlmAuditEvent, LegalArtifactVersion
from app.models.legal_artifact_verification import (
    CitationVerificationStatus,
    LegalCitationVerification,
    LegalReviewFinding,
    LegalReviewFindingKind,
    LegalReviewSeverity,
)
from app.models.legal_demand import LegalDemand
from app.models.process_monitoring import (
    MonitoredProcess,
    ProcessPublication,
    PublicationJourneyState,
)
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.case_strategy_service import CaseStrategyService
from app.services.citation_verification_service import extract_citations, iter_text_blocks
from app.services.legal_journey_service import (
    JourneyActorType,
    LegalJourneyError,
    LegalJourneyService,
)

REVIEW_AUDIT_ENTITY_TYPE = "legal_review_finding"

_CRITICAL_SEVERITIES = frozenset({LegalReviewSeverity.critica.value})

_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"PREENCHER", re.IGNORECASE),
    re.compile(r"\[[A-Za-zÀ-ÿ ]{2,40}\]"),
    re.compile(r"_{4,}"),
    re.compile(r"\bXXX+\b"),
    re.compile(r"\[\s*\.\.\.\s*\]"),
)


def find_placeholders(content: object) -> list[str]:
    """Varredura por regex das marcas de preenchimento da regra crítica
    global — nunca por heurística de linguagem natural."""
    seen: dict[str, str] = {}
    for text_block in iter_text_blocks(content):
        for pattern in _PLACEHOLDER_PATTERNS:
            for match in pattern.finditer(text_block):
                raw = match.group(0).strip()
                key = raw.lower()
                if key not in seen:
                    seen[key] = raw
    return list(seen.values())


class LegalReviewNotFoundError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@dataclass(frozen=True)
class LegalReviewResult:
    artifact_version_id: UUID
    findings: list[LegalReviewFinding] = field(default_factory=list)
    is_protocolable: bool = False
    journey_advanced: bool = False
    # Parecer executivo do revisor sênior nos 3 níveis (crítico / médio /
    # cosmético) — derivado das severidades pela metodologia embutida em
    # app/agents/knowledge/revisao_senior_protocolo.py, nunca por texto livre.
    parecer: dict[str, list[str]] = field(default_factory=dict)
    # Veredito binário do revisor: protocolável (entregue ao humano com
    # checklist) ou bloqueada. NUNCA significa protocolo realizado — o
    # protocolo é exclusivamente humano (ver ProtocolFolderService).
    review_status: str = STATUS_BLOQUEADA
    methodology_version: str = REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION


class LegalReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Leitura auxiliar escopada por workspace ─────────────────────────

    async def _require_demand(self, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == demand_id,
                LegalDemand.workspace_id == workspace_id,
            )
        )
        if demand is None:
            raise LegalReviewNotFoundError("Demanda jurídica não encontrada.")
        return demand

    async def _require_artifact(
        self, workspace_id: UUID, demand: LegalDemand, artifact_id: UUID
    ) -> LegalArtifactVersion:
        artifact = await self.db.scalar(
            select(LegalArtifactVersion).where(
                LegalArtifactVersion.id == artifact_id,
                LegalArtifactVersion.workspace_id == workspace_id,
            )
        )
        if artifact is None:
            raise LegalReviewNotFoundError("Documento jurídico não encontrado no workspace.")
        if demand.matter_id is None or artifact.matter_id != demand.matter_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Documento pertence a um caso diferente do vinculado a esta demanda.",
            )
        return artifact

    async def list_findings(
        self, workspace_id: UUID, demand_id: UUID, artifact_id: UUID
    ) -> list[LegalReviewFinding]:
        demand = await self._require_demand(workspace_id, demand_id)
        artifact = await self._require_artifact(workspace_id, demand, artifact_id)
        result = await self.db.scalars(
            select(LegalReviewFinding)
            .where(
                LegalReviewFinding.workspace_id == workspace_id,
                LegalReviewFinding.artifact_version_id == artifact.id,
            )
            .order_by(LegalReviewFinding.created_at.asc())
        )
        return list(result.all())

    # ── Revisão adversarial ──────────────────────────────────────────────

    async def review(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        artifact_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> LegalReviewResult:
        demand = await self._require_demand(workspace_id, demand_id)
        artifact = await self._require_artifact(workspace_id, demand, artifact_id)

        findings: list[tuple[str, str, str, dict]] = []

        for snippet in find_placeholders(artifact.content):
            findings.append(
                (
                    LegalReviewSeverity.critica.value,
                    LegalReviewFindingKind.placeholder.value,
                    f"Marcador de preenchimento encontrado no artefato: '{snippet}'.",
                    {"snippet": snippet},
                )
            )

        citation_rows = list(
            await self.db.scalars(
                select(LegalCitationVerification).where(
                    LegalCitationVerification.workspace_id == workspace_id,
                    LegalCitationVerification.artifact_version_id == artifact.id,
                )
            )
        )
        verified_texts = {
            row.citation_text
            for row in citation_rows
            if row.status == CitationVerificationStatus.verificada.value
        }
        unsafe_by_text = {
            row.citation_text: row
            for row in citation_rows
            if row.status != CitationVerificationStatus.verificada.value
        }
        for candidate in extract_citations(artifact.content):
            if candidate.text in verified_texts:
                continue
            existing = unsafe_by_text.get(candidate.text)
            citation_status = existing.status if existing else "nunca_conferida"
            findings.append(
                (
                    LegalReviewSeverity.critica.value,
                    LegalReviewFindingKind.citacao_insegura.value,
                    f"Citação '{candidate.text}' está com status '{citation_status}' "
                    "— não pode sustentar a peça sem conferência na fonte.",
                    {
                        "citationText": candidate.text,
                        "citationId": str(existing.id) if existing else None,
                        "status": citation_status,
                    },
                )
            )

        matrix = await CaseStrategyService(self.db).get_matrix(workspace_id, demand_id)
        for gap in matrix.gaps:
            if gap.kind == "pedido_sem_fato":
                findings.append(
                    (
                        LegalReviewSeverity.alta.value,
                        LegalReviewFindingKind.pedido_sem_fato.value,
                        gap.description,
                        {"pleadingId": str(gap.pleading_id) if gap.pleading_id else None},
                    )
                )
        for pleading in matrix.pleadings:
            if not (pleading.legal_basis or "").strip():
                findings.append(
                    (
                        LegalReviewSeverity.media.value,
                        LegalReviewFindingKind.pedido_sem_fundamento.value,
                        "Pedido sem fundamento jurídico informado: "
                        f"{pleading.request_text[:200]}",
                        {"pleadingId": str(pleading.id)},
                    )
                )

        await self.db.execute(
            delete(LegalReviewFinding).where(
                LegalReviewFinding.workspace_id == workspace_id,
                LegalReviewFinding.artifact_version_id == artifact.id,
            )
        )

        finding_rows = [
            LegalReviewFinding(
                workspace_id=workspace_id,
                legal_demand_id=demand.id,
                artifact_version_id=artifact.id,
                severity=severity,
                kind=kind,
                description=description,
                reference=reference,
                created_by_user_id=actor_user_id,
            )
            for severity, kind, description, reference in findings
        ]
        self.db.add_all(finding_rows)
        await self.db.flush()

        is_protocolable = not any(row.severity in _CRITICAL_SEVERITIES for row in finding_rows)

        # Metodologia do revisor sênior (13 camadas): a severidade técnica de
        # cada achado vira parecer executivo nos 3 níveis, e o veredito é
        # derivado do parecer — nunca declarado à mão. "protocolável" aqui
        # significa apenas "entregue ao humano com checklist"; protocolo é
        # exclusivamente humano.
        parecer = parecer_por_nivel(finding_rows)
        review_status = veredito_status(parecer)

        journey_advanced = await self._register_journey_checkpoint(
            workspace_id, demand, actor_user_id
        )

        actor_label = (
            await resolve_human_actor_label(self.db, actor_user_id)
            if actor_user_id
            else "Sistema — Revisão adversarial"
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=actor_label,
                event_type="legal_review_run",
                entity_type=REVIEW_AUDIT_ENTITY_TYPE,
                entity_id=artifact.id,
                correlation_id=str(demand_id),
                summary=(
                    f"Revisão adversarial de '{artifact.title}' — {len(finding_rows)} achado(s), "
                    f"protocolável={is_protocolable}."
                ),
                after_data={
                    "artifactVersionId": str(artifact.id),
                    "isProtocolable": is_protocolable,
                    "severities": [row.severity for row in finding_rows],
                    "reviewStatus": review_status,
                    "methodologyVersion": REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION,
                    "camadas": list(CAMADAS_REVISAO),
                    "parecer": {nivel: len(itens) for nivel, itens in parecer.items()},
                    "protocolExecuted": False,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()

        return LegalReviewResult(
            artifact_version_id=artifact.id,
            findings=finding_rows,
            is_protocolable=is_protocolable,
            journey_advanced=journey_advanced,
            parecer=parecer,
            review_status=review_status,
            methodology_version=REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION,
        )

    # ── Jornada (Onda 4) — best-effort, nunca derruba a revisão ─────────

    async def _register_journey_checkpoint(
        self,
        workspace_id: UUID,
        demand: LegalDemand,
        actor_user_id: UUID | None,
    ) -> bool:
        if demand.matter_id is None or actor_user_id is None:
            return False

        publication = await self.db.scalar(
            select(ProcessPublication)
            .join(
                MonitoredProcess,
                MonitoredProcess.id == ProcessPublication.monitored_process_id,
            )
            .where(
                ProcessPublication.workspace_id == workspace_id,
                MonitoredProcess.workspace_id == workspace_id,
                MonitoredProcess.matter_id == demand.matter_id,
                ProcessPublication.journey_state == PublicationJourneyState.minuta_em_producao,
            )
            .order_by(ProcessPublication.created_at.desc())
            .limit(1)
        )
        if publication is None:
            return False
        try:
            result = await LegalJourneyService(self.db, workspace_id).advance(
                publication,
                PublicationJourneyState.revisao_juridica,
                actor_type=JourneyActorType.human,
                actor_user_id=actor_user_id,
            )
        except LegalJourneyError:
            return False
        return result.advanced
