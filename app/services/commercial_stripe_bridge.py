"""Canonical bridge for hosted SaaS checkout and provider reconciliation.

The historical name is preserved for compatibility while the implementation
now isolates Stripe, Asaas and Mercado Pago behind the same contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.models.commercial_catalog import (
    CommercialBillingInterval,
    CommercialCheckoutIntent,
    CommercialCheckoutIntentStatus,
    CommercialContractProviderBinding,
    CommercialContractStatus,
    CommercialOffer,
    CommercialOfferFamily,
    CommercialOfferVersion,
    CommercialProvider,
    CommercialProviderBindingStatus,
    CommercialProviderPriceBinding,
)
from app.schemas.commercial_catalog import (
    WorkspaceContractLineWrite,
    WorkspaceContractRevisionPreviewRequest,
)
from app.services.commercial_catalog_service import CommercialCatalogService


class CommercialStripeBridgeError(RuntimeError):
    code = "COMMERCIAL-STRIPE-ERROR"
    status_code = 422


class CommercialStripeBridgeConflict(CommercialStripeBridgeError):  # noqa: N818
    code = "COMMERCIAL-STRIPE-CONFLICT"
    status_code = 409


class CommercialStripeBridgeUnavailable(CommercialStripeBridgeError):  # noqa: N818
    code = "COMMERCIAL-STRIPE-UNAVAILABLE"
    status_code = 503


@dataclass(frozen=True)
class StripeProviderState:
    state: str
    message: str
    available: bool


class CommercialStripeBridge:
    def __init__(
        self, db: AsyncSession, provider: CommercialProvider = CommercialProvider.stripe
    ):
        self.db = db
        self.catalog = CommercialCatalogService(db)
        self.provider = provider

    @property
    def provider_label(self) -> str:
        return {
            CommercialProvider.stripe: "Stripe",
            CommercialProvider.asaas: "Asaas",
            CommercialProvider.mercado_pago: "Mercado Pago",
        }[self.provider]

    def _credentials_ready(self) -> bool:
        if self.provider == CommercialProvider.stripe:
            return bool(
                settings.STRIPE_SECRET_KEY.strip()
                and settings.STRIPE_WEBHOOK_SECRET.strip()
            )
        if self.provider == CommercialProvider.asaas:
            return bool(
                settings.SAAS_ASAAS_ACCESS_TOKEN.strip()
                and settings.SAAS_ASAAS_WEBHOOK_TOKEN.strip()
            )
        return bool(
            settings.MERCADO_PAGO_ACCESS_TOKEN.strip()
            and settings.MERCADO_PAGO_WEBHOOK_SECRET.strip()
            and settings.PUBLIC_API_URL.strip().startswith("https://")
        )

    @staticmethod
    def _hash(value: dict) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _line_write(line) -> WorkspaceContractLineWrite:
        return WorkspaceContractLineWrite(
            sku=line.sku,
            policy_version_id=line.policy_version_id,
            quantity=line.quantity,
            contracted_price_cents=line.contracted_price_cents,
            billing_interval=line.billing_interval,
            entitlement_overrides=line.entitlement_overrides,
            effective_from=line.effective_from,
            effective_until=line.effective_until,
        )

    async def latest_offer(self, sku: str) -> tuple[CommercialOffer, CommercialOfferVersion]:
        row = (
            await self.db.execute(
                select(CommercialOffer, CommercialOfferVersion)
                .join(CommercialOfferVersion, CommercialOfferVersion.offer_id == CommercialOffer.id)
                .where(CommercialOffer.sku == sku)
                .order_by(CommercialOfferVersion.version.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            raise CommercialStripeBridgeConflict("Oferta comercial não encontrada.")
        return row[0], row[1]

    async def active_price_binding(
        self, offer_version_id: UUID
    ) -> CommercialProviderPriceBinding | None:
        return await self.db.scalar(
            select(CommercialProviderPriceBinding).where(
                CommercialProviderPriceBinding.offer_version_id == offer_version_id,
                CommercialProviderPriceBinding.provider == self.provider,
                CommercialProviderPriceBinding.billing_interval
                == CommercialBillingInterval.monthly,
                CommercialProviderPriceBinding.status == CommercialProviderBindingStatus.active,
            )
        )

    async def provider_state(self) -> StripeProviderState:
        if not self._credentials_ready():
            return StripeProviderState(
                "not_configured",
                f"{self.provider_label} ainda não foi configurado neste ambiente; "
                "nenhuma cobrança será iniciada.",
                False,
            )
        if not external_delivery_allowed():
            return StripeProviderState(
                "gate_closed",
                "Entregas externas estão desabilitadas; o checkout permanece bloqueado.",
                False,
            )
        latest_versions = (
            select(
                CommercialOfferVersion.offer_id,
                func.max(CommercialOfferVersion.version).label("latest_version"),
            )
            .group_by(CommercialOfferVersion.offer_id)
            .subquery()
        )
        active_public_crm = list(
            (
                await self.db.execute(
                    select(CommercialOfferVersion.id)
                    .join(CommercialOffer, CommercialOffer.id == CommercialOfferVersion.offer_id)
                    .join(
                        latest_versions,
                        (latest_versions.c.offer_id == CommercialOfferVersion.offer_id)
                        & (latest_versions.c.latest_version == CommercialOfferVersion.version),
                    )
                    .where(
                        CommercialOffer.family == CommercialOfferFamily.crm,
                        CommercialOfferVersion.is_public.is_(True),
                        CommercialOfferVersion.is_active.is_(True),
                        CommercialOfferVersion.is_quote_only.is_(False),
                    )
                )
            ).scalars()
        )
        bound = (
            set(
                (
                    await self.db.execute(
                        select(CommercialProviderPriceBinding.offer_version_id).where(
                            CommercialProviderPriceBinding.offer_version_id.in_(active_public_crm),
                            CommercialProviderPriceBinding.provider == self.provider,
                            CommercialProviderPriceBinding.status
                            == CommercialProviderBindingStatus.active,
                        )
                    )
                ).scalars()
            )
            if active_public_crm
            else set()
        )
        if not bound:
            return StripeProviderState(
                "not_configured",
                f"Nenhum preço {self.provider_label} está vinculado às ofertas publicadas.",
                False,
            )
        if len(bound) != len(set(active_public_crm)):
            return StripeProviderState(
                "partially_bound",
                "O checkout está disponível apenas para ofertas com preço "
                f"{self.provider_label} configurado.",
                True,
            )
        return StripeProviderState(
            "configured",
            f"Checkout seguro hospedado pelo {self.provider_label} disponível "
            "para os planos publicados.",
            True,
        )

    async def list_price_bindings(self) -> list[tuple[CommercialProviderPriceBinding, str, int]]:
        rows = await self.db.execute(
            select(
                CommercialProviderPriceBinding, CommercialOffer.sku, CommercialOfferVersion.version
            )
            .join(CommercialOffer, CommercialOffer.id == CommercialProviderPriceBinding.offer_id)
            .join(
                CommercialOfferVersion,
                CommercialOfferVersion.id == CommercialProviderPriceBinding.offer_version_id,
            )
            .order_by(CommercialOffer.sku, CommercialOfferVersion.version.desc())
        )
        return [(binding, sku, version) for binding, sku, version in rows.all()]

    async def configure_price_binding(
        self,
        *,
        sku: str,
        provider_price_id: str | None,
        actor_user_id: UUID,
        reason: str,
    ) -> CommercialProviderPriceBinding:
        offer, version = await self.latest_offer(sku)
        if version.is_quote_only or not version.is_active:
            raise CommercialStripeBridgeConflict(
                "Somente oferta ativa com preço publicado aceita checkout automático."
            )
        if self.provider == CommercialProvider.stripe:
            if not provider_price_id:
                raise CommercialStripeBridgeConflict(
                    "O Price ID publicado pelo Stripe é obrigatório."
                )
        else:
            # Asaas e Mercado Pago recebem valor, recorrência e moeda do catálogo
            # canônico a cada checkout; eles não possuem um objeto Price externo.
            provider_price_id = provider_price_id or f"{self.provider.value}:{version.id}"
        current = await self.active_price_binding(version.id)
        if current is not None and current.provider_price_id == provider_price_id:
            return current
        if current is not None:
            current.status = CommercialProviderBindingStatus.inactive
            current.version += 1
        binding = CommercialProviderPriceBinding(
            offer_id=offer.id,
            offer_version_id=version.id,
            provider=self.provider,
            billing_interval=CommercialBillingInterval.monthly,
            provider_price_id=provider_price_id,
            status=CommercialProviderBindingStatus.active,
            reason=reason,
            configured_by_user_id=actor_user_id,
        )
        self.db.add(binding)
        await self.db.flush()
        return binding

    async def prepare_checkout(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        target_sku: str,
        expected_contract_version: int,
        reason: str,
        idempotency_key: str,
    ) -> tuple[CommercialCheckoutIntent, CommercialProviderPriceBinding, bool]:
        request_hash = self._hash(
            {
                "workspace_id": str(workspace_id),
                "target_sku": target_sku,
                "expected_contract_version": expected_contract_version,
                "reason": reason,
                "provider": self.provider.value,
            }
        )
        existing = await self.db.scalar(
            select(CommercialCheckoutIntent).where(
                CommercialCheckoutIntent.workspace_id == workspace_id,
                CommercialCheckoutIntent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise CommercialStripeBridgeConflict(
                    "A chave de idempotência já foi usada com outra solicitação."
                )
            binding = await self.active_price_binding(existing.target_offer_version_id)
            if binding is None:
                raise CommercialStripeBridgeUnavailable(
                    "O preço vinculado a esta tentativa não está mais disponível."
                )
            return existing, binding, True

        contract = await self.catalog.get_workspace_contract(workspace_id)
        if contract.version != expected_contract_version:
            raise CommercialStripeBridgeConflict(
                "O contrato mudou. Recarregue antes de iniciar o checkout."
            )
        provider_binding = await self.contract_binding(contract.id, any_provider=True)
        if provider_binding is not None and provider_binding.provider_subscription_id:
            raise CommercialStripeBridgeConflict(
                f"A assinatura já está vinculada ao {provider_binding.provider.value}. "
                "Use a administração de cobrança "
                "para evitar duplicidade."
            )
        concurrent_intent = await self.db.scalar(
            select(CommercialCheckoutIntent.id).where(
                CommercialCheckoutIntent.contract_id == contract.id,
                CommercialCheckoutIntent.status.in_(
                    [
                        CommercialCheckoutIntentStatus.prepared,
                        CommercialCheckoutIntentStatus.session_created,
                    ]
                ),
            ).limit(1)
        )
        if concurrent_intent is not None:
            raise CommercialStripeBridgeConflict(
                "Já existe um checkout de assinatura em andamento para este contrato."
            )
        offer, version = await self.latest_offer(target_sku)
        if (
            offer.family != CommercialOfferFamily.crm
            or not version.is_public
            or not version.is_active
            or version.is_quote_only
            or CommercialBillingInterval.monthly.value not in version.available_intervals
        ):
            raise CommercialStripeBridgeConflict(
                "A oferta escolhida não aceita checkout automático mensal."
            )
        binding = await self.active_price_binding(version.id)
        if binding is None:
            raise CommercialStripeBridgeUnavailable(
                f"Este plano ainda não possui preço {self.provider_label} configurado."
            )

        current_base = next(
            (line for line in contract.lines if line.offer_family == CommercialOfferFamily.crm),
            None,
        )
        if current_base is not None and current_base.sku == target_sku:
            if contract.status not in {
                CommercialContractStatus.trialing,
                CommercialContractStatus.past_due,
                CommercialContractStatus.paused,
            }:
                raise CommercialStripeBridgeConflict("Este plano já está ativo no contrato.")
            target_lines = [self._line_write(line) for line in contract.lines]
        else:
            lines = [
                self._line_write(line)
                for line in contract.lines
                if line.offer_family != CommercialOfferFamily.crm
            ]
            lines.insert(0, WorkspaceContractLineWrite(sku=target_sku))
            preview = await self.catalog.preview_contract_revision(
                contract.id,
                WorkspaceContractRevisionPreviewRequest(
                    expected_version=contract.version,
                    lines=lines,
                ),
                preserve_exceptions=True,
            )
            if not preview.eligible:
                raise CommercialStripeBridgeConflict(
                    " ".join(preview.blockers) or "A alteração comercial não está elegível."
                )
            target_lines = [self._line_write(line) for line in preview.lines]

        intent = CommercialCheckoutIntent(
            workspace_id=workspace_id,
            contract_id=contract.id,
            actor_user_id=actor_user_id,
            provider=self.provider,
            target_sku=target_sku,
            target_offer_version_id=version.id,
            expected_contract_version=contract.version,
            target_lines=[line.model_dump(mode="json") for line in target_lines],
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=CommercialCheckoutIntentStatus.prepared,
            reason=reason,
        )
        self.db.add(intent)
        await self.db.flush()
        return intent, binding, False

    async def contract_binding(
        self, contract_id: UUID, *, any_provider: bool = False
    ) -> CommercialContractProviderBinding | None:
        query = select(CommercialContractProviderBinding).where(
            CommercialContractProviderBinding.contract_id == contract_id
        )
        if not any_provider:
            query = query.where(
                CommercialContractProviderBinding.provider == self.provider
            )
        return await self.db.scalar(
            query.order_by(CommercialContractProviderBinding.updated_at.desc())
        )
