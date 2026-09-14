"""
Schemas do SDR Jurídico Trabalhista v1.

Define a estrutura de qualificação de lead, estado da conversa SDR
e os dados persistidos no Matter após qualificação.
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.services.whatsapp.helpers import normalize_phone_number

# ── Enums do SDR ──────────────────────────────────────────────────────────────

class SDRStep(str, Enum):
    """Etapas do fluxo SDR."""
    inicio = "inicio"
    coletando_nome = "coletando_nome"
    coletando_demanda = "coletando_demanda"
    coletando_empregador = "coletando_empregador"
    coletando_tempo_empresa = "coletando_tempo_empresa"
    coletando_situacao = "coletando_situacao"
    coletando_documentos = "coletando_documentos"
    qualificando = "qualificando"
    qualificado = "qualificado"
    handoff_closer = "handoff_closer"
    handoff_assistente = "handoff_assistente"
    encerrado = "encerrado"


class SDRHandoffTarget(str, Enum):
    """Destino do handoff após qualificação."""
    closer_juridico = "closer_juridico"
    assistente_juridico = "assistente_juridico"
    nenhum = "nenhum"


# ── Schema de Qualificação Estruturada ───────────────────────────────────────

class SDRQualification(BaseModel):
    """
    Qualificação estruturada do lead trabalhista.
    Persistida no campo sdr_qualification (JSONB) do Matter.
    """
    # Dados coletados
    client_name: Optional[str] = Field(None, description="Nome completo do cliente")
    phone: Optional[str] = Field(None, description="Telefone/WhatsApp do cliente")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if not str(value or "").strip():
            return None
        normalized = normalize_phone_number(value)
        if normalized is None:
            raise ValueError(
                "Informe DDI, DDD e número válidos. Exemplo aceito pela Meta: 5561999999999"
            )
        return normalized
    demand_type: Optional[str] = Field(None, description="Tipo de demanda trabalhista")
    employer: Optional[str] = Field(None, description="Nome do empregador/empresa")
    time_at_company: Optional[str] = Field(None, description="Tempo de trabalho na empresa")
    employment_situation: Optional[str] = Field(
        None,
        description="Situação atual: empregado, demitido, afastado, etc."
    )
    has_ctps: Optional[bool] = Field(None, description="Possui CTPS/registro formal")
    has_documents: Optional[bool] = Field(None, description="Possui documentos relevantes")
    documents_requested: List[str] = Field(
        default_factory=list,
        description="Lista de documentos solicitados ao cliente"
    )
    documents_received: List[str] = Field(
        default_factory=list,
        description="Lista de documentos já recebidos"
    )

    # Avaliação do SDR
    qualification_score: Optional[float] = Field(
        None, ge=0.0, le=10.0,
        description="Score de qualificação do lead (0–10)"
    )
    classification: Optional[str] = Field(
        None,
        description="Classificação: Premium, Forte, Bom, Frio"
    )
    estimated_claim_value: Optional[float] = Field(
        None,
        description="Estimativa preliminar do valor da causa (R$)"
    )
    viability_notes: Optional[str] = Field(
        None,
        description="Notas de viabilidade geradas pelo SDR"
    )
    handoff_target: SDRHandoffTarget = Field(
        default=SDRHandoffTarget.nenhum,
        description="Destino do handoff após qualificação"
    )
    handoff_notes: Optional[str] = Field(
        None,
        description="Notas para o closer ou assistente jurídico"
    )

    # Controle de fluxo
    current_step: SDRStep = Field(default=SDRStep.inicio)
    completed: bool = Field(default=False)
    conversation_summary: Optional[str] = Field(
        None,
        description="Resumo da conversa de qualificação"
    )


# ── Schemas de API ────────────────────────────────────────────────────────────

class SDRStatusResponse(BaseModel):
    """Resposta do endpoint de status do SDR de um Matter."""
    matter_id: UUID
    sdr_step: SDRStep
    sdr_completed: bool
    qualification: Optional[SDRQualification] = None


class SDRReprocessRequest(BaseModel):
    """Requisição para reprocessar o SDR de um Matter."""
    matter_id: UUID
    reason: Optional[str] = Field(None, description="Motivo do reprocessamento")


class SDRReprocessResponse(BaseModel):
    """Resposta do reprocessamento do SDR."""
    matter_id: UUID
    status: str
    message: str
