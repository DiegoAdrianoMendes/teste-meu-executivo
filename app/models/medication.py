"""Catálogo CMED compartilhado e estimativas de tratamento isoladas por caso."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User


class MedicationReference(UUIDMixin, TimestampMixin, Base):
    """Uma apresentação CMED, sem qualquer dado de cliente ou workspace."""

    __tablename__ = "medication_references"
    __table_args__ = (
        UniqueConstraint("catalog_key", name="uq_medication_reference_catalog_key"),
        Index("ix_medication_reference_dataset_product", "dataset_version", "product_name"),
        Index("ix_medication_reference_active_ingredient", "active_ingredient"),
        Index("ix_medication_reference_ggrem", "ggrem_code"),
    )

    catalog_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(96), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(300), nullable=False)
    active_ingredient: Mapped[str | None] = mapped_column(String(500), nullable=True)
    laboratory: Mapped[str | None] = mapped_column(String(500), nullable=True)
    probable_area: Mapped[str | None] = mapped_column(String(220), nullable=True)
    oncology: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    injunction_type: Mapped[str | None] = mapped_column(String(300), nullable=True)
    therapeutic_class_cmed: Mapped[str | None] = mapped_column(Text, nullable=True)
    cmed_presentation: Mapped[str] = mapped_column(Text, nullable=False)
    anvisa_registration: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ggrem_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pmvg_without_tax: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    pmvg_18: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    pf_without_tax: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cap_applies: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hospital_restriction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commercialized_2025: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    practical_legal_observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_dataset_label: Mapped[str] = mapped_column(String(220), nullable=False)
    source_rows: Mapped[list[dict]] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    source_ranks: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class MatterTreatmentEstimate(UUIDMixin, TimestampMixin, Base):
    """Cálculo explícito do custo prescrito, separado do catálogo compartilhado."""

    __tablename__ = "matter_treatment_estimates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "matter_id",
            "medication_reference_id",
            name="uq_matter_treatment_estimate_current",
        ),
        Index("ix_treatment_estimate_workspace_matter", "workspace_id", "matter_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    matter_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("matters.id", ondelete="CASCADE"), nullable=False
    )
    medication_reference_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("medication_references.id", ondelete="RESTRICT"),
        nullable=False,
    )
    presentations_per_cycle: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    cycle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    treatment_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_per_presentation: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    total_treatment_cost: Mapped[Decimal] = mapped_column(Numeric(22, 2), nullable=False)
    calculation_formula: Mapped[str] = mapped_column(Text, nullable=False)
    prescription_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="rascunho", index=True
    )
    applied_to_matter_economic_value: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    source_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    matter: Mapped["Matter"] = relationship("Matter", back_populates="treatment_estimates")
    medication: Mapped[MedicationReference] = relationship("MedicationReference")
    reviewed_by: Mapped["User | None"] = relationship("User")


__all__ = ["MatterTreatmentEstimate", "MedicationReference"]
