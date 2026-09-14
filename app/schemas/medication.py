"""Schemas do catálogo CMED, cálculo de tratamento e política comercial."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

ReviewStatus = Literal["rascunho", "revisado", "aprovado", "bloqueado"]


class MedicationReferenceRead(CamelCaseSchema):
    id: UUID
    catalog_key: str
    dataset_version: str
    product_name: str
    active_ingredient: str | None = None
    laboratory: str | None = None
    probable_area: str | None = None
    oncology: bool
    injunction_type: str | None = None
    therapeutic_class_cmed: str | None = None
    cmed_presentation: str
    anvisa_registration: str | None = None
    ggrem_code: str | None = None
    pmvg_without_tax: float
    pmvg_18: float | None = None
    pf_without_tax: float | None = None
    cap_applies: bool
    hospital_restriction: bool
    commercialized_2025: bool
    label: str | None = None
    practical_legal_observation: str | None = None
    source_url: str
    source_dataset_label: str
    source_rows: list[dict] = Field(default_factory=list)
    source_ranks: dict = Field(default_factory=dict)


class MedicationCatalogStatusRead(CamelCaseSchema):
    dataset_version: str
    source_sha256: str
    record_count: int
    oncology_count: int
    source_filename: str
    cmed_published_at: str
    source_url: str
    price_unit: str
    calculation_formula: str
    limitations: list[str]


class TreatmentEstimateUpsert(WriteSchema):
    medication_reference_id: UUID
    presentations_per_cycle: float = Field(gt=0, le=100_000)
    cycle_count: int = Field(gt=0, le=10_000)
    cycle_interval_days: int | None = Field(default=None, ge=1, le=3650)
    treatment_duration_days: int | None = Field(default=None, ge=1, le=36500)
    prescription_reference: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    review_status: ReviewStatus = "rascunho"
    apply_to_matter_economic_value: bool = False


class MatterTreatmentEstimateRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    medication_reference_id: UUID
    medication: MedicationReferenceRead
    presentations_per_cycle: float
    cycle_count: int
    cycle_interval_days: int | None = None
    treatment_duration_days: int | None = None
    cost_per_presentation: float
    total_treatment_cost: float
    calculation_formula: str
    prescription_reference: str | None = None
    notes: str | None = None
    review_status: ReviewStatus
    applied_to_matter_economic_value: bool
    source_snapshot: dict
    reviewed_by_user_id: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LegalPricingPolicyRead(CamelCaseSchema):
    minimum_initial_fee: float = 4_000
    maximum_initial_fee: float | None = None
    allow_success_only: bool = True
    success_only_moral_damages_percentage: float = 100
    allow_injunction_milestone_fee: bool = True
    oncology_obligation_fee_minimum: float = 100_000
    oncology_obligation_fee_maximum: float = 300_000
    require_treatment_cycle_for_medication_value: bool = True
    require_human_review: bool = True
    currency: str = "BRL"
    policy_note: str = (
        "Entrada abaixo do mínimo somente no modo integralmente no êxito; "
        "valores de obrigação de fazer dependem do custo prescrito e de aprovação humana."
    )


class LegalPricingPolicyUpdate(WriteSchema):
    minimum_initial_fee: float = Field(ge=0, le=1_000_000_000)
    maximum_initial_fee: float | None = Field(default=None, ge=0, le=1_000_000_000)
    allow_success_only: bool = True
    success_only_moral_damages_percentage: float = Field(default=100, ge=0, le=100)
    allow_injunction_milestone_fee: bool = True
    oncology_obligation_fee_minimum: float = Field(default=100_000, ge=0)
    oncology_obligation_fee_maximum: float = Field(default=300_000, ge=0)
    require_treatment_cycle_for_medication_value: bool = True
    require_human_review: bool = True
    currency: str = Field(default="BRL", pattern="^BRL$")
    policy_note: str = Field(default=LegalPricingPolicyRead().policy_note, max_length=2000)

    @model_validator(mode="after")
    def validate_ranges(self) -> "LegalPricingPolicyUpdate":
        if (
            self.maximum_initial_fee is not None
            and self.maximum_initial_fee < self.minimum_initial_fee
        ):
            raise ValueError("O teto de entrada não pode ser inferior ao mínimo.")
        if self.oncology_obligation_fee_maximum < self.oncology_obligation_fee_minimum:
            raise ValueError("A faixa oncológica máxima não pode ser inferior à mínima.")
        return self


__all__ = [
    "LegalPricingPolicyRead",
    "LegalPricingPolicyUpdate",
    "MatterTreatmentEstimateRead",
    "MedicationCatalogStatusRead",
    "MedicationReferenceRead",
    "TreatmentEstimateUpsert",
]
