"""
Service: CitationVerificationService — conferência de citação (Onda 6,
§9/§3 item 20).

Regra da casa (CLAUDE.md do usuário, `regra_validar_jurisprudencia_chrome`):
nenhum REsp/Tema/Súmula citado numa peça pode ir a protocolo sem
conferência na FONTE — nunca na memória paramétrica de um modelo de
linguagem. Por isso este serviço:

  * extrai candidatos a citação (súmula/tema/REsp/lei — vocabulário fechado
    do §9) do `content` de uma `LegalArtifactVersion` por regex, nunca por
    geração;
  * delega a conferência em si a um `CitationSourceProvider` INJETADO —
    hoje não existe, neste código-base, nenhuma fonte real de jurisprudência
    integrada (grep confirma: sem DataJud/Jusbrasil/RAG de jurisprudência).
    O default é `NullCitationSourceProvider`, que HONESTAMENTE nunca
    verifica nada — é o "gate fechado" do enunciado. Quando uma fonte real
    existir (Onda 7/8, ou integração dedicada), ela entra aqui implementando
    o protocolo, sem redesenho;
  * MESMO com um provider real injetado, a chamada só é feita se
    `model_api_execution_allowed()` (app/core/klm_runtime — o mesmo gate
    fail-closed que já governa toda chamada a modelo neste código-base)
    estiver aberto. Gate fechado => 'nao_verificada' honesto, sem tentar
    nada — nunca lança, nunca finge.
  * o veredito NUNCA é "verificada" sem uma fonte retornada pelo provider
    (`CitationLookupOutcome.source_reference` não vazio) — geração pura,
    sem fonte, é tratada como busca sem resultado ('nao_localizada'), não
    como confirmação;
  * o bloqueio de protocolabilidade é feito em `ApprovalService.resolve`
    (app/services/approval_service.py, Onda 2): aprovar um
    `legal_artifact:*` com qualquer citação fora de 'verificada' é
    rejeitado com 422 ANTES da transição de status. Este serviço
    deliberadamente NÃO escreve em `LegalArtifactVersion` — a tabela é
    registrada `register_append_only_model` (Onda 2, evidence_sha256
    travado) e todo UPDATE via ORM é rejeitado por `reject_mutation`
    (app/db/append_only.py); a coluna `protocolability_state` adicionada
    por outra frente (migração d0f2a4c6b813, ainda sem serviço próprio)
    herda essa mesma trava e por isso NÃO pode ser usada como gate mutável
    — o veredito vive só nas linhas de `LegalCitationVerification`
    (mutáveis, tabela própria) e é consultado sob demanda, nunca copiado
    para uma coluna que não pode ser atualizada.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import model_api_execution_allowed
from app.models.klm_operations import KlmAuditEvent, LegalArtifactVersion
from app.models.legal_artifact_verification import (
    CitationVerificationStatus,
    LegalCitationVerification,
)
from app.models.legal_demand import LegalDemand
from app.services.audit_actor_service import resolve_human_actor_label

CITATION_AUDIT_ENTITY_TYPE = "legal_citation_verification"

# ── Extração de citações (regex, nunca geração) ─────────────────────────────
# Vocabulário fechado do §9: súmula / tema (repetitivo) / acórdão (REsp e
# siglas correlatas do STJ/STF) / lei.
_CITATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "sumula",
        re.compile(
            r"S[uú]mula\s+(?:Vinculante\s+)?n?[ºo°]?\.?\s*\d+"
            r"(?:\s+d[ao]\s+(?:STF|STJ|TST|TJ[A-Z]{0,2}))?",
            re.IGNORECASE,
        ),
    ),
    (
        "tema",
        re.compile(
            r"Tema\s+(?:Repetitivo\s+)?n?[ºo°]?\.?\s*\d+"
            r"(?:\s+d[ao]\s+(?:STF|STJ))?",
            re.IGNORECASE,
        ),
    ),
    (
        "resp",
        re.compile(
            r"\b(?:REsp|RE|AgInt|AgRg|AREsp|EAREsp|EDiv|RHC|HC|ADI|ADPF)\.?"
            r"\s*n?[ºo°]?\.?\s*[\d\.\/-]{3,}",
            re.IGNORECASE,
        ),
    ),
    (
        "lei",
        re.compile(
            r"Lei\s+(?:Complementar\s+)?n?[ºo°]?\.?\s*[\d\.]{1,}(?:\/\d{2,4})?",
            re.IGNORECASE,
        ),
    ),
)


def iter_text_blocks(value: object) -> list[str]:
    """Percorre recursivamente `content` (dict/list/str) e devolve toda
    string-folha — não presume nenhum formato fixo de seção/parágrafo."""
    blocks: list[str] = []

    def _walk(node: object) -> None:
        if isinstance(node, str):
            if node.strip():
                blocks.append(node)
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)
        elif isinstance(node, list | tuple):
            for item in node:
                _walk(item)

    _walk(value)
    return blocks


@dataclass(frozen=True)
class CitationCandidate:
    text: str
    kind: str


def extract_citations(content: object) -> list[CitationCandidate]:
    """Extrai citações candidatas por regex — nunca por geração. Dedup por
    texto normalizado (espaços colapsados), preserva a primeira grafia
    encontrada."""
    seen: dict[str, CitationCandidate] = {}
    for text_block in iter_text_blocks(content):
        for kind, pattern in _CITATION_PATTERNS:
            for match in pattern.finditer(text_block):
                raw = match.group(0).strip()
                normalized = re.sub(r"\s+", " ", raw).lower()
                if normalized not in seen:
                    seen[normalized] = CitationCandidate(text=raw, kind=kind)
    return list(seen.values())


# ── Fonte de conferência — protocolo injetável, default honesto ───────────


@dataclass(frozen=True)
class CitationLookupOutcome:
    found: bool
    source_reference: str | None = None
    note: str | None = None


class CitationSourceProvider(Protocol):
    """Fonte real de conferência de citação (DataJud/Jusbrasil/RAG de
    jurisprudência dedicado — nenhuma existe hoje neste código-base, ver
    docstring do módulo). Implementações futuras entram aqui."""

    name: str

    async def lookup(self, citation_text: str, citation_kind: str) -> CitationLookupOutcome: ...


class NullCitationSourceProvider:
    """Default honesto: nenhuma fonte configurada. NUNCA verifica —
    devolve sempre `found=False` para que o serviço marque 'nao_verificada'
    (nunca 'nao_localizada', que implicaria uma busca real sem resultado)."""

    name = "nenhum_provedor_configurado"

    async def lookup(self, citation_text: str, citation_kind: str) -> CitationLookupOutcome:
        return CitationLookupOutcome(
            found=False,
            note="Sem provedor de fonte de jurisprudência configurado.",
        )


class CitationVerificationNotFoundError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@dataclass(frozen=True)
class CitationVerificationRunResult:
    artifact_version_id: UUID
    verifications: list[LegalCitationVerification] = field(default_factory=list)
    all_verified: bool = False
    gate_open: bool = False


class CitationVerificationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        provider: CitationSourceProvider | None = None,
    ) -> None:
        self.db = db
        self._provider = provider or NullCitationSourceProvider()

    # ── Leitura auxiliar escopada por workspace ─────────────────────────

    async def _require_demand(self, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == demand_id,
                LegalDemand.workspace_id == workspace_id,
            )
        )
        if demand is None:
            raise CitationVerificationNotFoundError("Demanda jurídica não encontrada.")
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
            raise CitationVerificationNotFoundError(
                "Documento jurídico não encontrado no workspace."
            )
        if demand.matter_id is None or artifact.matter_id != demand.matter_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Documento pertence a um caso diferente do vinculado a esta demanda.",
            )
        return artifact

    async def list_verifications(
        self, workspace_id: UUID, demand_id: UUID, artifact_id: UUID
    ) -> list[LegalCitationVerification]:
        demand = await self._require_demand(workspace_id, demand_id)
        artifact = await self._require_artifact(workspace_id, demand, artifact_id)
        result = await self.db.scalars(
            select(LegalCitationVerification)
            .where(
                LegalCitationVerification.workspace_id == workspace_id,
                LegalCitationVerification.artifact_version_id == artifact.id,
            )
            .order_by(LegalCitationVerification.created_at.asc())
        )
        return list(result.all())

    # ── Conferência ──────────────────────────────────────────────────────

    async def verify_citations(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        artifact_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> CitationVerificationRunResult:
        demand = await self._require_demand(workspace_id, demand_id)
        artifact = await self._require_artifact(workspace_id, demand, artifact_id)

        candidates = extract_citations(artifact.content)
        gate_open = model_api_execution_allowed()
        now = datetime.now(UTC)

        existing_by_text = {
            row.citation_text: row
            for row in await self.db.scalars(
                select(LegalCitationVerification).where(
                    LegalCitationVerification.workspace_id == workspace_id,
                    LegalCitationVerification.artifact_version_id == artifact.id,
                )
            )
        }

        verifications: list[LegalCitationVerification] = []
        for candidate in candidates:
            row = existing_by_text.get(candidate.text)
            if row is None:
                row = LegalCitationVerification(
                    workspace_id=workspace_id,
                    legal_demand_id=demand.id,
                    artifact_version_id=artifact.id,
                    citation_text=candidate.text,
                    citation_kind=candidate.kind,
                    status=CitationVerificationStatus.nao_verificada.value,
                )
                self.db.add(row)

            if not gate_open:
                row.status = CitationVerificationStatus.nao_verificada.value
                row.source_reference = None
                row.trail = {
                    "gate": "closed",
                    "checkedAt": now.isoformat(),
                    "note": "model_api_execution_allowed() está fechado.",
                }
            else:
                try:
                    outcome = await self._provider.lookup(candidate.text, candidate.kind)
                except Exception as error:  # nunca deixa a conferência derrubar a chamada
                    row.status = CitationVerificationStatus.nao_verificada.value
                    row.source_reference = None
                    row.trail = {
                        "gate": "open",
                        "provider": self._provider.name,
                        "checkedAt": now.isoformat(),
                        "error": type(error).__name__,
                    }
                else:
                    if outcome.found and outcome.source_reference:
                        row.status = CitationVerificationStatus.verificada.value
                        row.source_reference = outcome.source_reference
                    else:
                        row.status = CitationVerificationStatus.nao_localizada.value
                        row.source_reference = None
                    row.trail = {
                        "gate": "open",
                        "provider": self._provider.name,
                        "checkedAt": now.isoformat(),
                        "note": outcome.note,
                    }
            row.checked_by_user_id = actor_user_id
            verifications.append(row)

        await self.db.flush()

        all_verified = all(
            row.status == CitationVerificationStatus.verificada.value for row in verifications
        )

        actor_label = (
            await resolve_human_actor_label(self.db, actor_user_id)
            if actor_user_id
            else "Sistema — Conferência de citações"
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=actor_label,
                event_type="legal_citation_verification_run",
                entity_type=CITATION_AUDIT_ENTITY_TYPE,
                entity_id=artifact.id,
                correlation_id=str(demand_id),
                summary=(
                    f"{len(verifications)} citação(ões) conferidas para '{artifact.title}' — "
                    f"gate={'aberto' if gate_open else 'fechado'}, "
                    f"verificadas={sum(1 for v in verifications if v.status == 'verificada')}."
                ),
                after_data={
                    "artifactVersionId": str(artifact.id),
                    "allVerified": all_verified,
                    "statuses": [v.status for v in verifications],
                },
                occurred_at=now,
            )
        )
        await self.db.flush()

        return CitationVerificationRunResult(
            artifact_version_id=artifact.id,
            verifications=verifications,
            all_verified=all_verified,
            gate_open=gate_open,
        )
