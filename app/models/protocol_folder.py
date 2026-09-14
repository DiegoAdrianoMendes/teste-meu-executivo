"""
Modelo: ProtocolFolder — pasta de protocolo + manifesto (Onda 7, Plano
Mestre §10).

A plataforma PREPARA e VALIDA a pasta de protocolo (estrutura de
subpastas numeradas, manifesto de conformidade, ZIP de entrega) — ela
NUNCA protocola. `PROTOCOLO É EXCLUSIVAMENTE HUMANO`: não existe, em
nenhuma rota de `app/api/v1/endpoints/protocol_folder.py`, um verbo que
"protocole" nos autos; o único jeito de este registro sair de
`bloqueado`/`pendente` para `protocolavel` é TODAS as validações do §10
passarem, e o único jeito dele registrar que o protocolo de fato
aconteceu é `registrar_recibo` — sempre POSTERIOR ao evento real, sempre
colado por um humano (nunca inferido, nunca disparado por um agente; ver
app/services/protocol_folder_service.py e o teste que prova a ausência de
rota).

REUSA (não duplica):
  * `LegalArtifactVersion`/`LegalArtifactState` (Onda 2/5/6) — a pasta
    referencia UMA versão já aprovada por `artifact_version_id`; nunca
    copia `content`.
  * `LegalCitationVerification` (Onda 6) e `LegalReviewFinding` (Onda 6)
    — o manifesto consulta essas tabelas para o item "citações
    verificadas"/"achado crítico aberto", nunca reimplementa a conferência.
  * `compute_checklist`/`recompute_checklist` (Onda 6,
    app/services/legal_editor_service.py) — placeholder/procuração/custas
    do artefato em si vêm de lá.
  * `Document`/`StorageObject` (GED existente) — os arquivos físicos da
    pasta são os mesmos documentos do Caso 360, nunca um upload paralelo.

RLS direta por workspace_id, mesmo padrão de
`app/db/rls.py::LEGAL_PRODUCTION_DIRECT_TENANT_TABLES`.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.klm_operations import LegalArtifactVersion
    from app.models.legal_demand import LegalDemand
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.workspace import Workspace


class ProtocolFolderState(StrEnum):
    """Vocabulário idêntico ao `protocolability_state` do editor jurídico
    (Onda 6) — mesmo significado: `bloqueado` tem impeditivo estrutural
    (placeholder/artefato não aprovado/citação não verificada/achado
    crítico/hash de arquivo alterado), `pendente` tem lacuna sanável
    (procuração/custas/assinatura/responsável ausente), `protocolavel` só
    quando TODAS as validações do §10 passam. Nunca existe um quarto
    estado 'pronto' fora deste vocabulário fail-closed."""

    bloqueado = "bloqueado"
    pendente = "pendente"
    protocolavel = "protocolavel"


# Vocabulário fechado das subpastas do §10 — numeração e ordem SEMPRE
# nesta sequência (ver `protocol_folder_service.FOLDER_ORDER`); persistido
# aqui só como referência para quem lê o modelo sem abrir o serviço.
PROTOCOL_FOLDER_SUBFOLDERS: tuple[str, ...] = (
    "00_MANIFESTO",
    "01_PETICAO",
    "02_PROCURACAO",
    "03_DOCS_PESSOAIS",
    "04_PROVAS",
    "05_DOCS_PROCESSUAIS",
    "06_CUSTAS",
    "99_ORIGINAIS",
)


class ProtocolFolder(UUIDMixin, TimestampMixin, Base):
    """Uma linha por (workspace, demanda, versão de artefato) — projeção
    MUTÁVEL (mesmo espírito de `LegalArtifactState`, nunca
    `register_append_only_model`): cada nova montagem/validação
    ATUALIZA `manifest`/`files`/`state` da mesma linha, o que é o que
    permite detectar "hash de arquivo alterado após aprovação" (compara o
    hash recém-calculado contra o hash já persistido de uma montagem
    anterior — ver `protocol_folder_service.build_protocol_folder`)."""

    __tablename__ = "protocol_folders"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "legal_demand_id",
            "artifact_version_id",
            name="uq_protocol_folder_demand_artifact",
        ),
        CheckConstraint(
            "state IN ('bloqueado','pendente','protocolavel')",
            name="ck_protocol_folder_state_values",
        ),
        CheckConstraint(
            "(receipt_registered_at IS NULL) = (receipt_registered_by_user_id IS NULL)",
            name="ck_protocol_folder_receipt_pair",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    artifact_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("klm_legal_artifact_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tribunal: Mapped[str] = mapped_column(String(160), nullable=False)
    # Mesmo vocabulário de `LegalArtifactVersion.kind`/`LegalArtifactKind`
    # (petition/contract/power_of_attorney/proposal) — "tipo de ato" do §10.
    act_type: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ProtocolFolderState.bloqueado.value,
        server_default=ProtocolFolderState.bloqueado.value,
        index=True,
    )
    # Estrutura completa da validação do §10 (processo/tribunal/tipo de
    # ato/responsável/versão liberada/assinatura/procuração/custas/
    # citações/placeholders/pendências/data) — ver
    # `protocol_folder_service.ManifestResult.as_dict`.
    manifest: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Lista ORDENADA de arquivos {order, folder, filename, sourceType,
    # sourceId, sha256, sizeBytes, extension} — a MESMA lista que vira o
    # ZIP de entrega.
    files: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    # Confirmação humana explícita de assinatura — NUNCA inferida de
    # nenhum campo (não há verificação criptográfica de assinatura digital
    # integrada nesta onda; ver docstring do serviço).
    signature_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    built_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Recibo (registro POSTERIOR, exclusivamente humano) ──
    receipt_registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    receipt_registered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Número/protocolo do comprovante real colado pelo humano — texto
    # livre (cada tribunal tem seu próprio formato de recibo).
    receipt_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    matter: Mapped[Matter] = relationship("Matter")
    artifact_version: Mapped[LegalArtifactVersion] = relationship("LegalArtifactVersion")
    built_by: Mapped[User | None] = relationship("User", foreign_keys=[built_by_user_id])
    receipt_registered_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[receipt_registered_by_user_id]
    )

    @property
    def is_protocoled(self) -> bool:
        return self.receipt_registered_at is not None

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<ProtocolFolder id={self.id} state={self.state}>"
