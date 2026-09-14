"""Porta de gateway de pagamentos — contrato único para Asaas e futuros
provedores (Mercado Pago, Pagar.me).

Tipos de domínio próprios: nunca vazamos o shape bruto do Asaas para o resto
do sistema. Campos marcados "a confirmar no sandbox" no plano
(`docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md`, seção 3) são opcionais e
tolerantes — o adapter real deve aceitar ausência deles sem quebrar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Protocol


class BillingType(str, Enum):
    """Meio de cobrança aceito pelo Asaas (`billingType`)."""

    BOLETO = "BOLETO"
    CREDIT_CARD = "CREDIT_CARD"
    PIX = "PIX"
    UNDEFINED = "UNDEFINED"  # cliente escolhe o meio no checkout


@dataclass(frozen=True)
class DiscountTerms:
    """Desconto por pagamento antecipado. Shape exato a confirmar no sandbox
    (plano §3.2) — todos os campos são opcionais/tolerantes."""

    value: Optional[Decimal] = None
    due_date_limit_days: Optional[int] = None
    type: Optional[str] = None  # "FIXED" | "PERCENTAGE" (a confirmar)


@dataclass(frozen=True)
class FineTerms:
    """Multa por atraso. Limite legal BR: até 2% (relação de consumo)."""

    value: Optional[Decimal] = None
    type: Optional[str] = None  # "FIXED" | "PERCENTAGE" (a confirmar)


@dataclass(frozen=True)
class InterestTerms:
    """Juros de mora a.m. Limite legal BR: até 1% a.m. (relação de consumo)."""

    value: Optional[Decimal] = None


@dataclass(frozen=True)
class SplitTerms:
    """Repasse a outra conta Asaas (fase 2 — repasse a sócio/parceiro)."""

    wallet_id: str
    fixed_value: Optional[Decimal] = None
    percentual_value: Optional[Decimal] = None


@dataclass(frozen=True)
class CustomerRequest:
    """Payload mínimo para criar/sincronizar um customer Asaas a partir de um
    Client do Meu Executivo."""

    name: str
    external_reference: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    cpf_cnpj: Optional[str] = None
    address_line: Optional[str] = None
    notification_disabled: Optional[bool] = None


@dataclass(frozen=True)
class CustomerResult:
    external_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChargeRequest:
    """Payload de domínio para `POST /v3/payments` (plano §3.2).

    Um billingType por cobrança. Parcelamento: `installment_count` +
    (`installment_value` OU `total_value`, dividido igualmente pelo Asaas).
    `external_reference` é a chave de conciliação (nosso `entry_id`/id).
    """

    customer_external_id: str
    billing_type: BillingType
    value: Decimal
    due_date: date
    external_reference: Optional[str] = None
    description: Optional[str] = None
    installment_count: Optional[int] = None
    installment_value: Optional[Decimal] = None
    total_value: Optional[Decimal] = None
    discount: Optional[DiscountTerms] = None
    fine: Optional[FineTerms] = None
    interest: Optional[InterestTerms] = None
    split: tuple[SplitTerms, ...] = field(default_factory=tuple)
    callback_url: Optional[str] = None
    callback_auto_redirect: Optional[bool] = None


@dataclass(frozen=True)
class ChargeResult:
    """Resposta normalizada do create de cobrança.

    Campos como `invoice_url`/`bank_slip_url` não vieram listados na leitura
    da doc (plano §3.2, "a confirmar no sandbox") — mantidos opcionais.
    """

    external_id: str
    status: str
    billing_type: Optional[BillingType] = None
    value: Optional[Decimal] = None
    due_date: Optional[date] = None
    invoice_url: Optional[str] = None
    bank_slip_url: Optional[str] = None
    external_reference: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PixQrCodeResult:
    """`GET /v3/payments/{id}/pixQrCode` — endpoint separado (plano §3.2).
    QR dinâmico, uso único, válido até 12 meses após o vencimento."""

    encoded_image: Optional[str] = None  # base64
    payload: Optional[str] = None  # copia-e-cola
    expiration_date: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubscriptionRequest:
    """`POST /v3/subscriptions` (plano §3.4) — agendador de cobranças."""

    customer_external_id: str
    billing_type: BillingType
    value: Decimal
    cycle: str  # WEEKLY|BIWEEKLY|MONTHLY|BIMONTHLY|QUARTERLY|SEMIANNUALLY|YEARLY
    next_due_date: date
    external_reference: Optional[str] = None
    description: Optional[str] = None
    end_date: Optional[date] = None
    max_payments: Optional[int] = None
    discount: Optional[DiscountTerms] = None
    fine: Optional[FineTerms] = None
    interest: Optional[InterestTerms] = None
    split: tuple[SplitTerms, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SubscriptionResult:
    external_id: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentLinkRequest:
    """`POST /v3/paymentLinks` (plano §3.5). `charge_type` DETACHED
    (avulsa) | RECURRENT (assinatura) | INSTALLMENT (parcelado)."""

    name: str
    charge_type: str  # DETACHED | RECURRENT | INSTALLMENT
    billing_type: BillingType = BillingType.UNDEFINED
    value: Optional[Decimal] = None  # ausente = pagador digita o valor
    description: Optional[str] = None
    due_date_limit_days: Optional[int] = None
    max_installment_count: Optional[int] = None
    end_date: Optional[date] = None
    external_reference: Optional[str] = None


@dataclass(frozen=True)
class PaymentLinkResult:
    external_id: str
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StatementEntry:
    """Uma linha do extrato Asaas (`GET /v3/financialTransactions`, §3.8).
    Valor com sinal: crédito positivo, débito negativo."""

    transaction_type: str
    value: Decimal
    date: Optional[date] = None
    description: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StatementPage:
    entries: tuple[StatementEntry, ...]
    total_count: Optional[int] = None
    has_more: bool = False


class PaymentGatewayPort(Protocol):
    """Contrato único que qualquer gateway de pagamento deve implementar.

    Implementações reais (ex.: `AsaasAdapter`) devem fazer o gate
    `external_delivery_allowed()` na frente de TODA chamada de rede.
    """

    async def create_customer(self, request: CustomerRequest) -> CustomerResult: ...

    async def create_charge(self, request: ChargeRequest) -> ChargeResult: ...

    async def get_pix_qr(self, charge_external_id: str) -> PixQrCodeResult: ...

    async def create_subscription(
        self, request: SubscriptionRequest
    ) -> SubscriptionResult: ...

    async def create_payment_link(
        self, request: PaymentLinkRequest
    ) -> PaymentLinkResult: ...

    async def cancel_charge(self, charge_external_id: str) -> ChargeResult: ...

    async def get_statement(
        self,
        start: date,
        finish: date,
        offset: int = 0,
        limit: int = 100,
    ) -> StatementPage: ...
