"""Native Ads Manager projection, evidence import and first-party KPI joins."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.db.post_commit import queue_realtime_event
from app.models.ads_manager import (
    AdsManagerEntity,
    AdsManagerImportReceipt,
    AdsMetricObservation,
)
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    LeadHydrationJob,
    OmnichannelChannelConnection,
)
from app.schemas.ads_manager import AdsSnapshotIngest
from app.services.workspace_time_service import (
    resolve_workspace_timezone,
    workspace_date_window_utc,
)

_SECRET_KEY = re.compile(
    r"(?:access|refresh|api|private|client)?[_-]?(?:token|secret|password|key|signature|cookie)$",
    re.IGNORECASE,
)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _safe_ratio(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    denominator_value = Decimal(str(denominator))
    if denominator_value == 0:
        return None
    return (Decimal(str(numerator)) / denominator_value).quantize(Decimal("0.0001"))


class AdsManagerService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _connection(
        self, workspace_id: UUID, connection_id: UUID
    ) -> OmnichannelChannelConnection:
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.id == connection_id,
            )
        )
        if connection is None:
            raise LookupError("Conexão de anúncios não encontrada.")
        if connection.provider not in {"meta_ads", "tiktok_ads"}:
            raise ValueError("A conexão não pertence ao Gestor de Anúncios.")
        if connection.status in {"paused", "disconnected_local"}:
            raise ValueError("A conexão de anúncios está pausada ou desconectada.")
        return connection

    async def ingest_snapshot(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        payload: AdsSnapshotIngest,
    ) -> dict[str, Any]:
        connection = await self._connection(workspace_id, connection_id)
        if payload.source == "provider_sync" and not external_delivery_allowed():
            raise ValueError("Importação externa bloqueada pelo gate do Meu Executivo.")
        payload_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        receipt_query = select(AdsManagerImportReceipt).where(
            AdsManagerImportReceipt.workspace_id == workspace_id,
            AdsManagerImportReceipt.connection_id == connection_id,
            AdsManagerImportReceipt.external_sync_id == payload.external_sync_id,
        )
        existing = await self.db.scalar(receipt_query)
        if existing is not None:
            if existing.payload_sha256 != payload_hash:
                raise ValueError("O sync de anúncios já existe com conteúdo diferente.")
            return await self._import_result(existing, payload, deduplicated=True)

        keys = [(item.entity_type, item.external_id) for item in payload.entities]
        if len(keys) != len(set(keys)):
            raise ValueError("O snapshot contém a mesma entidade mais de uma vez.")
        for item in payload.entities:
            if item.parent_external_id == item.external_id:
                raise ValueError("Uma entidade de anúncio não pode ser seu próprio pai.")

        observation_count = sum(len(item.metrics) for item in payload.entities)
        receipt = AdsManagerImportReceipt(
            workspace_id=workspace_id,
            connection_id=connection_id,
            provider=connection.provider,
            external_sync_id=payload.external_sync_id,
            payload_sha256=payload_hash,
            source=payload.source,
            entity_count=len(payload.entities),
            observation_count=observation_count,
            observed_at=payload.observed_at,
            import_metadata=_scrub(payload.metadata),
        )
        entities: list[AdsManagerEntity] = []
        observations: list[AdsMetricObservation] = []
        try:
            async with self.db.begin_nested():
                self.db.add(receipt)
                await self.db.flush()
                for item in payload.entities:
                    entity = await self.db.scalar(
                        select(AdsManagerEntity).where(
                            AdsManagerEntity.workspace_id == workspace_id,
                            AdsManagerEntity.connection_id == connection_id,
                            AdsManagerEntity.entity_type == item.entity_type,
                            AdsManagerEntity.external_id == item.external_id,
                        )
                    )
                    if entity is None:
                        entity = AdsManagerEntity(
                            workspace_id=workspace_id,
                            connection_id=connection_id,
                            provider=connection.provider,
                            entity_type=item.entity_type,
                            external_id=item.external_id,
                            first_seen_at=payload.observed_at,
                        )
                        self.db.add(entity)
                    else:
                        entity.version += 1
                    entity.parent_external_id = item.parent_external_id
                    entity.display_name = item.display_name
                    entity.effective_status = item.effective_status
                    entity.configured_status = item.configured_status
                    entity.objective = item.objective
                    entity.buying_type = item.buying_type
                    entity.currency = item.currency
                    entity.timezone = item.timezone
                    entity.hierarchy_path = item.hierarchy_path
                    entity.entity_metadata = _scrub(item.metadata)
                    entity.last_seen_at = payload.observed_at
                    await self.db.flush()
                    entities.append(entity)
                    for metric in item.metrics:
                        metric_payload = metric.model_dump(mode="json", by_alias=True)
                        observation = AdsMetricObservation(
                            workspace_id=workspace_id,
                            entity_id=entity.id,
                            import_receipt_id=receipt.id,
                            provider=connection.provider,
                            external_sync_id=payload.external_sync_id,
                            metric_date=metric.metric_date,
                            attribution_window=metric.attribution_window,
                            currency=metric.currency.upper(),
                            spend=metric.spend,
                            impressions=metric.impressions,
                            reach=metric.reach,
                            clicks=metric.clicks,
                            conversations=metric.conversations,
                            comments=metric.comments,
                            leads=metric.leads,
                            qualified_leads=metric.qualified_leads,
                            opportunities=metric.opportunities,
                            contracts=metric.contracts,
                            revenue=metric.revenue,
                            provider_metrics=_scrub(metric.provider_metrics),
                            payload_sha256=_canonical_hash(metric_payload),
                            observed_at=payload.observed_at,
                        )
                        self.db.add(observation)
                        observations.append(observation)
                await self.db.flush()
                # SQLAlchemy expires server-managed timestamps on UPDATE. Refresh the
                # projection while the async session is active so response rendering
                # never attempts implicit I/O outside greenlet_spawn.
                for entity in entities:
                    await self.db.refresh(entity)
        except IntegrityError:
            existing = await self.db.scalar(receipt_query)
            if existing is None:
                raise
            if existing.payload_sha256 != payload_hash:
                raise ValueError(
                    "O sync de anúncios foi criado concorrentemente com outro conteúdo."
                )
            return await self._import_result(existing, payload, deduplicated=True)

        connection.last_synced_at = payload.observed_at
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="ads_manager.snapshot.imported",
            data={
                "connectionId": str(connection_id),
                "provider": connection.provider,
                "receiptId": str(receipt.id),
                "entityCount": len(entities),
                "observationCount": len(observations),
            },
        )
        return {
            "receipt": receipt,
            "entities": entities,
            "observations": observations,
            "deduplicated": False,
        }

    async def _import_result(
        self,
        receipt: AdsManagerImportReceipt,
        payload: AdsSnapshotIngest,
        *,
        deduplicated: bool,
    ) -> dict[str, Any]:
        entity_keys = {(item.entity_type, item.external_id) for item in payload.entities}
        entities = list(
            (
                await self.db.scalars(
                    select(AdsManagerEntity).where(
                        AdsManagerEntity.workspace_id == receipt.workspace_id,
                        AdsManagerEntity.connection_id == receipt.connection_id,
                    )
                )
            ).all()
        )
        entities = [
            item for item in entities if (item.entity_type, item.external_id) in entity_keys
        ]
        observations = list(
            (
                await self.db.scalars(
                    select(AdsMetricObservation).where(
                        AdsMetricObservation.workspace_id == receipt.workspace_id,
                        AdsMetricObservation.import_receipt_id == receipt.id,
                    )
                )
            ).all()
        )
        return {
            "receipt": receipt,
            "entities": entities,
            "observations": observations,
            "deduplicated": deduplicated,
        }

    async def list_entities(
        self,
        workspace_id: UUID,
        *,
        connection_id: UUID | None = None,
        provider: str | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[AdsManagerEntity]:
        query = select(AdsManagerEntity).where(
            AdsManagerEntity.workspace_id == workspace_id
        )
        if connection_id:
            query = query.where(AdsManagerEntity.connection_id == connection_id)
        if provider:
            query = query.where(AdsManagerEntity.provider == provider)
        if entity_type:
            query = query.where(AdsManagerEntity.entity_type == entity_type)
        if status:
            query = query.where(AdsManagerEntity.effective_status == status)
        return list(
            (
                await self.db.scalars(
                    query.order_by(
                        AdsManagerEntity.entity_type.asc(),
                        AdsManagerEntity.display_name.asc(),
                    ).limit(limit)
                )
            ).all()
        )

    async def list_imports(
        self,
        workspace_id: UUID,
        *,
        provider: str | None = None,
        limit: int = 100,
    ) -> list[AdsManagerImportReceipt]:
        query = select(AdsManagerImportReceipt).where(
            AdsManagerImportReceipt.workspace_id == workspace_id
        )
        if provider:
            query = query.where(AdsManagerImportReceipt.provider == provider)
        return list(
            (
                await self.db.scalars(
                    query.order_by(AdsManagerImportReceipt.received_at.desc()).limit(limit)
                )
            ).all()
        )

    async def summary(
        self,
        workspace_id: UUID,
        *,
        date_from: date,
        date_to: date,
        provider: str | None = None,
    ) -> dict[str, Any]:
        if date_to < date_from:
            raise ValueError("A data final deve ser igual ou posterior à inicial.")
        campaign_query = select(AdsManagerEntity).where(
            AdsManagerEntity.workspace_id == workspace_id,
            AdsManagerEntity.entity_type == "campaign",
        )
        if provider:
            campaign_query = campaign_query.where(AdsManagerEntity.provider == provider)
        campaigns = list((await self.db.scalars(campaign_query)).all())
        campaign_ids = [item.id for item in campaigns]
        observations: list[AdsMetricObservation] = []
        if campaign_ids:
            observations = list(
                (
                    await self.db.scalars(
                        select(AdsMetricObservation)
                        .where(
                            AdsMetricObservation.workspace_id == workspace_id,
                            AdsMetricObservation.entity_id.in_(campaign_ids),
                            AdsMetricObservation.metric_date >= date_from,
                            AdsMetricObservation.metric_date <= date_to,
                        )
                        .order_by(AdsMetricObservation.observed_at.asc())
                    )
                ).all()
            )
        latest: dict[tuple[UUID, date], AdsMetricObservation] = {}
        for observation in observations:
            latest[(observation.entity_id, observation.metric_date)] = observation

        workspace_timezone = await resolve_workspace_timezone(self.db, workspace_id)
        start_at, end_at = workspace_date_window_utc(
            date_from,
            date_to,
            workspace_timezone,
        )
        capture_query = select(LeadCaptureReceipt).where(
            LeadCaptureReceipt.workspace_id == workspace_id,
            LeadCaptureReceipt.created_at >= start_at,
            LeadCaptureReceipt.created_at < end_at,
        )
        hydration_query = select(LeadHydrationJob.state, func.count()).where(
            LeadHydrationJob.workspace_id == workspace_id,
            LeadHydrationJob.created_at >= start_at,
            LeadHydrationJob.created_at < end_at,
        )
        if provider:
            capture_query = capture_query.where(LeadCaptureReceipt.provider == provider)
            hydration_query = hydration_query.where(LeadHydrationJob.provider == provider)
        captures = list((await self.db.scalars(capture_query)).all())
        captured_leads = len(captures)
        captures_by_campaign: dict[tuple[str, str], int] = {}
        for capture in captures:
            acquisition = (capture.result or {}).get("acquisition") or {}
            references = acquisition.get("providerReferences") or {}
            campaign_id = references.get("campaignId")
            if campaign_id:
                key = (capture.provider, str(campaign_id))
                captures_by_campaign[key] = captures_by_campaign.get(key, 0) + 1

        campaign_rows: list[dict[str, Any]] = []
        all_metrics = list(latest.values())
        for campaign in campaigns:
            metrics = [item for item in all_metrics if item.entity_id == campaign.id]
            row = self._aggregate_metrics(metrics)
            campaign_captures = captures_by_campaign.get(
                (campaign.provider, campaign.external_id), 0
            )
            row.update(
                {
                    "entity_id": campaign.id,
                    "external_id": campaign.external_id,
                    "display_name": campaign.display_name,
                    "provider": campaign.provider,
                    "effective_status": campaign.effective_status,
                    "objective": campaign.objective,
                    "captured_leads": campaign_captures,
                    "cpl": _safe_ratio(
                        row["spend"], campaign_captures or row["provider_leads"]
                    ),
                }
            )
            campaign_rows.append(row)
        campaign_rows.sort(key=lambda item: item["spend"], reverse=True)
        totals = self._aggregate_metrics(all_metrics)

        hydration_counts = {
            state: int(count)
            for state, count in (
                await self.db.execute(hydration_query.group_by(LeadHydrationJob.state))
            ).all()
        }
        cpl_lead_count = captured_leads or totals["provider_leads"]
        totals.update(
            {
                "date_from": date_from,
                "date_to": date_to,
                "provider": provider,
                "captured_leads": captured_leads,
                "hydration_pending": sum(
                    hydration_counts.get(state, 0)
                    for state in ("queued", "processing", "retrying", "blocked_external")
                ),
                "hydration_failed": hydration_counts.get("failed", 0),
                "cpl": _safe_ratio(totals["spend"], cpl_lead_count),
                "campaigns": campaign_rows,
                "generated_at": datetime.now(UTC),
            }
        )
        return totals

    @staticmethod
    def _aggregate_metrics(metrics: list[AdsMetricObservation]) -> dict[str, Any]:
        currencies = {item.currency for item in metrics}
        if len(currencies) == 1:
            currency = next(iter(currencies))
        elif currencies:
            currency = "MIX"
        else:
            currency = "BRL"
        spend = sum((Decimal(item.spend) for item in metrics), Decimal("0"))
        revenue = sum((Decimal(item.revenue) for item in metrics), Decimal("0"))
        impressions = sum(item.impressions for item in metrics)
        reach = sum(item.reach for item in metrics)
        clicks = sum(item.clicks for item in metrics)
        conversations = sum(item.conversations for item in metrics)
        comments = sum(item.comments for item in metrics)
        leads = sum(item.leads for item in metrics)
        qualified = sum(item.qualified_leads for item in metrics)
        opportunities = sum(item.opportunities for item in metrics)
        contracts = sum(item.contracts for item in metrics)
        ratios_allowed = currency != "MIX"
        return {
            "currency": currency,
            "spend": spend,
            "impressions": impressions,
            "reach": reach,
            "clicks": clicks,
            "conversations": conversations,
            "comments": comments,
            "provider_leads": leads,
            "qualified_leads": qualified,
            "opportunities": opportunities,
            "contracts": contracts,
            "revenue": revenue,
            "ctr": _safe_ratio(clicks * 100, impressions),
            "cpl": _safe_ratio(spend, leads) if ratios_allowed else None,
            "cac": _safe_ratio(spend, contracts) if ratios_allowed else None,
            "roas": _safe_ratio(revenue, spend) if ratios_allowed else None,
        }


__all__ = ["AdsManagerService"]
