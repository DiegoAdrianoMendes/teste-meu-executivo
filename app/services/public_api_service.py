"""
Service da API externa oficial por workspace.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging_privacy import safe_error_type
from app.core.public_api_policy import (
    MUTABLE_PUBLIC_API_SCOPES,
    SENSITIVE_PUBLIC_API_SCOPES,
)
from app.core.rbac import get_effective_permissions
from app.db.tenant import set_workspace_context
from app.models.client import Client
from app.models.matter import KanbanColumnId, PracticeArea
from app.models.public_api import (
    PublicApiMutationReceipt,
    PublicApiMutationState,
    PublicApiRequestLog,
    WorkspacePublicApiToken,
)
from app.models.user import User
from app.models.whatsapp import (
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignAudience,
)
from app.models.whatsapp_note import WhatsAppConversationNote
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.matter import MatterCreate, MatterMoveRequest, MatterRead
from app.schemas.public_api import (
    PublicApiCampaignCreateRequest,
    PublicApiCampaignCreateResponse,
    PublicApiConversationNoteCreate,
    PublicApiLeadCreate,
    PublicApiLeadCreateResponse,
    PublicApiRequestLogRead,
    PublicApiTemplateSendRequest,
    PublicApiTemplateSendResponse,
    WorkspacePublicApiTokenCreate,
    WorkspacePublicApiTokenCreateResponse,
    WorkspacePublicApiTokenRead,
)
from app.schemas.whatsapp import ConversationRead, WhatsAppContactRead
from app.schemas.whatsapp_campaign import CampaignLaunchRequest
from app.schemas.whatsapp_collaboration import ConversationInternalNoteRead
from app.services.matter_service import MatterService
from app.services.whatsapp.campaign_service import WhatsAppCampaignService
from app.services.whatsapp.collaboration_service import WhatsAppCollaborationService
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.contact_capacity_service import WhatsAppContactCapacityService
from app.services.whatsapp.helpers import normalize_phone_number, phone_number_lookup_variants
from app.services.whatsapp.message_service import WhatsAppMessageService
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp.strategy_factory import WhatsAppStrategyFactory

PUBLIC_API_SCOPE_PERMISSIONS: dict[str, frozenset[tuple[str, str]]] = {
    "contacts:read": frozenset({("mensagens", "view")}),
    "conversations:read": frozenset({("mensagens", "view")}),
    "leads:write": frozenset({("kanban", "create"), ("lead_data", "edit")}),
    "matters:write": frozenset({("kanban", "edit")}),
    "notes:write": frozenset({("mensagens", "send_message")}),
    "templates:send": frozenset({("mensagens", "send_message")}),
    "campaigns:write": frozenset({("mensagens", "send_message")}),
}


@dataclass
class PublicApiAuthResult:
    token: WorkspacePublicApiToken
    scope: str


@dataclass(frozen=True)
class PublicApiMutationExecution:
    response_body: dict[str, Any]
    status_code: int
    replayed: bool = False


@dataclass
class _PreparedMetaTemplateDelivery:
    connection: Any
    template: Any
    phone: str
    pending_message: WhatsAppMessage | None


async def _find_contact_by_phone_ordered(db, workspace_id, connection_id, phone: str):
    """Uma pessoa nas formas canônica, alias da Meta e E.164 (`+`), preferindo a
    linha canônica (S4); evita reescrever o telefone por cima de uma irmã."""
    variants = sorted(phone_number_lookup_variants(phone))
    if not variants:
        return None
    return await db.scalar(
        select(WhatsAppContact)
        .where(
            WhatsAppContact.workspace_id == workspace_id,
            WhatsAppContact.whatsapp_connection_id == connection_id,
            WhatsAppContact.phone.in_(variants),
        )
        .order_by(
            case(
                (WhatsAppContact.phone == phone, 0),
                (WhatsAppContact.phone == f"+{phone}", 1),
                else_=2,
            ),
            WhatsAppContact.id,
        )
        .limit(1)
    )


class PublicApiService:
    async def _find_contact_by_phone(self, workspace_id, connection_id, phone: str):
        return await _find_contact_by_phone_ordered(self.db, workspace_id, connection_id, phone)

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.cipher = WhatsAppSecretCipher()
        self.meta_client = MetaCloudClient()
        self.connection_service = WhatsAppConnectionService(
            db,
            cipher=self.cipher,
            meta_client=self.meta_client,
            qr_client=QrGatewayClient(),
        )
        self.message_service = WhatsAppMessageService(
            db,
            connection_service=self.connection_service,
            strategy_factory=WhatsAppStrategyFactory(
                meta_client=self.meta_client,
                qr_client=QrGatewayClient(),
                access_token_resolver=self.connection_service.get_meta_access_token,
                instance_token_resolver=self.connection_service.get_instance_token,
            ),
        )
        self.collaboration_service = WhatsAppCollaborationService(db)
        self.matter_service = MatterService(db)
        self.campaign_service = WhatsAppCampaignService(
            db,
            connection_service=self.connection_service,
            meta_client=self.meta_client,
        )

    async def list_tokens(self, workspace_id: UUID) -> list[WorkspacePublicApiTokenRead]:
        result = await self.db.execute(
            select(WorkspacePublicApiToken)
            .where(WorkspacePublicApiToken.workspace_id == workspace_id)
            .order_by(WorkspacePublicApiToken.created_at.desc())
        )
        return [WorkspacePublicApiTokenRead.model_validate(item) for item in result.scalars().all()]

    async def list_logs(self, workspace_id: UUID, *, limit: int = 30) -> list[PublicApiRequestLogRead]:
        result = await self.db.execute(
            select(PublicApiRequestLog)
            .where(PublicApiRequestLog.workspace_id == workspace_id)
            .order_by(PublicApiRequestLog.requested_at.desc())
            .limit(limit)
        )
        return [PublicApiRequestLogRead.model_validate(item) for item in result.scalars().all()]

    async def create_token(
        self,
        workspace_id: UUID,
        acting_user_id: UUID,
        payload: WorkspacePublicApiTokenCreate,
    ) -> WorkspacePublicApiTokenCreateResponse:
        scopes = list(dict.fromkeys(payload.scopes))
        await self._assert_creator_permissions(
            workspace_id=workspace_id,
            creator_id=acting_user_id,
            scopes=scopes,
        )
        plain_token = self._generate_plain_token(workspace_id)
        token = WorkspacePublicApiToken(
            workspace_id=workspace_id,
            created_by_id=acting_user_id,
            label=payload.label.strip(),
            token_prefix=plain_token[:18],
            token_hash=self._hash_token(plain_token),
            scopes=scopes,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            expires_at=payload.expires_at,
            is_active=True,
        )
        self.db.add(token)
        await self.db.commit()
        await self.db.refresh(token)
        return WorkspacePublicApiTokenCreateResponse(
            token=WorkspacePublicApiTokenRead.model_validate(token),
            plainTextToken=plain_token,
        )

    async def revoke_token(self, workspace_id: UUID, token_id: UUID) -> None:
        token = await self._get_token_for_workspace(workspace_id, token_id)
        token.is_active = False
        token.revoked_at = self._utc_now()
        await self.db.commit()

    async def authenticate(
        self,
        *,
        raw_token: str,
        required_scope: str,
    ) -> PublicApiAuthResult:
        token_hash = self._hash_token(raw_token)
        result = await self.db.execute(
            select(WorkspacePublicApiToken).where(WorkspacePublicApiToken.token_hash == token_hash)
        )
        token = result.scalars().first()
        if token is None or not token.is_active or token.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "PUBAPI-401", "message": "Token da API externa inválido ou revogado."},
            )
        if token.expires_at and token.expires_at <= self._utc_now():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "PUBAPI-402", "message": "Token da API externa expirado."},
            )
        # The token table is the narrow authentication bootstrap. Bind its
        # tenant before any RLS-protected domain query runs on this session.
        await set_workspace_context(self.db, token.workspace_id)
        if required_scope not in (token.scopes or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PUBAPI-403", "message": "Escopo insuficiente para esta operação."},
            )
        try:
            await self._assert_creator_permissions(
                workspace_id=token.workspace_id,
                creator_id=token.created_by_id,
                scopes=[required_scope],
            )
        except HTTPException:
            token.is_active = False
            token.revoked_at = self._utc_now()
            await self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "PUBAPI-404",
                    "message": "Token revogado porque seu responsável não possui mais o acesso necessário.",
                },
            ) from None
        await self._enforce_rate_limit(token, required_scope=required_scope)
        token.last_used_at = self._utc_now()
        await self.db.flush()
        return PublicApiAuthResult(token=token, scope=required_scope)

    async def _assert_creator_permissions(
        self,
        *,
        workspace_id: UUID,
        creator_id: UUID | None,
        scopes: list[str],
    ) -> None:
        if creator_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PUBAPI-405", "message": "Token sem responsável ativo."},
            )
        user = await self.db.scalar(
            select(User).where(
                User.id == creator_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PUBAPI-405", "message": "Responsável pelo token está inativo."},
            )
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == creator_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PUBAPI-405", "message": "Responsável pelo token não possui vínculo ativo."},
            )
        permissions = {
            (permission.module, permission.action)
            for permission in get_effective_permissions(
                membership.role,
                membership.permission_grants,
                membership.permission_denials,
            )
        }
        if ("integrations", "manage") not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PUBAPI-406", "message": "Gerenciamento de integrações não está autorizado."},
            )
        if set(scopes) & SENSITIVE_PUBLIC_API_SCOPES and ("lead_data", "view") not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PUBAPI-407",
                    "message": "Escopos sensíveis exigem acesso aos dados dos leads.",
                },
            )
        missing_permissions = {
            permission
            for scope in scopes
            for permission in PUBLIC_API_SCOPE_PERMISSIONS.get(scope, frozenset())
            if permission not in permissions
        }
        if missing_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PUBAPI-408",
                    "message": "O responsável pelo token não possui as permissões operacionais exigidas pelos escopos.",
                },
            )

    async def audit_request(
        self,
        *,
        token: WorkspacePublicApiToken,
        scope: str,
        path: str,
        method: str,
        status_code: int,
        ip_address: str | None,
        user_agent: str | None,
        metadata: Optional[dict] = None,
    ) -> None:
        self._add_audit_request(
            token=token,
            scope=scope,
            path=path,
            method=method,
            status_code=status_code,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata,
        )
        await self.db.commit()

    def _add_audit_request(
        self,
        *,
        token: WorkspacePublicApiToken,
        scope: str,
        path: str,
        method: str,
        status_code: int,
        ip_address: str | None,
        user_agent: str | None,
        metadata: Optional[dict] = None,
    ) -> None:
        self.db.add(
            PublicApiRequestLog(
                workspace_id=token.workspace_id,
                token_id=token.id,
                path=path,
                method=method.upper(),
                scope=scope,
                status_code=status_code,
                ip_address=ip_address,
                user_agent=user_agent,
                request_metadata=metadata or {},
            )
        )

    async def execute_mutation(
        self,
        *,
        token: WorkspacePublicApiToken,
        scope: str,
        operation: str,
        idempotency_key: str,
        payload: Any,
        resource: dict[str, Any] | None,
        response_status: int,
        path: str,
        method: str,
        ip_address: str | None,
        user_agent: str | None,
        handler: Callable[[], Awaitable[Any]],
        audit_metadata: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> PublicApiMutationExecution:
        receipt, replayed = await self._claim_mutation_receipt(
            token=token,
            operation=operation,
            idempotency_key=idempotency_key,
            payload=payload,
            resource=resource,
        )
        if replayed:
            return await self._return_completed_replay(
                receipt=receipt,
                token=token,
                scope=scope,
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                audit_metadata=audit_metadata,
            )

        try:
            response = await handler()
            return await self._complete_mutation(
                receipt=receipt,
                response=response,
                response_status=response_status,
                token=token,
                scope=scope,
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                audit_metadata=audit_metadata,
            )
        except Exception:
            await self.db.rollback()
            raise

    async def execute_send_template_mutation(
        self,
        *,
        token: WorkspacePublicApiToken,
        scope: str,
        idempotency_key: str,
        payload: PublicApiTemplateSendRequest,
        path: str,
        method: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> PublicApiMutationExecution:
        def audit_metadata(body: dict[str, Any]) -> dict[str, Any]:
            return {
                "phone": body.get("phone"),
                "wamid": body.get("wamid"),
            }
        receipt, replayed = await self._claim_mutation_receipt(
            token=token,
            operation="templates.send",
            idempotency_key=idempotency_key,
            payload=payload,
            resource=None,
        )
        if replayed:
            return await self._return_completed_replay(
                receipt=receipt,
                token=token,
                scope=scope,
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                audit_metadata=audit_metadata,
            )

        try:
            prepared = await self._prepare_meta_template_delivery(token.workspace_id, payload)
            if not external_delivery_allowed():
                return await self._complete_mutation(
                    receipt=receipt,
                    response=PublicApiTemplateSendResponse(
                        wamid=None,
                        phone=prepared.phone,
                        template=None,
                    ),
                    response_status=status.HTTP_200_OK,
                    token=token,
                    scope=scope,
                    path=path,
                    method=method,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    audit_metadata=audit_metadata,
                )

            receipt.state = PublicApiMutationState.outbox
            receipt.outbox_payload = {
                "kind": "meta_template",
                "connectionId": str(prepared.connection.id),
                "templateId": str(payload.meta_template_id),
                "contactId": str(payload.contact_id) if payload.contact_id else None,
                "phone": prepared.phone,
                "headerParameters": payload.header_parameters,
                "bodyParameters": payload.body_parameters,
                "pendingMessageId": (
                    str(prepared.pending_message.id) if prepared.pending_message else None
                ),
            }
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        try:
            response = await self._dispatch_meta_template_delivery(prepared, payload)
            return await self._complete_mutation(
                receipt=receipt,
                response=response,
                response_status=status.HTTP_200_OK,
                token=token,
                scope=scope,
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                audit_metadata=audit_metadata,
            )
        except Exception as exc:
            await self._mark_mutation_uncertain(receipt.id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PUBAPI-IDEMPOTENCY-UNCERTAIN",
                    "message": (
                        "O provedor externo pode ter recebido a solicitação; "
                        "a operação exige reconciliação antes de novo envio."
                    ),
                },
            ) from exc

    async def execute_create_campaign_mutation(
        self,
        *,
        token: WorkspacePublicApiToken,
        scope: str,
        idempotency_key: str,
        payload: PublicApiCampaignCreateRequest,
        path: str,
        method: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> PublicApiMutationExecution:
        def audit_metadata(body: dict[str, Any]) -> dict[str, Any]:
            return {
                "campaignId": (body.get("campaign") or {}).get("id"),
                "launchNow": payload.launch_now,
            }
        receipt, replayed = await self._claim_mutation_receipt(
            token=token,
            operation="campaigns.create",
            idempotency_key=idempotency_key,
            payload=payload,
            resource=None,
        )
        if replayed:
            return await self._return_completed_replay(
                receipt=receipt,
                token=token,
                scope=scope,
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                audit_metadata=audit_metadata,
            )

        try:
            if token.created_by_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "PUBAPI-412",
                        "message": (
                            "O token da API precisa ter um criador associado para criar campanhas."
                        ),
                    },
                )
            created = await self._create_campaign_without_commit(
                token.workspace_id,
                acting_user_id=token.created_by_id,
                payload=payload,
            )
            if not payload.launch_now or not external_delivery_allowed():
                return await self._complete_mutation(
                    receipt=receipt,
                    response=created,
                    response_status=status.HTTP_201_CREATED,
                    token=token,
                    scope=scope,
                    path=path,
                    method=method,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    audit_metadata=audit_metadata,
                )

            receipt.state = PublicApiMutationState.outbox
            receipt.outbox_payload = {
                "kind": "campaign_launch",
                "campaignId": str(created.campaign.id),
            }
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        try:
            campaign = await self.campaign_service.launch_campaign(
                token.workspace_id,
                created.campaign.id,
                CampaignLaunchRequest(dry_run=False, sample_size=8),
            )
            return await self._complete_mutation(
                receipt=receipt,
                response=PublicApiCampaignCreateResponse(campaign=campaign),
                response_status=status.HTTP_201_CREATED,
                token=token,
                scope=scope,
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                audit_metadata=audit_metadata,
            )
        except Exception as exc:
            await self._mark_mutation_uncertain(receipt.id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PUBAPI-IDEMPOTENCY-UNCERTAIN",
                    "message": (
                        "O lançamento externo pode ter iniciado; "
                        "a campanha exige reconciliação antes de nova tentativa."
                    ),
                },
            ) from exc

    async def _claim_mutation_receipt(
        self,
        *,
        token: WorkspacePublicApiToken,
        operation: str,
        idempotency_key: str,
        payload: Any,
        resource: dict[str, Any] | None,
    ) -> tuple[PublicApiMutationReceipt, bool]:
        request_hash = self._mutation_payload_hash(payload, resource=resource)
        query = select(PublicApiMutationReceipt).where(
            PublicApiMutationReceipt.workspace_id == token.workspace_id,
            PublicApiMutationReceipt.token_id == token.id,
            PublicApiMutationReceipt.operation == operation,
            PublicApiMutationReceipt.idempotency_key == idempotency_key,
        )
        existing = await self.db.scalar(query.with_for_update())
        if existing is not None:
            return self._validate_existing_receipt(existing, request_hash)

        receipt = PublicApiMutationReceipt(
            workspace_id=token.workspace_id,
            token_id=token.id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state=PublicApiMutationState.pending,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(receipt)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(query.with_for_update())
            if existing is None:
                raise
            return self._validate_existing_receipt(existing, request_hash)
        return receipt, False

    def _validate_existing_receipt(
        self,
        receipt: PublicApiMutationReceipt,
        request_hash: str,
    ) -> tuple[PublicApiMutationReceipt, bool]:
        if not hmac.compare_digest(receipt.request_hash, request_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PUBAPI-IDEMPOTENCY-CONFLICT",
                    "message": (
                        "Idempotency-Key já foi usada nesta operação com payload diferente."
                    ),
                },
            )
        if (
            receipt.state == PublicApiMutationState.completed
            and receipt.response_status is not None
            and receipt.response_body is not None
        ):
            return receipt, True
        if receipt.state in {
            PublicApiMutationState.outbox,
            PublicApiMutationState.uncertain,
        }:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PUBAPI-IDEMPOTENCY-UNCERTAIN",
                    "message": (
                        "A tentativa anterior possui efeito externo pendente ou incerto; "
                        "não haverá reenvio automático."
                    ),
                },
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PUBAPI-IDEMPOTENCY-IN-PROGRESS",
                "message": "Já existe uma tentativa em processamento para esta chave.",
            },
        )

    async def _return_completed_replay(
        self,
        *,
        receipt: PublicApiMutationReceipt,
        token: WorkspacePublicApiToken,
        scope: str,
        path: str,
        method: str,
        ip_address: str | None,
        user_agent: str | None,
        audit_metadata: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> PublicApiMutationExecution:
        body = dict(receipt.response_body or {})
        metadata = audit_metadata(body)
        metadata["idempotentReplay"] = True
        self._add_audit_request(
            token=token,
            scope=scope,
            path=path,
            method=method,
            status_code=receipt.response_status or status.HTTP_200_OK,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata,
        )
        await self.db.commit()
        return PublicApiMutationExecution(
            response_body=body,
            status_code=receipt.response_status or status.HTTP_200_OK,
            replayed=True,
        )

    async def _complete_mutation(
        self,
        *,
        receipt: PublicApiMutationReceipt,
        response: Any,
        response_status: int,
        token: WorkspacePublicApiToken,
        scope: str,
        path: str,
        method: str,
        ip_address: str | None,
        user_agent: str | None,
        audit_metadata: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> PublicApiMutationExecution:
        body = jsonable_encoder(response, by_alias=True)
        if not isinstance(body, dict):
            raise TypeError("Public API mutation responses must serialize to an object")
        receipt.state = PublicApiMutationState.completed
        receipt.response_status = response_status
        receipt.response_body = body
        receipt.completed_at = self._utc_now()
        receipt.error_code = None
        self._add_audit_request(
            token=token,
            scope=scope,
            path=path,
            method=method,
            status_code=response_status,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=audit_metadata(body),
        )
        await self.db.commit()
        return PublicApiMutationExecution(
            response_body=body,
            status_code=response_status,
        )

    async def _mark_mutation_uncertain(self, receipt_id: UUID, exc: Exception) -> None:
        await self.db.rollback()
        receipt = await self.db.scalar(
            select(PublicApiMutationReceipt)
            .where(PublicApiMutationReceipt.id == receipt_id)
            .with_for_update()
        )
        if receipt is not None and receipt.state != PublicApiMutationState.completed:
            receipt.state = PublicApiMutationState.uncertain
            receipt.uncertain_at = self._utc_now()
            receipt.error_code = safe_error_type(exc)
            await self.db.commit()

    def _mutation_payload_hash(
        self,
        payload: Any,
        *,
        resource: dict[str, Any] | None,
    ) -> str:
        canonical = json.dumps(
            {
                "payload": jsonable_encoder(payload, by_alias=False),
                "resource": resource or {},
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    async def list_contacts(self, workspace_id: UUID, *, search: str | None = None, limit: int = 50) -> list[WhatsAppContactRead]:
        return await self.message_service.list_contacts(workspace_id, search=search, limit=limit)

    async def get_conversation(self, workspace_id: UUID, contact_id: UUID) -> ConversationRead:
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def create_lead(
        self,
        workspace_id: UUID,
        payload: PublicApiLeadCreate,
        *,
        commit: bool = True,
    ) -> PublicApiLeadCreateResponse:
        client = Client(
            workspace_id=workspace_id,
            name=payload.name.strip(),
            email=payload.email,
            phone=normalize_phone_number(payload.phone) or payload.phone,
            cpf=payload.cpf,
        )
        self.db.add(client)
        await self.db.flush()

        try:
            practice_area = PracticeArea(payload.practice_area)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "PUBAPI-422", "message": "Área de atuação inválida para criação do lead."},
            ) from exc

        matter = await self.matter_service.create(
            MatterCreate(
                client_id=client.id,
                practice_area=practice_area,
                demand_type=payload.demand_type,
                lead_source=payload.lead_source or "api_publica",
                executive_summary=payload.executive_summary,
                responsible_human_id=payload.responsible_human_id,
                next_action=payload.next_action,
                claim_value=payload.claim_value,
            ),
            workspace_id=workspace_id,
            created_by=None,
        )

        contact_read: WhatsAppContactRead | None = None
        phone = normalize_phone_number(payload.phone)
        if phone:
            connection = await self.connection_service.get_default_connection(workspace_id, connected_only=True)
            if connection is not None:
                contact = await self._find_contact_by_phone(workspace_id, connection.id, phone)
                if contact is None:
                    await WhatsAppContactCapacityService(self.db).enforce_new_contact(
                        workspace_id=workspace_id
                    )
                    contact = await self._find_contact_by_phone(workspace_id, connection.id, phone)
                if contact is None:
                    try:
                        # S3: retry do cliente da API pública chega junto; a colisão
                        # no índice único fica no SAVEPOINT e a transação segue.
                        async with self.db.begin_nested():
                            contact = WhatsAppContact(
                                workspace_id=workspace_id,
                                whatsapp_connection_id=connection.id,
                                phone=phone,
                                name=payload.name.strip(),
                                matter_id=matter.id,
                                queue_status="ai",
                            )
                            self.db.add(contact)
                            await self.db.flush()
                    except IntegrityError:
                        contact = await self._find_contact_by_phone(
                            workspace_id, connection.id, phone
                        )
                        if contact is None:
                            raise
                elif contact.phone != phone:
                    # A busca prefere a linha canônica: sem irmã canônica, reescrever é seguro.
                    contact.phone = phone
                if contact is not None:
                    if not contact.name:
                        contact.name = payload.name.strip()
                    if contact.matter_id is None:
                        contact.matter_id = matter.id
                contact_read = (await self.message_service.list_contacts(workspace_id, search=phone, limit=5))[0]
        if commit:
            await self.db.commit()

        return PublicApiLeadCreateResponse(
            matter=MatterRead.model_validate(matter),
            contact=contact_read,
        )

    async def move_stage(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        *,
        column_id: str,
        micro_status: str | None,
        commit: bool = True,
    ) -> MatterRead:
        moved = await self.matter_service.move_card(
            matter_id,
            MatterMoveRequest(column_id=KanbanColumnId(column_id), micro_status=micro_status),
            moved_by=None,
            workspace_id=workspace_id,
        )
        if moved is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PUBAPI-404", "message": "Card não encontrado para mover de etapa."},
            )
        await self.db.flush()
        refreshed = await self.matter_service.get_by_id(matter_id, workspace_id)
        if commit:
            await self.db.commit()
        return MatterRead.model_validate(refreshed or moved)

    async def add_note(
        self,
        workspace_id: UUID,
        contact_id: UUID,
        *,
        author_user_id: UUID | None,
        payload: PublicApiConversationNoteCreate,
        commit: bool = True,
    ) -> ConversationInternalNoteRead:
        acting_user_id = author_user_id
        if acting_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PUBAPI-409", "message": "O token da API precisa ter um criador associado para registrar notas."},
            )
        contact = await self.collaboration_service._get_contact(contact_id, workspace_id)
        note = WhatsAppConversationNote(
            workspace_id=workspace_id,
            contact_id=contact.id,
            matter_id=contact.matter_id,
            author_user_id=acting_user_id,
            body=payload.body.strip(),
            mentions=[],
            note_metadata={"source": "public_api"},
        )
        self.db.add(note)
        await self.db.flush()
        if contact.matter_id:
            await self.matter_service.add_note(
                contact.matter_id,
                f"[Nota interna da API pública] {payload.body.strip()}",
                agent_id=acting_user_id,
                workspace_id=workspace_id,
            )
        await self.db.refresh(note)
        author = await self.db.get(User, acting_user_id)
        response = ConversationInternalNoteRead(
            id=note.id,
            contactId=note.contact_id,
            matterId=note.matter_id,
            authorUserId=note.author_user_id,
            authorName=author.name if author else None,
            body=note.body,
            mentions=[],
            noteMetadata=note.note_metadata,
            createdAt=note.created_at,
        )
        if commit:
            await self.db.commit()
        return response

    async def send_meta_template(
        self,
        workspace_id: UUID,
        payload: PublicApiTemplateSendRequest,
    ) -> PublicApiTemplateSendResponse:
        prepared = await self._prepare_meta_template_delivery(workspace_id, payload)
        await self.db.commit()

        if not external_delivery_allowed():
            return PublicApiTemplateSendResponse(
                wamid=None,
                phone=prepared.phone,
                template=None,
            )

        response = await self._dispatch_meta_template_delivery(prepared, payload)
        await self.db.commit()
        return response

    async def _prepare_meta_template_delivery(
        self,
        workspace_id: UUID,
        payload: PublicApiTemplateSendRequest,
    ) -> _PreparedMetaTemplateDelivery:
        template = await self.campaign_service._get_template(workspace_id, payload.meta_template_id)
        if template.status != MetaTemplateStatus.approved:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PUBAPI-410", "message": "O template oficial ainda não está aprovado pela Meta."},
            )
        connection = await self.campaign_service._resolve_official_connection(workspace_id, payload.whatsapp_connection_id)
        phone = normalize_phone_number(payload.phone)
        contact = None
        if payload.contact_id:
            conversation = await self.message_service.get_conversation(payload.contact_id, workspace_id)
            contact = conversation.contact
            phone = normalize_phone_number(contact.phone)
        if not phone:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "PUBAPI-411", "message": "Informe um telefone ou uma conversa válida para disparar o template."},
            )

        pending_message = None
        if payload.contact_id:
            pending_message = WhatsAppMessage(
                contact_id=payload.contact_id,
                whatsapp_connection_id=connection.id,
                direction=WaMessageDirection.outbound,
                status=WaMessageStatus.pending,
                body=f"[Template oficial] {template.name}",
                sent_by_id=None,
            )
            self.db.add(pending_message)
            await self.db.flush()

        return _PreparedMetaTemplateDelivery(
            connection=connection,
            template=template,
            phone=phone,
            pending_message=pending_message,
        )

    async def _dispatch_meta_template_delivery(
        self,
        prepared: _PreparedMetaTemplateDelivery,
        payload: PublicApiTemplateSendRequest,
    ) -> PublicApiTemplateSendResponse:
        response = await self.meta_client.send_template_message(
            phone_number_id=prepared.connection.phone_number_id or "",
            access_token=await self.connection_service.resolve_meta_access_token(prepared.connection),
            to=prepared.phone,
            template_name=prepared.template.name,
            language_code=prepared.template.language,
            header_parameters=payload.header_parameters,
            body_parameters=payload.body_parameters,
        )
        wamid = response.get("messages", [{}])[0].get("id")

        if prepared.pending_message is not None:
            prepared.pending_message.wamid = wamid
            prepared.pending_message.status = WaMessageStatus.sent
            await self.db.flush()

        return PublicApiTemplateSendResponse(
            wamid=wamid,
            phone=prepared.phone,
            template=None,
        )

    async def create_campaign(
        self,
        workspace_id: UUID,
        *,
        acting_user_id: UUID | None,
        payload: PublicApiCampaignCreateRequest,
    ) -> PublicApiCampaignCreateResponse:
        if acting_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PUBAPI-412", "message": "O token da API precisa ter um criador associado para criar campanhas."},
            )
        response = await self._create_campaign_without_commit(
            workspace_id,
            acting_user_id=acting_user_id,
            payload=payload,
        )
        await self.db.commit()
        if payload.launch_now and external_delivery_allowed():
            campaign = await self.campaign_service.launch_campaign(
                workspace_id,
                response.campaign.id,
                CampaignLaunchRequest(dry_run=False, sample_size=8),
            )
            return PublicApiCampaignCreateResponse(campaign=campaign)
        return response

    async def _create_campaign_without_commit(
        self,
        workspace_id: UUID,
        *,
        acting_user_id: UUID,
        payload: PublicApiCampaignCreateRequest,
    ) -> PublicApiCampaignCreateResponse:
        connection = await self.campaign_service._resolve_official_connection(
            workspace_id,
            payload.whatsapp_connection_id,
        )
        template = await self.campaign_service._get_template(
            workspace_id,
            payload.meta_template_id,
        )
        self.campaign_service._validate_template_connection(template, connection.id)
        self.campaign_service._validate_template_parameters(
            template,
            header_parameters=payload.header_parameters,
            body_parameters=payload.body_parameters,
            button_parameters=payload.button_parameters,
        )
        scheduled_at = self.campaign_service._normalize_datetime(payload.scheduled_at)
        campaign = WhatsAppCampaign(
            workspace_id=workspace_id,
            whatsapp_connection_id=connection.id,
            created_by_id=acting_user_id,
            title=payload.title,
            description=payload.description,
            scheduled_at=scheduled_at,
            status=(
                CampaignStatus.scheduled
                if self.campaign_service._is_future(scheduled_at)
                else CampaignStatus.draft
            ),
        )
        audience_preview = await self.campaign_service._build_audience_preview(
            workspace_id,
            payload.audience_filter,
        )
        campaign.total_recipients = audience_preview["total"]
        campaign.eligible_recipients = audience_preview["eligible"]
        campaign.blocked_recipients = audience_preview["blocked"]
        campaign.audience = WhatsAppCampaignAudience(
            filters=payload.audience_filter.model_dump(mode="json", exclude_none=True),
            estimated_recipients=audience_preview["eligible"],
            sample_recipients=audience_preview["sample"],
        )
        campaign.template_snapshot = self.campaign_service._build_template_snapshot(
            template,
            header_parameters=payload.header_parameters,
            body_parameters=payload.body_parameters,
            button_parameters=payload.button_parameters,
        )
        self.db.add(campaign)
        await self.db.flush()
        campaign_read = await self.campaign_service.get_campaign(workspace_id, campaign.id)
        return PublicApiCampaignCreateResponse(campaign=campaign_read)

    async def _enforce_rate_limit(
        self,
        token: WorkspacePublicApiToken,
        *,
        required_scope: str,
    ) -> None:
        bucket = self._utc_now().strftime("%Y%m%d%H%M")
        key = f"public-api:{token.id}:{bucket}"
        try:
            redis = await get_redis()
            current = await redis.incr(key)
            if current == 1:
                await redis.expire(key, 75)
            if int(current) > int(token.rate_limit_per_minute):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"code": "PUBAPI-429", "message": "Rate limit da API externa excedido para este token."},
                )
        except HTTPException:
            raise
        except Exception as exc:
            if required_scope in MUTABLE_PUBLIC_API_SCOPES:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "PUBAPI-RATE-LIMITER-UNAVAILABLE",
                        "message": (
                            "Rate limiter indisponível; mutações da API externa estão "
                            "temporariamente bloqueadas."
                        ),
                    },
                ) from exc
            return

    async def _get_token_for_workspace(self, workspace_id: UUID, token_id: UUID) -> WorkspacePublicApiToken:
        result = await self.db.execute(
            select(WorkspacePublicApiToken).where(
                WorkspacePublicApiToken.id == token_id,
                WorkspacePublicApiToken.workspace_id == workspace_id,
            )
        )
        token = result.scalars().first()
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PUBAPI-404", "message": "Token da API externa não encontrado."},
            )
        return token

    def _generate_plain_token(self, workspace_id: UUID) -> str:
        suffix = secrets.token_urlsafe(32)
        return f"klm_{str(workspace_id)[:8]}_{suffix}"

    def _hash_token(self, plain_token: str) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            plain_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)
