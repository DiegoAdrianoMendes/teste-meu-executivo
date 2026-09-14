"""Backfill de CTWA histórico (T1.2 do Plano Meta Máximo).

Varre `MessageEvidence` (OmnichannelMessageEvidenceEvent) já persistido — o
histórico de mensagens WhatsApp recebidas antes do sprint F2.1, quando a
captura de `referral` ao vivo (`webhook_service._parse_referral` /
`_record_ctwa_acquisition`) ainda não existia — e emite os MESMOS artefatos
que a captura ao vivo emitiria para cada mensagem que já veio de um anúncio
CTWA: `thread_metadata.acquisition` (primeiro touch cronológico vence) +
`AcquisitionEvent(conversation_started, dedupe_key=f"ctwa:{wamid}")`.

Leitura estritamente read-only sobre a evidência (nunca edita/apaga um
`OmnichannelMessageEvidenceEvent`); a única escrita é a criação idempotente de
`AcquisitionEvent` e a atualização do `thread_metadata` do
`OmnichannelThread` correspondente — exatamente o que a captura ao vivo já
faz.

Decisão de resolução evidência→thread: cada linha de evidência já carrega
`thread_id` e `message_id` denormalizados no momento da captura original
(ver `WhatsAppWebhookService._record_provider_observation`, que grava
`thread_id=canonical.thread_id` e `event_payload=message_data` — o dicionário
bruto do webhook, com `referral` quando veio de um anúncio). Isso já É "a
mesma resolução usada ao vivo": não recalculamos nada, apenas reaproveitamos o
que o pipeline ao vivo resolveu e persistiu na hora. Se a linha não tem
`thread_id` (evidência órfã, sem canonical ainda resolvido), a entrada conta
como `unresolved` e nenhum evento é criado a partir dela.

Ordem de varredura = `received_at` da evidência (o `id` é um UUID aleatório,
não sequencial, então não serve de proxy cronológico). `received_at` é o
instante em que a evidência foi gravada, que é a ordem cronológica real de
chegada da mensagem original (mesmo para dados "históricos": a evidência foi
gravada em tempo real quando a mensagem chegou, só o efeito colateral de
aquisição é que não existia ainda). Isso garante que "o PRIMEIRO touch vence"
ao reusar a mesma semântica de `setdefault` do método ao vivo — não é
necessário recalcular por timestamp.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import AcquisitionEvent
from app.models.omnichannel_v6 import OmnichannelMessageEvidenceEvent
from app.services.whatsapp.webhook_service import WhatsAppWebhookService, _parse_referral

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500

_WHATSAPP_EVIDENCE_PROVIDERS = ("meta_cloud", "qr_gateway")


@dataclass
class CtwaBackfillReport:
    scanned: int = 0
    with_referral: int = 0
    events_created: int = 0
    skipped_existing: int = 0
    unresolved: int = 0
    dry_run: bool = True
    sample_dedupe_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "withReferral": self.with_referral,
            "eventsCreated": self.events_created,
            "skippedExisting": self.skipped_existing,
            "unresolved": self.unresolved,
            "dryRun": self.dry_run,
            "sampleDedupeKeys": self.sample_dedupe_keys[:20],
        }


class CtwaBackfillService:
    """Recupera acquisition events CTWA de mensagens WhatsApp já recebidas."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # Só usamos `_record_ctwa_acquisition`, que depende exclusivamente de
        # `self.db`; as demais dependências do webhook service não são usadas
        # neste fluxo (sem envio, sem automação, sem campanha) e ficam None.
        self._webhook_service = WhatsAppWebhookService(
            db,
            connection_service=None,  # type: ignore[arg-type]
            message_service=None,  # type: ignore[arg-type]
            automation_rule_service=None,  # type: ignore[arg-type]
            campaign_service=None,  # type: ignore[arg-type]
        )

    async def run(
        self,
        *,
        workspace_id: UUID | None = None,
        dry_run: bool = True,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_batches: int | None = None,
    ) -> CtwaBackfillReport:
        report = CtwaBackfillReport(dry_run=dry_run)
        cursor: tuple[datetime, UUID] | None = None
        batches_done = 0

        while True:
            if max_batches is not None and batches_done >= max_batches:
                break
            batch = await self._fetch_batch(
                workspace_id=workspace_id,
                after_cursor=cursor,
                batch_size=batch_size,
            )
            if not batch:
                break
            batches_done += 1
            for evidence in batch:
                report.scanned += 1
                await self._process_one(evidence, report=report, dry_run=dry_run)
            if not dry_run:
                await self.db.commit()
            last = batch[-1]
            cursor = (last.received_at, last.id)
            if len(batch) < batch_size:
                break

        return report

    async def _fetch_batch(
        self,
        *,
        workspace_id: UUID | None,
        after_cursor: tuple[datetime, UUID] | None,
        batch_size: int,
    ) -> list[OmnichannelMessageEvidenceEvent]:
        # Ordenado por `(received_at, id)` — o `id` é um UUID aleatório (não
        # sequencial), então não reflete ordem cronológica; `received_at` é o
        # instante em que a evidência foi gravada, que É a ordem cronológica
        # real de chegada da mensagem (mesmo para dados históricos: a
        # evidência foi criada em tempo real quando a mensagem chegou). Essa
        # ordem é o que garante "o primeiro touch vence" na captura ao vivo
        # via `setdefault`.
        stmt = (
            select(OmnichannelMessageEvidenceEvent)
            .where(
                OmnichannelMessageEvidenceEvent.event_type == "provider_receipt",
                OmnichannelMessageEvidenceEvent.provider.in_(_WHATSAPP_EVIDENCE_PROVIDERS),
            )
            .order_by(
                OmnichannelMessageEvidenceEvent.received_at.asc(),
                OmnichannelMessageEvidenceEvent.id.asc(),
            )
            .limit(batch_size)
        )
        if workspace_id is not None:
            stmt = stmt.where(OmnichannelMessageEvidenceEvent.workspace_id == workspace_id)
        if after_cursor is not None:
            after_received_at, after_id = after_cursor
            stmt = stmt.where(
                or_(
                    OmnichannelMessageEvidenceEvent.received_at > after_received_at,
                    (OmnichannelMessageEvidenceEvent.received_at == after_received_at)
                    & (OmnichannelMessageEvidenceEvent.id > after_id),
                )
            )
        result = await self.db.scalars(stmt)
        return list(result.all())

    async def _process_one(
        self,
        evidence: OmnichannelMessageEvidenceEvent,
        *,
        report: CtwaBackfillReport,
        dry_run: bool,
    ) -> None:
        payload = evidence.event_payload if isinstance(evidence.event_payload, dict) else {}
        referral = _parse_referral(payload)
        if not referral:
            return
        report.with_referral += 1

        wamid = str(evidence.external_message_id or "").strip()
        if not wamid:
            report.unresolved += 1
            return

        dedupe_key = f"ctwa:{wamid}"
        existing = await self.db.scalar(
            select(AcquisitionEvent.id).where(
                AcquisitionEvent.workspace_id == evidence.workspace_id,
                AcquisitionEvent.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            report.skipped_existing += 1
            return

        if evidence.thread_id is None:
            # Sem thread canônico resolvido não há como atribuir a conversa;
            # marcamos como não resolvido em vez de criar um evento órfão.
            report.unresolved += 1
            return

        if dry_run:
            report.events_created += 1
            if len(report.sample_dedupe_keys) < 20:
                report.sample_dedupe_keys.append(dedupe_key)
            return

        happened_at = evidence.provider_occurred_at or evidence.received_at
        await self._webhook_service._record_ctwa_acquisition(
            workspace_id=evidence.workspace_id,
            wamid=wamid,
            referral=referral,
            thread_id=evidence.thread_id,
            happened_at=happened_at,
        )
        report.events_created += 1
        if len(report.sample_dedupe_keys) < 20:
            report.sample_dedupe_keys.append(dedupe_key)
