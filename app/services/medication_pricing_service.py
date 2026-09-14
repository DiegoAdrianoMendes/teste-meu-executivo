"""Importa referências CMED e calcula custos somente com ciclos prescritos."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.medication import MatterTreatmentEstimate, MedicationReference
from app.models.workspace import Workspace
from app.schemas.medication import (
    LegalPricingPolicyRead,
    LegalPricingPolicyUpdate,
    MedicationCatalogStatusRead,
    TreatmentEstimateUpsert,
)
from app.services.audit_actor_service import resolve_human_actor_label

CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "medication_catalog_cmed_2026_06_10.json"
)
DEFAULT_LEGAL_PRICING_POLICY = LegalPricingPolicyRead().model_dump(
    mode="json", by_alias=True
)


@lru_cache(maxsize=1)
def load_packaged_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


class MedicationPricingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def seed_catalog(self) -> int:
        """Upsert idempotente do catálogo empacotado; não lê o XLSX em runtime."""
        catalog = load_packaged_catalog()
        records = catalog["records"]
        keys = [record["catalogKey"] for record in records]
        existing_rows = await self.db.execute(
            select(MedicationReference).where(MedicationReference.catalog_key.in_(keys))
        )
        existing = {row.catalog_key: row for row in existing_rows.scalars()}
        inserted = 0
        source_hash = catalog["source"]["sha256"]
        for record in records:
            reference = existing.get(record["catalogKey"])
            if reference is None:
                reference = MedicationReference(catalog_key=record["catalogKey"])
                self.db.add(reference)
                inserted += 1
            values = self._reference_values(record, source_hash)
            for field_name, value in values.items():
                setattr(reference, field_name, value)
        await self.db.flush()
        return inserted

    async def catalog_status(self) -> MedicationCatalogStatusRead:
        catalog = load_packaged_catalog()
        dataset_version = catalog["datasetVersion"]
        record_count = int(
            await self.db.scalar(
                select(func.count(MedicationReference.id)).where(
                    MedicationReference.dataset_version == dataset_version
                )
            )
            or 0
        )
        oncology_count = int(
            await self.db.scalar(
                select(func.count(MedicationReference.id)).where(
                    MedicationReference.dataset_version == dataset_version,
                    MedicationReference.oncology.is_(True),
                )
            )
            or 0
        )
        policy = catalog["calculationPolicy"]
        source = catalog["source"]
        return MedicationCatalogStatusRead(
            dataset_version=dataset_version,
            source_sha256=source["sha256"],
            record_count=record_count,
            oncology_count=oncology_count,
            source_filename=source["originalFilename"],
            cmed_published_at=source["cmedPublishedAt"],
            source_url=source["cmedPortal"],
            price_unit=policy["priceUnit"],
            calculation_formula=policy["formula"],
            limitations=[policy["note"], *policy["forbiddenInferences"]],
        )

    async def search_catalog(
        self,
        *,
        query: str | None,
        oncology: bool | None,
        limit: int,
    ) -> list[MedicationReference]:
        dataset_version = load_packaged_catalog()["datasetVersion"]
        statement = select(MedicationReference).where(
            MedicationReference.dataset_version == dataset_version
        )
        normalized = (query or "").strip()
        if normalized:
            escaped = normalized.replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    MedicationReference.product_name.ilike(pattern, escape="\\"),
                    MedicationReference.active_ingredient.ilike(pattern, escape="\\"),
                    MedicationReference.ggrem_code.ilike(pattern, escape="\\"),
                    MedicationReference.anvisa_registration.ilike(pattern, escape="\\"),
                )
            )
        if oncology is not None:
            statement = statement.where(MedicationReference.oncology.is_(oncology))
        statement = statement.order_by(
            MedicationReference.pmvg_without_tax.desc(), MedicationReference.product_name
        ).limit(limit)
        rows = await self.db.execute(statement)
        return list(rows.scalars())

    async def list_treatment_estimates(
        self, workspace_id: UUID, matter_id: UUID
    ) -> list[MatterTreatmentEstimate]:
        await self._matter(workspace_id, matter_id)
        rows = await self.db.execute(
            select(MatterTreatmentEstimate)
            .options(selectinload(MatterTreatmentEstimate.medication))
            .where(
                MatterTreatmentEstimate.workspace_id == workspace_id,
                MatterTreatmentEstimate.matter_id == matter_id,
            )
            .order_by(MatterTreatmentEstimate.updated_at.desc())
        )
        return list(rows.scalars())

    async def upsert_treatment_estimate(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        payload: TreatmentEstimateUpsert,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> MatterTreatmentEstimate:
        matter = await self._matter(workspace_id, matter_id, for_update=True)
        medication = await self.db.get(MedicationReference, payload.medication_reference_id)
        if medication is None:
            raise LookupError("Apresentação CMED não encontrada no catálogo local.")
        estimate = await self.db.scalar(
            select(MatterTreatmentEstimate).where(
                MatterTreatmentEstimate.workspace_id == workspace_id,
                MatterTreatmentEstimate.matter_id == matter_id,
                MatterTreatmentEstimate.medication_reference_id == medication.id,
            )
        )
        before = self._estimate_snapshot(estimate) if estimate else None
        if estimate is None:
            estimate = MatterTreatmentEstimate(
                workspace_id=workspace_id,
                matter_id=matter_id,
                medication_reference_id=medication.id,
            )
            self.db.add(estimate)

        presentations_per_cycle = Decimal(str(payload.presentations_per_cycle))
        total = (
            medication.pmvg_without_tax
            * presentations_per_cycle
            * Decimal(payload.cycle_count)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        estimate.presentations_per_cycle = presentations_per_cycle
        estimate.cycle_count = payload.cycle_count
        estimate.cycle_interval_days = payload.cycle_interval_days
        estimate.treatment_duration_days = payload.treatment_duration_days
        estimate.cost_per_presentation = medication.pmvg_without_tax
        estimate.total_treatment_cost = total
        estimate.calculation_formula = (
            f"{medication.pmvg_without_tax:.2f} × {presentations_per_cycle} "
            f"apresentação(ões)/ciclo × {payload.cycle_count} ciclo(s)"
        )
        estimate.prescription_reference = payload.prescription_reference
        estimate.notes = payload.notes
        estimate.review_status = payload.review_status
        estimate.applied_to_matter_economic_value = (
            payload.apply_to_matter_economic_value
        )
        estimate.source_snapshot = {
            "datasetVersion": medication.dataset_version,
            "catalogKey": medication.catalog_key,
            "productName": medication.product_name,
            "activeIngredient": medication.active_ingredient,
            "cmedPresentation": medication.cmed_presentation,
            "ggremCode": medication.ggrem_code,
            "pmvgWithoutTax": float(medication.pmvg_without_tax),
            "sourceUrl": medication.source_url,
            "sourceRows": medication.source_rows,
        }
        if payload.review_status in {"revisado", "aprovado"}:
            estimate.reviewed_by_user_id = actor_user_id
            estimate.reviewed_at = datetime.now(UTC)
        else:
            estimate.reviewed_by_user_id = None
            estimate.reviewed_at = None
        if payload.apply_to_matter_economic_value:
            matter.economic_value_annual = total
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            event_type="matter_treatment_estimate_updated",
            entity_id=matter_id,
            summary=(
                f"Custo do tratamento com {medication.product_name} calculado "
                "a partir dos ciclos informados."
            ),
            before=before,
            after=self._estimate_snapshot(estimate),
        )
        await self.db.refresh(estimate, attribute_names=["medication"])
        return estimate

    async def delete_treatment_estimate(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        estimate_id: UUID,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> None:
        await self._matter(workspace_id, matter_id)
        estimate = await self.db.scalar(
            select(MatterTreatmentEstimate)
            .options(selectinload(MatterTreatmentEstimate.medication))
            .where(
                MatterTreatmentEstimate.id == estimate_id,
                MatterTreatmentEstimate.workspace_id == workspace_id,
                MatterTreatmentEstimate.matter_id == matter_id,
            )
        )
        if estimate is None:
            raise LookupError("Estimativa de tratamento não encontrada neste caso.")
        before = self._estimate_snapshot(estimate)
        await self.db.delete(estimate)
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            event_type="matter_treatment_estimate_deleted",
            entity_id=matter_id,
            summary="Estimativa de tratamento removida do caso.",
            before=before,
            after=None,
        )

    async def get_pricing_policy(self, workspace_id: UUID) -> LegalPricingPolicyRead:
        workspace = await self._workspace(workspace_id)
        merged = deepcopy(DEFAULT_LEGAL_PRICING_POLICY)
        merged.update(deepcopy((workspace.settings or {}).get("legalPricingPolicy") or {}))
        return LegalPricingPolicyRead.model_validate(merged)

    async def update_pricing_policy(
        self,
        workspace_id: UUID,
        payload: LegalPricingPolicyUpdate,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> LegalPricingPolicyRead:
        workspace = await self._workspace(workspace_id, for_update=True)
        before_raw = deepcopy((workspace.settings or {}).get("legalPricingPolicy") or {})
        before = deepcopy(DEFAULT_LEGAL_PRICING_POLICY)
        before.update(before_raw)
        settings_data = deepcopy(workspace.settings or {})
        settings_data["legalPricingPolicy"] = payload.model_dump(mode="json", by_alias=True)
        workspace.settings = settings_data
        after = LegalPricingPolicyRead.model_validate(settings_data["legalPricingPolicy"])
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            event_type="legal_pricing_policy_updated",
            entity_id=workspace_id,
            summary="Política interna de honorários atualizada.",
            before=before,
            after=after.model_dump(mode="json", by_alias=True),
            entity_type="workspace",
        )
        return after

    async def _matter(
        self, workspace_id: UUID, matter_id: UUID, *, for_update: bool = False
    ) -> Matter:
        statement = select(Matter).where(
            Matter.id == matter_id,
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        matter = await self.db.scalar(statement)
        if matter is None:
            raise LookupError("Caso não encontrado no workspace.")
        return matter

    async def _workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> Workspace:
        statement = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
        )
        if for_update:
            statement = statement.with_for_update()
        workspace = await self.db.scalar(statement)
        if workspace is None:
            raise LookupError("Workspace não encontrado.")
        return workspace

    async def _audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        actor_label: str,
        event_type: str,
        entity_id: UUID,
        summary: str,
        before: dict | None,
        after: dict | None,
        entity_type: str = "matter",
    ) -> None:
        actor_label = await resolve_human_actor_label(
            self.db,
            actor_user_id,
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                before_data=before or {},
                after_data=after or {},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()

    @staticmethod
    def _reference_values(record: dict[str, Any], source_hash: str) -> dict[str, Any]:
        return {
            "dataset_version": record["datasetVersion"],
            "source_sha256": source_hash,
            "product_name": record["productName"],
            "active_ingredient": record["activeIngredient"],
            "laboratory": record["laboratory"],
            "probable_area": record["probableArea"],
            "oncology": record["oncology"],
            "injunction_type": record["injunctionType"],
            "therapeutic_class_cmed": record["therapeuticClassCmed"],
            "cmed_presentation": record["cmedPresentation"],
            "anvisa_registration": record["anvisaRegistration"],
            "ggrem_code": record["ggremCode"],
            "pmvg_without_tax": Decimal(str(record["pmvgWithoutTax"])),
            "pmvg_18": Decimal(str(record["pmvg18"])),
            "pf_without_tax": Decimal(str(record["pfWithoutTax"])),
            "cap_applies": record["capApplies"],
            "hospital_restriction": record["hospitalRestriction"],
            "commercialized_2025": record["commercialized2025"],
            "label": record["label"],
            "practical_legal_observation": record["practicalLegalObservation"],
            "source_url": record["sourceUrl"],
            "source_dataset_label": record["sourceDatasetLabel"],
            "source_rows": record["sourceRows"],
            "source_ranks": record["sourceRanks"],
        }

    @staticmethod
    def _estimate_snapshot(estimate: MatterTreatmentEstimate | None) -> dict | None:
        if estimate is None:
            return None
        return {
            "id": str(estimate.id),
            "medicationReferenceId": str(estimate.medication_reference_id),
            "presentationsPerCycle": float(estimate.presentations_per_cycle),
            "cycleCount": estimate.cycle_count,
            "cycleIntervalDays": estimate.cycle_interval_days,
            "treatmentDurationDays": estimate.treatment_duration_days,
            "costPerPresentation": float(estimate.cost_per_presentation),
            "totalTreatmentCost": float(estimate.total_treatment_cost),
            "reviewStatus": estimate.review_status,
            "appliedToMatterEconomicValue": estimate.applied_to_matter_economic_value,
        }


__all__ = [
    "DEFAULT_LEGAL_PRICING_POLICY",
    "MedicationPricingService",
    "load_packaged_catalog",
]
