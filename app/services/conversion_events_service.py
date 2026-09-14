"""Registrador único dos marcos do funil de conversão (anúncio → contrato).

Fachada fina e explícita sobre `KlmOperationsService.record_acquisition_event`:
os quatro marcos do funil comercial (`lead_qualificado`, `reuniao_agendada`,
`contrato_assinado`, `cobranca_paga`) mapeiam para os nomes canônicos que já
circulam em todo o sistema de tracking/attribution
(`lead_qualified`, `meeting_booked`, `contract_created`, `payment_confirmed`
— ver `TRACKING_CONVERSION_EVENTS` em `klm_operations_service.py` e
`_META_EVENT_NAMES`/`_TIKTOK_EVENT_NAMES` em `ads_conversion_feedback_service.py`).

Por que reaproveitar `AcquisitionEvent` em vez de uma tabela `ConversionEvent`
nova: o sistema já tem, testado e em produção local,
  * idempotência por `UNIQUE(workspace_id, dedupe_key)` (aqui:
    `f"conversion:{marco}:{matter_id}"` — um marco por caso, reprocessar não
    duplica);
  * snapshot de atribuição completo (ctwa_clid/gclid/fbclid/utms/source) via
    `AcquisitionEvent.click_ids`/`event_metadata` + o motor de atribuição em
    `acquisition_attribution_service.py`;
  * preparo automático do envelope de feedback de conversão por provedor
    (`KlmOperationsService._ensure_conversion_feedback_intent` →
    `ExternalActionIntent(action_type="ads_conversion_feedback")`) sempre que
    o marco registrado é um evento de conversão de tracking.
Duplicar essa maquinaria numa tabela `ConversionEvent` paralela criaria dois
caminhos de verdade para a mesma coisa. Este módulo é o ponto de entrada
nomeado que a arquitetura pede; por baixo, é o mesmo pipeline testado.

Pontos de chamada real (marcos do funil):
  * `lead_qualificado` — já emitido em `sdr_intake_service.record_sdr_lead_qualified`.
  * `reuniao_agendada` — `meeting_service.MeetingService.create_for_workspace`
    (fechado pela auditoria 2026-07-26, item 7 — antes só existia no seed).
  * `contrato_assinado` — já emitido nos pontos de aprovação de proposta/contrato
    (F11) e no webhook de assinatura.
  * `cobranca_paga` — já emitido em `finance_service._record_payment_confirmed`
    e no webhook de cobrança (F1).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import AcquisitionEvent
from app.schemas.klm_operations import AcquisitionEventCreate
from app.services.klm_operations_service import KlmOperationsService

# Marco (PT-BR, vocabulário de negócio) -> nome canônico do evento de tracking.
FUNNEL_MILESTONES: dict[str, str] = {
    "lead_qualificado": "lead_qualified",
    "reuniao_agendada": "meeting_booked",
    "contrato_assinado": "contract_created",
    "cobranca_paga": "payment_confirmed",
}


class UnknownFunnelMilestoneError(ValueError):
    """Marco de funil fora do vocabulário fixo de `FUNNEL_MILESTONES`."""


def canonical_event_name(marco: str) -> str:
    try:
        return FUNNEL_MILESTONES[marco]
    except KeyError as exc:
        raise UnknownFunnelMilestoneError(
            f"Marco de funil desconhecido: {marco!r}. Use um de {sorted(FUNNEL_MILESTONES)}."
        ) from exc


async def record_conversion_event(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    marco: str,
    matter_id: UUID,
    client_id: UUID | None = None,
    channel: str,
    source: str | None = None,
    medium: str | None = None,
    campaign: str | None = None,
    value: float | None = None,
    currency: str = "BRL",
    click_ids: Mapping[str, str] | None = None,
    event_metadata: Mapping[str, Any] | None = None,
    happened_at: datetime | None = None,
    dedupe_suffix: str | None = None,
) -> tuple[AcquisitionEvent, bool]:
    """Registra um marco do funil uma única vez por caso (idempotente).

    `dedupe_suffix` permite um marco ocorrer mais de uma vez de forma
    controlada (ex.: mais de uma reunião real no mesmo caso) — quando
    omitido, a chave é só `conversion:{marco}:{matter_id}` (um por caso).
    Retorna `(evento, já_existia)`, mesmo contrato de
    `KlmOperationsService.record_acquisition_event`.
    """
    event_name = canonical_event_name(marco)
    dedupe_key = f"conversion:{marco}:{matter_id}"
    if dedupe_suffix:
        dedupe_key = f"{dedupe_key}:{dedupe_suffix}"
    payload = AcquisitionEventCreate(
        matter_id=matter_id,
        client_id=client_id,
        event_name=event_name,
        channel=channel,
        source=source,
        medium=medium,
        campaign=campaign,
        dedupe_key=dedupe_key,
        happened_at=happened_at,
        value=value,
        currency=currency,
        click_ids=dict(click_ids or {}),
        event_metadata=dict(event_metadata or {}),
    )
    return await KlmOperationsService(db).record_acquisition_event(
        workspace_id, user_id, payload
    )


__all__ = [
    "FUNNEL_MILESTONES",
    "UnknownFunnelMilestoneError",
    "canonical_event_name",
    "record_conversion_event",
]
