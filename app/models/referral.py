"""
Modelos: ReferralPartner / MatterReferral / ReferralCommission
Indicações com comissão (F13) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F13.

`ReferralPartner` cadastra quem indica casos (contato, cliente, parceiro ou
advogado) e o acordo de comissão (percentual ou fixo). `MatterReferral` liga
um caso (1:1, `matter_id` UNIQUE) ao parceiro que o indicou. `ReferralCommission`
é a trava de idempotência: uma linha por `(workspace_id, charge_id)`, garantindo
que reprocessar o mesmo webhook de pagamento nunca gere duas despesas de
comissão para a mesma cobrança.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.finance import FinanceEntry
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class ReferralPartnerType(str, enum.Enum):
    contato = "contato"
    cliente = "cliente"
    parceiro = "parceiro"
    advogado = "advogado"


class ReferralCommissionType(str, enum.Enum):
    percentual = "percentual"
    fixo = "fixo"


class ReferralPartner(UUIDMixin, TimestampMixin, Base):
    """Parceiro/pessoa que indica casos ao escritório, com acordo de
    comissão. `comissao_valor` é sempre definido por humano (nunca
    inferido automaticamente)."""

    __tablename__ = "referral_partners"
    __table_args__ = (
        Index("ix_referral_partners_workspace_ativo", "workspace_id", "ativo"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    tipo: Mapped[ReferralPartnerType] = mapped_column(
        Enum(ReferralPartnerType, name="referral_partner_type_enum"),
        nullable=False,
        default=ReferralPartnerType.contato,
    )
    contato_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    comissao_tipo: Mapped[ReferralCommissionType] = mapped_column(
        Enum(ReferralCommissionType, name="referral_commission_type_enum"),
        nullable=False,
        default=ReferralCommissionType.percentual,
    )
    comissao_valor: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)

    vigencia_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    vigencia_fim: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter_referrals: Mapped[list["MatterReferral"]] = relationship(
        "MatterReferral", back_populates="partner"
    )

    def vigente_em(self, referencia: date) -> bool:
        if not self.ativo:
            return False
        if referencia < self.vigencia_inicio:
            return False
        if self.vigencia_fim is not None and referencia > self.vigencia_fim:
            return False
        return True


class MatterReferral(UUIDMixin, TimestampMixin, Base):
    """Liga um caso ao parceiro que o indicou (1:1 — um caso tem no máximo
    uma origem de indicação ativa por vez)."""

    __tablename__ = "matter_referrals"
    __table_args__ = (
        UniqueConstraint("matter_id", name="uq_matter_referrals_matter_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    partner_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_partners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    registrado_por: Mapped[Optional[UUID]] = mapped_column(nullable=True)
    data: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter: Mapped["Matter"] = relationship("Matter")
    partner: Mapped["ReferralPartner"] = relationship(
        "ReferralPartner", back_populates="matter_referrals"
    )


class ReferralCommission(UUIDMixin, TimestampMixin, Base):
    """Trava de idempotência: uma linha por `(workspace_id, charge_id)`.
    Reprocessar o mesmo webhook de PAYMENT_RECEIVED (retries, reentrega)
    nunca cria uma segunda despesa de comissão para a mesma cobrança."""

    __tablename__ = "referral_commissions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "charge_id", name="uq_referral_commissions_workspace_charge"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    partner_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_partners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    charge_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_charges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expense_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    valor_base: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    comissao_tipo: Mapped[ReferralCommissionType] = mapped_column(
        Enum(ReferralCommissionType, name="referral_commission_type_enum", create_type=False),
        nullable=False,
    )
    comissao_valor_calculado: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    commission_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter: Mapped["Matter"] = relationship("Matter")
    partner: Mapped["ReferralPartner"] = relationship("ReferralPartner")
    expense_entry: Mapped["FinanceEntry"] = relationship("FinanceEntry")
