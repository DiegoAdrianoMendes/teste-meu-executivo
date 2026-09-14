"""Fail-honest public catalog and tenant commercial request workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.factory import get_saas_billing_gateway
from app.db.platform_control import set_commercial_provider_reconcile_context
from app.models.commercial_account_request import CommercialAccountRequest
from app.models.commercial_catalog import (
    CommercialContractLineStatus,
    CommercialContractProviderBinding,
    CommercialContractStatus,
    CommercialOfferFamily,
    CommercialProvider,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.platform_work_item import PlatformWorkItem
from app.schemas.commercial_catalog import (
    CommercialEntitlementSetting,
    WorkspaceContractLineWrite,
    WorkspaceContractRevisionPreviewRequest,
    WorkspaceContractRevisionRequest,
)
from app.schemas.commercial_self_service import (
    CommercialAccountPreviewRead,
    CommercialAccountRequestCreate,
    CommercialAccountRequestRead,
    PublicCommercialCatalogRead,
    PublicCommercialOfferRead,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogConflictError,
    CommercialCatalogNotFoundError,
    CommercialCatalogService,
)


class CommercialSelfServiceError(RuntimeError):
    code = "COMMERCIAL-SELF-SERVICE-ERROR"
    status_code = 422


class CommercialSelfServiceConflictError(CommercialSelfServiceError):
    code = "COMMERCIAL-REQUEST-CONFLICT"
    status_code = 409


class CommercialSelfServiceNotFoundError(CommercialSelfServiceError):
    code = "COMMERCIAL-REQUEST-NOT-FOUND"
    status_code = 404


def _quantity_label(key: str, setting: CommercialEntitlementSetting) -> str | None:
    if not setting.enabled:
        return None
    value = setting.limit_quantity
    labels = {
        "users": "usuários ativos",
        "workspaces": "unidades",
        "whatsapp_numbers": "números de WhatsApp",
        "agents": "agentes de IA",
        "automations": "automações ativas",
        "ai_conversations": "conversas com IA/mês",
        "active_tasks": "tarefas ativas",
    }
    label = labels.get(key)
    if label is None:
        return None
    return (
        f"{value:,} {label}".replace(",", ".")
        if value is not None
        else f"{label} sem limite contratual"
    )


class CommercialSelfService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.catalog = CommercialCatalogService(db)

    async def public_catalog(self) -> PublicCommercialCatalogRead:
        offers, _ = await self.catalog.list_offers(limit=20, public_only=True)
        items: list[PublicCommercialOfferRead] = []
        for offer in offers:
            highlights = [
                label
                for key, setting in offer.snapshot.entitlements.items()
                if (label := _quantity_label(key.value, setting)) is not None
            ][:6]
            items.append(
                PublicCommercialOfferRead(
                    sku=offer.sku,
                    name=offer.snapshot.name,
                    description=offer.snapshot.description,
                    family=offer.family.value,
                    kind=offer.kind.value,
                    is_recommended=offer.snapshot.is_recommended,
                    is_quote_only=offer.snapshot.is_quote_only,
                    price_monthly_cents=offer.snapshot.price_monthly_cents,
                    currency="BRL",
                    cta=offer.snapshot.cta,
                    highlights=highlights,
                )
            )
        return PublicCommercialCatalogRead(
            items=items,
            generated_at=datetime.now(UTC),
            state="available" if items else "not_published",
        )

    async def list_requests(
        self,
        workspace_id: UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CommercialAccountRequest], int]:
        filters = [CommercialAccountRequest.workspace_id == workspace_id]
        if status is not None:
            filters.append(CommercialAccountRequest.status == status)
        total = int(
            await self.db.scalar(
                select(func.count()).select_from(CommercialAccountRequest).where(*filters)
            )
            or 0
        )
        items = list(
            (
                await self.db.scalars(
                    select(CommercialAccountRequest)
                    .where(*filters)
                    .order_by(
                        CommercialAccountRequest.created_at.desc(),
                        CommercialAccountRequest.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return items, total

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

    async def preview(
        self, workspace_id: UUID, payload: CommercialAccountRequestCreate
    ) -> CommercialAccountPreviewRead:
        contract = await self.catalog.get_workspace_contract(workspace_id)
        if contract.version != payload.expected_contract_version:
            raise CommercialSelfServiceConflictError(
                "O contrato mudou. Recarregue a assinatura antes de continuar."
            )
        if payload.request_type == "cancel_contract":
            return CommercialAccountPreviewRead(
                request_type=payload.request_type,
                target_sku=None,
                contract=contract,
                revision=None,
                warnings=[
                    "O pedido não cancela imediatamente a conta nem apaga dados. "
                    "O time comercial revisará vigência, exportação e obrigações pendentes."
                ],
                eligible=contract.status.value != "cancelled",
            )

        public_catalog = await self.public_catalog()
        public_by_sku = {item.sku: item for item in public_catalog.items}
        lines = [self._line_write(line) for line in contract.lines]
        if payload.request_type == "change_base_plan":
            if not payload.target_sku or payload.target_sku not in public_by_sku:
                raise CommercialSelfServiceNotFoundError("Plano público não encontrado.")
            target = public_by_sku[payload.target_sku]
            if target.family != CommercialOfferFamily.crm.value:
                raise CommercialSelfServiceConflictError("A oferta escolhida não é um plano-base.")
            lines = [line for line in lines if not line.sku.startswith("crm_")]
            lines.insert(0, WorkspaceContractLineWrite(sku=payload.target_sku))
        elif payload.request_type == "activate_legal":
            if any(line.sku == "legal_360" for line in lines):
                raise CommercialSelfServiceConflictError("Jurídico 360 já faz parte do contrato.")
            # Legal 360 is quote-only: record a reviewable intent without
            # manufacturing a price or a contract line.
            return CommercialAccountPreviewRead(
                request_type=payload.request_type,
                target_sku="legal_360",
                contract=contract,
                revision=None,
                warnings=[
                    "Jurídico 360 é contratado por proposta; preço e limites serão "
                    "confirmados antes da ativação."
                ],
                eligible=True,
            )
        elif payload.request_type == "cancel_legal":
            if not any(line.sku == "legal_360" for line in lines):
                raise CommercialSelfServiceConflictError(
                    "Jurídico 360 não está ativo neste contrato."
                )
            lines = [line for line in lines if line.sku != "legal_360"]
            if not lines:
                raise CommercialSelfServiceConflictError(
                    "A remoção deixaria o contrato sem produto. "
                    "Solicite o encerramento do contrato."
                )
        else:
            raise CommercialSelfServiceConflictError("Tipo de solicitação comercial inválido.")

        try:
            revision = await self.catalog.preview_contract_revision(
                contract.id,
                WorkspaceContractRevisionPreviewRequest(
                    expected_version=contract.version,
                    lines=lines,
                ),
                preserve_exceptions=True,
            )
        except (CommercialCatalogConflictError, CommercialCatalogNotFoundError) as error:
            raise CommercialSelfServiceConflictError(str(error)) from error
        return CommercialAccountPreviewRead(
            request_type=payload.request_type,
            target_sku=payload.target_sku,
            contract=contract,
            revision=revision,
            warnings=list(revision.warnings) + list(revision.blockers),
            eligible=revision.eligible,
        )

    async def create_request(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        payload: CommercialAccountRequestCreate,
        idempotency_key: str,
    ) -> tuple[CommercialAccountRequest, bool]:
        key = idempotency_key.strip()
        if not key or len(key) > 200 or not key.isprintable():
            raise CommercialSelfServiceConflictError("Idempotency-Key inválida.")
        existing = await self.db.scalar(
            select(CommercialAccountRequest).where(
                CommercialAccountRequest.workspace_id == workspace_id,
                CommercialAccountRequest.idempotency_key == key,
            )
        )
        if existing is not None:
            same = (
                existing.requested_by_user_id == actor_user_id
                and existing.request_type == payload.request_type
                and existing.target_sku == payload.target_sku
                and existing.reason == payload.reason
                and existing.contract_version == payload.expected_contract_version
            )
            if not same:
                raise CommercialSelfServiceConflictError(
                    "A chave de idempotência já foi usada em outra solicitação."
                )
            return existing, True

        preview = await self.preview(workspace_id, payload)
        if not preview.eligible:
            raise CommercialSelfServiceConflictError(
                "A solicitação possui bloqueios. Revise a prévia antes de enviar."
            )
        obj = CommercialAccountRequest(
            workspace_id=workspace_id,
            requested_by_user_id=actor_user_id,
            request_type=payload.request_type,
            target_sku=payload.target_sku,
            reason=payload.reason,
            status="pending",
            contract_version=payload.expected_contract_version,
            preview_snapshot=preview.model_dump(mode="json"),
            idempotency_key=key,
            version=1,
        )
        self.db.add(obj)
        await self.db.flush()
        now = datetime.now(UTC)
        self.db.add(
            PlatformWorkItem(
                source_type="commercial_request",
                source_id=obj.id,
                source_version="1",
                dedupe_key=f"commercial_request:{obj.id}",
                kind="approval",
                state="open",
                priority="P2",
                workspace_id=workspace_id,
                requester_user_id=actor_user_id,
                safe_title="Solicitação comercial da conta",
                safe_summary={
                    "change_base_plan": "Alteração de plano-base",
                    "activate_legal": "Contratação do Jurídico 360",
                    "cancel_legal": "Cancelamento do Jurídico 360",
                    "cancel_contract": "Encerramento do contrato",
                }[payload.request_type],
                href=(
                    "/blackbox/workspaces?"
                    f"sourceType=commercial_request&sourceId={obj.id}"
                    f"&tab=commercial&focus=workspace&workspaceId={workspace_id}"
                ),
                required_module="commercial_contracts",
                required_action="view",
                allowed_commands=[],
                opened_at=now,
                source_updated_at=now,
                projected_at=now,
                projection_version=1,
                payload_safe={
                    "requestType": payload.request_type,
                    "targetSku": payload.target_sku,
                },
            )
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label="Administrador do workspace",
                event_type="commercial_change_requested",
                entity_type="commercial_account_request",
                entity_id=obj.id,
                summary="Solicitação comercial registrada para revisão do time da plataforma.",
                correlation_id=key,
                before_data=None,
                after_data={
                    "requestType": payload.request_type,
                    "targetSku": payload.target_sku,
                    "status": "pending",
                    "contractVersion": payload.expected_contract_version,
                },
            )
        )
        await self.db.flush()
        return obj, False

    async def _resolution_is_satisfied(self, obj: CommercialAccountRequest) -> bool:
        """Prove the requested contract state before an operator closes the request."""
        contract = await self.catalog.get_workspace_contract(obj.workspace_id)
        active_skus = {line.sku for line in contract.lines}
        if obj.request_type == "change_base_plan":
            return obj.target_sku in active_skus
        if obj.request_type == "activate_legal":
            return "legal_360" in active_skus
        if obj.request_type == "cancel_legal":
            return "legal_360" not in active_skus
        if obj.request_type == "cancel_contract":
            return contract.status.value == "cancelled"
        return False

    async def resolve(
        self,
        *,
        request_id: UUID,
        actor_user_id: UUID,
        expected_version: int,
        status: str,
        note: str,
    ) -> CommercialAccountRequest:
        obj = await self.db.scalar(
            select(CommercialAccountRequest)
            .where(CommercialAccountRequest.id == request_id)
            .with_for_update()
        )
        if obj is None:
            raise CommercialSelfServiceNotFoundError("Solicitação comercial não encontrada.")
        if obj.version != expected_version or obj.status != "pending":
            raise CommercialSelfServiceConflictError(
                "A solicitação mudou ou já foi encerrada. Recarregue antes de decidir."
            )
        if status == "resolved" and not await self._resolution_is_satisfied(obj):
            raise CommercialSelfServiceConflictError(
                "A alteração ainda não aparece no contrato canônico. "
                "Aplique a mudança na Conta 360 e tente novamente."
            )
        obj.status = status
        obj.resolved_by_user_id = actor_user_id
        obj.resolved_at = datetime.now(UTC)
        obj.resolution_note = note
        obj.version += 1
        item = await self.db.scalar(
            select(PlatformWorkItem).where(
                PlatformWorkItem.source_type == "commercial_request",
                PlatformWorkItem.source_id == obj.id,
            )
        )
        if item is not None:
            item.state = "done" if status == "resolved" else "cancelled"
            item.closed_at = obj.resolved_at
            item.source_version = str(obj.version)
            item.source_updated_at = obj.resolved_at
            item.projected_at = obj.resolved_at
            item.projection_version += 1
        await self.db.flush()
        return obj

    async def execute_provider_change(
        self,
        *,
        request_id: UUID,
        actor_user_id: UUID,
        expected_version: int,
        note: str,
    ) -> CommercialAccountRequest:
        """Executa no provedor e só então reconcilia o contrato canônico.

        O lock e a versão impedem duplo clique/conclusão concorrente. Stripe
        permanece no portal hospedado; Asaas e Mercado Pago usam suas APIs de
        assinatura. Descontos, créditos e bônus continuam preservados pelo
        resolver canônico, nunca pelo payload do navegador.
        """
        obj = await self.db.scalar(
            select(CommercialAccountRequest)
            .where(CommercialAccountRequest.id == request_id)
            .with_for_update()
        )
        if obj is None:
            raise CommercialSelfServiceNotFoundError("Solicitação comercial não encontrada.")
        if obj.version != expected_version or obj.status != "pending":
            raise CommercialSelfServiceConflictError(
                "A solicitação mudou ou já foi processada. Recarregue antes de executar."
            )
        if obj.request_type not in {"change_base_plan", "cancel_contract"}:
            raise CommercialSelfServiceConflictError(
                "Esta solicitação não altera a cobrança recorrente do provedor."
            )
        contract = await self.catalog.get_workspace_contract(obj.workspace_id)
        if contract.version != obj.contract_version:
            raise CommercialSelfServiceConflictError(
                "O contrato mudou depois da solicitação. Gere uma nova solicitação."
            )
        binding = await self.db.scalar(
            select(CommercialContractProviderBinding)
            .where(
                CommercialContractProviderBinding.contract_id == contract.id,
                CommercialContractProviderBinding.provider_subscription_id.is_not(None),
            )
            .with_for_update()
        )
        if binding is None or not binding.provider_subscription_id:
            raise CommercialSelfServiceConflictError(
                "A conta não possui assinatura vinculada a um provedor."
            )
        if binding.provider == CommercialProvider.stripe:
            raise CommercialSelfServiceConflictError(
                "A assinatura Stripe deve ser alterada no portal hospedado."
            )

        gateway = get_saas_billing_gateway(binding.provider)
        command_key = f"commercial-request:{obj.id}:v{obj.version}"
        if obj.request_type == "change_base_plan":
            preview = await self.preview(
                obj.workspace_id,
                CommercialAccountRequestCreate(
                    request_type=obj.request_type,
                    target_sku=obj.target_sku,
                    reason=obj.reason,
                    expected_contract_version=contract.version,
                ),
            )
            if preview.revision is None or not preview.revision.eligible:
                raise CommercialSelfServiceConflictError(
                    "A troca de plano não está mais elegível. Gere uma nova prévia."
                )
            amount_cents = preview.revision.price.recurring_price_cents
            if amount_cents <= 0:
                raise CommercialSelfServiceConflictError(
                    "Assinatura externa exige valor recorrente positivo."
                )
            result = await gateway.update_subscription_amount(
                subscription_id=binding.provider_subscription_id,
                amount_cents=amount_cents,
                idempotency_key=command_key,
            )
            lines = [self._line_write(line) for line in preview.revision.lines]
            await set_commercial_provider_reconcile_context(self.db)
            await self.catalog.revise_contract(
                contract_id=contract.id,
                expected_version=contract.version,
                payload=WorkspaceContractRevisionRequest(lines=lines, reason=note),
                actor_user_id=actor_user_id,
                idempotency_key=command_key,
                operation=f"{binding.provider.value}_provider_plan_change",
                event_type=f"{binding.provider.value}_provider_plan_changed",
                preserve_exceptions=True,
            )
        else:
            result = await gateway.cancel_subscription(
                subscription_id=binding.provider_subscription_id,
                idempotency_key=command_key,
            )
            lines = [
                self._line_write(line)
                for line in contract.lines
                if line.status == CommercialContractLineStatus.active
            ]
            await set_commercial_provider_reconcile_context(self.db)
            await self.catalog.revise_contract(
                contract_id=contract.id,
                expected_version=contract.version,
                payload=WorkspaceContractRevisionRequest(lines=lines, reason=note),
                actor_user_id=actor_user_id,
                idempotency_key=command_key,
                operation=f"{binding.provider.value}_provider_contract_cancel",
                event_type=f"{binding.provider.value}_provider_contract_cancelled",
                preserve_exceptions=True,
                target_status=CommercialContractStatus.cancelled,
            )
        binding.provider_status = result.provider_status
        binding.version += 1
        return await self.resolve(
            request_id=obj.id,
            actor_user_id=actor_user_id,
            expected_version=obj.version,
            status="resolved",
            note=note,
        )


def present_request(obj: CommercialAccountRequest) -> CommercialAccountRequestRead:
    return CommercialAccountRequestRead.model_validate(obj)
