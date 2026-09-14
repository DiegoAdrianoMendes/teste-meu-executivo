"""Signed public ingress and encrypted credential lifecycle for native channels."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, desc, func, literal, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.db.tenant import set_workspace_context
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    LeadHydrationJob,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
    OmnichannelPublicIngressReceipt,
    OmnichannelWebhookCredential,
)
from app.models.workspace import Workspace, WorkspaceStatus
from app.schemas.omnichannel_v6 import WebhookCredentialConfigure
from app.services.integration_secrets import IntegrationSecretCipher
from app.services.klm_operations_service import KlmOperationsService
from app.services.omnichannel_orchestration_service import OmnichannelOrchestrationService

LEAD_INGRESS_PROVIDERS = frozenset({"meta_ads", "tiktok_ads", "site", "lovable"})
SOCIAL_INGRESS_PROVIDERS = frozenset(
    {"meta_instagram", "meta_messenger", "tiktok_business"}
)
PUBLIC_INGRESS_PROVIDERS = LEAD_INGRESS_PROVIDERS | SOCIAL_INGRESS_PROVIDERS
META_INGRESS_PROVIDERS = frozenset(
    {"meta_ads", "meta_instagram", "meta_messenger"}
)
TIKTOK_INGRESS_PROVIDERS = frozenset({"tiktok_ads", "tiktok_business"})


class WebhookSignatureError(ValueError):
    """The request is not authentic or is outside the accepted replay window."""


class WebhookVersionConflictError(ValueError):
    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__("A credencial foi alterada por outro usuário.")


class PublicIngressConflictError(ValueError):
    """A provider event ID was replayed with divergent bytes."""


def _now() -> datetime:
    return datetime.now(UTC)


def _secret_value(value: Any) -> str:
    return value.get_secret_value() if value is not None else ""


def _signature_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str, *, tolerance_seconds: int) -> datetime:
    try:
        timestamp = int(value)
        occurred_at = datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, TypeError, ValueError) as error:
        raise WebhookSignatureError("Timestamp de assinatura inválido.") from error
    if abs((_now() - occurred_at).total_seconds()) > tolerance_seconds:
        raise WebhookSignatureError("Assinatura fora da janela contra replay.")
    return occurred_at


def _parse_signature_parts(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in value.split(","):
        key, separator, part_value = item.strip().partition("=")
        if separator and key and part_value:
            parts[key] = part_value
    return parts


class OmnichannelWebhookService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.operations = KlmOperationsService(db)

    @staticmethod
    def _ensure_provider(connection: OmnichannelChannelConnection) -> None:
        if connection.provider not in PUBLIC_INGRESS_PROVIDERS:
            raise ValueError("Esta conexão não aceita captação por webhook público.")

    async def _workspace_slug(self, workspace_id: UUID) -> str:
        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
        )
        if workspace is None:
            raise LookupError("Workspace não encontrado.")
        return workspace.slug

    @staticmethod
    def _status_payload(
        *,
        connection: OmnichannelChannelConnection,
        workspace_slug: str,
        credential: OmnichannelWebhookCredential | None,
    ) -> dict[str, Any]:
        return {
            "connection_id": connection.id,
            "provider": connection.provider,
            "connection_key": connection.connection_key,
            "state": credential.state if credential else "not_configured",
            "algorithm": credential.algorithm if credential else "hmac_sha256",
            "signing_secret_configured": bool(
                credential and credential.signing_secret_encrypted
            ),
            "verify_token_configured": bool(
                credential and credential.verify_token_encrypted
            ),
            "timestamp_tolerance_seconds": (
                credential.timestamp_tolerance_seconds if credential else 300
            ),
            "version": credential.version if credential else 0,
            "webhook_path": (
                "/api/v1/webhooks/omnichannel/"
                + ("social/" if connection.provider in SOCIAL_INGRESS_PROVIDERS else "")
                + f"{workspace_slug}/{connection.provider}/{connection.connection_key}"
            ),
            "external_gate_open": external_delivery_allowed(),
            "last_verified_at": credential.last_verified_at if credential else None,
            "rotated_at": credential.rotated_at if credential else None,
            "updated_at": credential.updated_at if credential else connection.updated_at,
        }

    async def credential_status(
        self, workspace_id: UUID, connection_id: UUID
    ) -> dict[str, Any]:
        connection = await OmnichannelOrchestrationService(self.db).get_connection(
            workspace_id, connection_id
        )
        self._ensure_provider(connection)
        credential = await self.db.scalar(
            select(OmnichannelWebhookCredential).where(
                OmnichannelWebhookCredential.workspace_id == workspace_id,
                OmnichannelWebhookCredential.connection_id == connection.id,
            )
        )
        return self._status_payload(
            connection=connection,
            workspace_slug=await self._workspace_slug(workspace_id),
            credential=credential,
        )

    async def webhook_center(
        self,
        workspace_id: UUID,
        *,
        provider: str | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Build the native webhook control center without exposing provider secrets."""

        completed_states = {"completed", "processed", "sent", "delivered", "hydrated"}
        failed_states = {"failed", "dead_letter", "cancelled"}
        blocked_states = {"blocked_external"}
        awaiting_states = {
            "queued",
            "fetching",
            "processing",
            "hydrated_pending",
            "awaiting_hydration",
            "retry_wait",
            "retrying",
        }
        connection_query = select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.provider.in_(PUBLIC_INGRESS_PROVIDERS),
        )
        if provider:
            connection_query = connection_query.where(
                OmnichannelChannelConnection.provider == provider
            )
        connections = list(
            (
                await self.db.scalars(
                    connection_query.order_by(
                        OmnichannelChannelConnection.display_name,
                        OmnichannelChannelConnection.id,
                    )
                )
            ).all()
        )
        connection_ids = [connection.id for connection in connections]
        credential_rows: list[OmnichannelWebhookCredential] = []
        receipt_rows: list[OmnichannelPublicIngressReceipt] = []
        aggregate_by_connection: dict[UUID, dict[str, Any]] = {}
        receipt_total_count = 0
        receipt_match_count = 0
        if connection_ids:
            credential_rows = list(
                (
                    await self.db.scalars(
                        select(OmnichannelWebhookCredential).where(
                            OmnichannelWebhookCredential.workspace_id == workspace_id,
                            OmnichannelWebhookCredential.connection_id.in_(connection_ids),
                        )
                    )
                ).all()
            )

            hydration_state = (
                select(LeadHydrationJob.state)
                .where(
                    LeadHydrationJob.workspace_id == workspace_id,
                    LeadHydrationJob.public_ingress_receipt_id
                    == OmnichannelPublicIngressReceipt.id,
                )
                .order_by(desc(LeadHydrationJob.updated_at), desc(LeadHydrationJob.id))
                .limit(1)
                .correlate(OmnichannelPublicIngressReceipt)
                .scalar_subquery()
            )
            capture_state = (
                select(LeadCaptureReceipt.state)
                .where(
                    LeadCaptureReceipt.workspace_id == workspace_id,
                    LeadCaptureReceipt.connection_id
                    == OmnichannelPublicIngressReceipt.connection_id,
                    LeadCaptureReceipt.provider == OmnichannelPublicIngressReceipt.provider,
                    LeadCaptureReceipt.connection_key
                    == OmnichannelPublicIngressReceipt.connection_key,
                    LeadCaptureReceipt.external_lead_id
                    == OmnichannelPublicIngressReceipt.external_lead_id,
                )
                .order_by(desc(LeadCaptureReceipt.updated_at), desc(LeadCaptureReceipt.id))
                .limit(1)
                .correlate(OmnichannelPublicIngressReceipt)
                .scalar_subquery()
            )
            social_event_state = (
                select(literal("processed"))
                .where(
                    OmnichannelPublicIngressReceipt.provider.in_(
                        SOCIAL_INGRESS_PROVIDERS
                    ),
                    OmnichannelChannelEvent.workspace_id == workspace_id,
                    OmnichannelChannelEvent.connection_id
                    == OmnichannelPublicIngressReceipt.connection_id,
                    OmnichannelChannelEvent.provider
                    == OmnichannelPublicIngressReceipt.provider,
                    OmnichannelChannelEvent.connection_key
                    == OmnichannelPublicIngressReceipt.connection_key,
                    OmnichannelChannelEvent.external_event_id
                    == OmnichannelPublicIngressReceipt.external_event_id,
                )
                .limit(1)
                .correlate(OmnichannelPublicIngressReceipt)
                .scalar_subquery()
            )
            processing_state = func.coalesce(
                hydration_state,
                capture_state,
                social_event_state,
                literal("received"),
            )
            receipt_scope = (
                OmnichannelPublicIngressReceipt.workspace_id == workspace_id,
                OmnichannelPublicIngressReceipt.connection_id.in_(connection_ids),
            )

            aggregate_rows = (
                await self.db.execute(
                    select(
                        OmnichannelPublicIngressReceipt.connection_id.label("connection_id"),
                        func.count(OmnichannelPublicIngressReceipt.id).label("receipt_count"),
                        func.sum(
                            case((processing_state.in_(completed_states), 1), else_=0)
                        ).label("processed_count"),
                        func.sum(
                            case((processing_state.in_(awaiting_states), 1), else_=0)
                        ).label("awaiting_count"),
                        func.sum(
                            case((processing_state.in_(failed_states), 1), else_=0)
                        ).label("failed_count"),
                        func.sum(
                            case((processing_state.in_(blocked_states), 1), else_=0)
                        ).label("blocked_count"),
                        func.max(OmnichannelPublicIngressReceipt.received_at).label(
                            "latest_receipt_at"
                        ),
                    )
                    .where(*receipt_scope)
                    .group_by(OmnichannelPublicIngressReceipt.connection_id)
                )
            ).mappings().all()
            aggregate_by_connection = {
                row["connection_id"]: {
                    "receipt_count": int(row["receipt_count"] or 0),
                    "processed_count": int(row["processed_count"] or 0),
                    "awaiting_count": int(row["awaiting_count"] or 0),
                    "failed_count": int(row["failed_count"] or 0),
                    "blocked_count": int(row["blocked_count"] or 0),
                    "latest_receipt_at": row["latest_receipt_at"],
                }
                for row in aggregate_rows
            }
            receipt_total_count = sum(
                row["receipt_count"] for row in aggregate_by_connection.values()
            )
            if state:
                receipt_match_count = int(
                    await self.db.scalar(
                        select(func.count(OmnichannelPublicIngressReceipt.id)).where(
                            *receipt_scope,
                            processing_state == state,
                        )
                    )
                    or 0
                )
            else:
                receipt_match_count = receipt_total_count

            receipt_query = select(OmnichannelPublicIngressReceipt).where(*receipt_scope)
            if state:
                receipt_query = receipt_query.where(processing_state == state)
            receipt_rows = list(
                (
                    await self.db.scalars(
                        receipt_query.order_by(
                            desc(OmnichannelPublicIngressReceipt.received_at),
                            desc(OmnichannelPublicIngressReceipt.id),
                        ).limit(limit)
                    )
                ).all()
            )

        receipt_ids = [receipt.id for receipt in receipt_rows]
        hydration_rows: list[LeadHydrationJob] = []
        if receipt_ids:
            hydration_rows = list(
                (
                    await self.db.scalars(
                        select(LeadHydrationJob).where(
                            LeadHydrationJob.workspace_id == workspace_id,
                            LeadHydrationJob.public_ingress_receipt_id.in_(receipt_ids),
                        ).order_by(desc(LeadHydrationJob.updated_at), desc(LeadHydrationJob.id))
                    )
                ).all()
            )

        hydration_by_ingress: dict[UUID, LeadHydrationJob] = {}
        for row in hydration_rows:
            hydration_by_ingress.setdefault(row.public_ingress_receipt_id, row)
        capture_ids = {
            row.lead_capture_receipt_id
            for row in hydration_rows
            if row.lead_capture_receipt_id is not None
        }
        lead_keys = {
            (row.provider, row.connection_key, row.external_lead_id)
            for row in receipt_rows
            if row.external_lead_id
        }
        capture_rows: list[LeadCaptureReceipt] = []
        capture_matchers = []
        if capture_ids:
            capture_matchers.append(LeadCaptureReceipt.id.in_(capture_ids))
        if lead_keys:
            capture_matchers.append(
                tuple_(
                    LeadCaptureReceipt.provider,
                    LeadCaptureReceipt.connection_key,
                    LeadCaptureReceipt.external_lead_id,
                ).in_(lead_keys)
            )
        if connection_ids and capture_matchers:
            capture_rows = list(
                (
                    await self.db.scalars(
                        select(LeadCaptureReceipt).where(
                            LeadCaptureReceipt.workspace_id == workspace_id,
                            LeadCaptureReceipt.connection_id.in_(connection_ids),
                            or_(*capture_matchers),
                        ).order_by(desc(LeadCaptureReceipt.updated_at), desc(LeadCaptureReceipt.id))
                    )
                ).all()
            )
        capture_by_id = {row.id: row for row in capture_rows if row.id in capture_ids}
        capture_by_key: dict[tuple[str, str, str], LeadCaptureReceipt] = {}
        for row in capture_rows:
            key = (row.provider, row.connection_key, row.external_lead_id)
            if key in lead_keys:
                capture_by_key.setdefault(key, row)

        social_event_keys = {
            (
                row.connection_id,
                row.provider,
                row.connection_key,
                row.external_event_id,
            )
            for row in receipt_rows
            if row.provider in SOCIAL_INGRESS_PROVIDERS
        }
        social_event_rows: list[OmnichannelChannelEvent] = []
        if social_event_keys:
            social_event_rows = list(
                (
                    await self.db.scalars(
                        select(OmnichannelChannelEvent).where(
                            OmnichannelChannelEvent.workspace_id == workspace_id,
                            tuple_(
                                OmnichannelChannelEvent.connection_id,
                                OmnichannelChannelEvent.provider,
                                OmnichannelChannelEvent.connection_key,
                                OmnichannelChannelEvent.external_event_id,
                            ).in_(social_event_keys),
                        )
                    )
                ).all()
            )
        processed_social_keys = {
            (
                row.connection_id,
                row.provider,
                row.connection_key,
                row.external_event_id,
            )
            for row in social_event_rows
        }
        social_event_by_key = {
            (
                row.connection_id,
                row.provider,
                row.connection_key,
                row.external_event_id,
            ): row
            for row in social_event_rows
        }

        def receipt_payload(receipt: OmnichannelPublicIngressReceipt) -> dict[str, Any]:
            hydration = hydration_by_ingress.get(receipt.id)
            capture = (
                capture_by_id.get(hydration.lead_capture_receipt_id)
                if hydration and hydration.lead_capture_receipt_id
                else capture_by_key.get(
                    (receipt.provider, receipt.connection_key, receipt.external_lead_id)
                )
            )
            social_event = social_event_by_key.get(
                (
                    receipt.connection_id,
                    receipt.provider,
                    receipt.connection_key,
                    receipt.external_event_id,
                )
            )
            if hydration is not None:
                processing_state = hydration.state
            elif capture is not None:
                processing_state = capture.state
            elif (
                receipt.connection_id,
                receipt.provider,
                receipt.connection_key,
                receipt.external_event_id,
            ) in processed_social_keys:
                processing_state = "processed"
            else:
                processing_state = "received"
            error_code = (
                hydration.last_error_code if hydration is not None else None
            ) or (capture.error_code if capture is not None else None)
            return {
                "id": receipt.id,
                "connection_id": receipt.connection_id,
                "provider": receipt.provider,
                "connection_key": receipt.connection_key,
                "external_event_id": receipt.external_event_id,
                "external_lead_id": receipt.external_lead_id,
                "payload_sha256": receipt.payload_sha256,
                "signature_scheme": receipt.signature_scheme,
                "signature_timestamp": receipt.signature_timestamp,
                "received_at": receipt.received_at,
                "processing_state": processing_state,
                "lead_capture_receipt_id": capture.id if capture is not None else None,
                "lead_capture_state": capture.state if capture is not None else None,
                "hydration_job_id": hydration.id if hydration is not None else None,
                "hydration_state": hydration.state if hydration is not None else None,
                "thread_id": (
                    capture.thread_id
                    if capture is not None
                    else social_event.thread_id if social_event is not None else None
                ),
                "matter_id": capture.matter_id if capture is not None else None,
                "identity_id": (
                    capture.identity_id
                    if capture is not None
                    else social_event.identity_id if social_event is not None else None
                ),
                "error_code": error_code,
            }

        receipts = [receipt_payload(row) for row in receipt_rows]
        credential_by_connection = {
            credential.connection_id: credential for credential in credential_rows
        }
        workspace_slug = await self._workspace_slug(workspace_id)
        connection_payloads: list[dict[str, Any]] = []
        for connection in connections:
            credential = credential_by_connection.get(connection.id)
            aggregate = aggregate_by_connection.get(
                connection.id,
                {
                    "receipt_count": 0,
                    "processed_count": 0,
                    "awaiting_count": 0,
                    "failed_count": 0,
                    "blocked_count": 0,
                    "latest_receipt_at": None,
                },
            )
            credential_active = credential is not None and credential.state == "active"
            has_receipt = aggregate["receipt_count"] > 0
            has_processed = aggregate["processed_count"] > 0
            steps = [
                {
                    "key": "connection",
                    "label": "Conexão cadastrada",
                    "completed": True,
                    "detail": "Conta e rota pertencem a este workspace.",
                },
                {
                    "key": "credential",
                    "label": "Assinatura protegida",
                    "completed": credential_active,
                    "detail": (
                        "Segredo criptografado e ativo."
                        if credential_active
                        else "Configure o segredo e o token de verificação, quando exigido."
                    ),
                },
                {
                    "key": "endpoint",
                    "label": "Endpoint pronto",
                    "completed": credential_active
                    and connection.status not in {"paused", "disconnected_local"},
                    "detail": "A URL é exclusiva para workspace, provedor e conexão.",
                },
                {
                    "key": "first_event",
                    "label": "Primeiro evento recebido",
                    "completed": has_receipt,
                    "detail": "O recibo assinado e seu hash ficam preservados.",
                },
                {
                    "key": "processing",
                    "label": "Processamento comprovado",
                    "completed": has_processed,
                    "detail": (
                        "Conversa social e identidade vinculadas sem ação externa nesta fase."
                        if connection.provider in SOCIAL_INGRESS_PROVIDERS
                        else "Lead hidratado e vinculado sem ação externa nesta fase."
                    ),
                },
            ]
            completed_steps = sum(1 for step in steps if step["completed"])
            connection_payloads.append(
                {
                    "connection": connection,
                    "credential": (
                        self._status_payload(
                            connection=connection,
                            workspace_slug=workspace_slug,
                            credential=credential,
                        )
                        if credential is not None
                        else None
                    ),
                    "setup_progress": round(completed_steps / len(steps) * 100),
                    "setup_steps": steps,
                    "receipt_count": aggregate["receipt_count"],
                    "processed_count": aggregate["processed_count"],
                    "awaiting_hydration_count": aggregate["awaiting_count"],
                    "failed_count": aggregate["failed_count"],
                    "blocked_count": aggregate["blocked_count"],
                    "latest_receipt_at": aggregate["latest_receipt_at"],
                }
            )

        return {
            "connection_count": len(connections),
            "configured_connection_count": sum(
                connection.webhook_state == "configured_local"
                for connection in connections
            ),
            "active_credential_count": sum(
                credential.state == "active" for credential in credential_rows
            ),
            "receipt_window_count": len(receipts),
            "receipt_total_count": receipt_total_count,
            "receipt_match_count": receipt_match_count,
            "processed_count": sum(
                row["processed_count"] for row in aggregate_by_connection.values()
            ),
            "awaiting_hydration_count": sum(
                row["awaiting_count"] for row in aggregate_by_connection.values()
            ),
            "failed_count": sum(
                row["failed_count"] for row in aggregate_by_connection.values()
            ),
            "blocked_count": sum(
                row["blocked_count"] for row in aggregate_by_connection.values()
            ),
            "idempotency_enforced": True,
            "external_gate_open": external_delivery_allowed(),
            "connections": connection_payloads,
            "receipts": receipts,
            "generated_at": _now(),
        }

    async def configure_credential(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID,
        payload: WebhookCredentialConfigure,
    ) -> dict[str, Any]:
        connection = await OmnichannelOrchestrationService(self.db).get_connection(
            workspace_id, connection_id
        )
        self._ensure_provider(connection)
        credential = await self.db.scalar(
            select(OmnichannelWebhookCredential)
            .where(
                OmnichannelWebhookCredential.workspace_id == workspace_id,
                OmnichannelWebhookCredential.connection_id == connection.id,
            )
            .with_for_update()
        )
        current_version = credential.version if credential else 0
        if payload.expected_version != current_version:
            raise WebhookVersionConflictError(current_version)

        cipher = IntegrationSecretCipher()
        signing_secret = _secret_value(payload.signing_secret).strip()
        verify_token = _secret_value(payload.verify_token).strip()
        if signing_secret and len(signing_secret) < 32:
            raise ValueError("O segredo de assinatura deve ter ao menos 32 caracteres.")
        if verify_token and len(verify_token) < 16:
            raise ValueError("O token de verificação deve ter ao menos 16 caracteres.")
        if credential is None:
            if payload.action in {"disable", "enable"}:
                raise ValueError("Configure a credencial antes de alterar seu estado.")
            if not signing_secret:
                raise ValueError("Informe o segredo de assinatura da conexão.")
            if connection.provider in META_INGRESS_PROVIDERS and not verify_token:
                raise ValueError("Conexões Meta exigem também um token de verificação.")
            credential = OmnichannelWebhookCredential(
                workspace_id=workspace_id,
                connection_id=connection.id,
                signing_secret_encrypted=cipher.encrypt(signing_secret),
                verify_token_encrypted=(cipher.encrypt(verify_token) if verify_token else None),
                timestamp_tolerance_seconds=payload.timestamp_tolerance_seconds or 300,
                state="active",
                version=1,
            )
            self.db.add(credential)
            event_type = "omnichannel_webhook_credential_configured"
        else:
            if payload.action == "rotate":
                credential.signing_secret_encrypted = cipher.encrypt(signing_secret)
                if payload.verify_token is not None:
                    credential.verify_token_encrypted = (
                        cipher.encrypt(verify_token) if verify_token else None
                    )
                credential.rotated_at = _now()
                credential.state = "active"
                event_type = "omnichannel_webhook_credential_rotated"
            elif payload.action == "disable":
                credential.state = "disabled"
                event_type = "omnichannel_webhook_credential_disabled"
            elif payload.action == "enable":
                credential.state = "active"
                event_type = "omnichannel_webhook_credential_enabled"
            else:
                if payload.signing_secret is not None:
                    raise ValueError("Use a ação rotate para substituir o segredo.")
                if payload.verify_token is not None:
                    credential.verify_token_encrypted = (
                        cipher.encrypt(verify_token) if verify_token else None
                    )
                event_type = "omnichannel_webhook_credential_updated"
            if payload.timestamp_tolerance_seconds is not None:
                credential.timestamp_tolerance_seconds = payload.timestamp_tolerance_seconds
            credential.version += 1

        if connection.provider in META_INGRESS_PROVIDERS and not credential.verify_token_encrypted:
            raise ValueError("Conexões Meta exigem um token de verificação configurado.")
        connection.webhook_state = (
            "configured_local" if credential.state == "active" else "disabled"
        )
        connection.version += 1
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type=event_type,
            entity_type="omnichannel_webhook_credential",
            entity_id=credential.id,
            summary=f"Credencial assinada de {connection.display_name} atualizada localmente.",
            after_data={
                "connectionId": str(connection.id),
                "provider": connection.provider,
                "state": credential.state,
                "version": credential.version,
                "signingSecretConfigured": True,
                "verifyTokenConfigured": bool(credential.verify_token_encrypted),
            },
        )
        await self.db.refresh(credential)
        return self._status_payload(
            connection=connection,
            workspace_slug=await self._workspace_slug(workspace_id),
            credential=credential,
        )

    async def resolve_public_ingress(
        self,
        *,
        workspace_slug: str,
        provider: str,
        connection_key: str,
    ) -> tuple[Workspace, OmnichannelChannelConnection, OmnichannelWebhookCredential]:
        if provider not in PUBLIC_INGRESS_PROVIDERS:
            raise LookupError("Rota de webhook não encontrada.")
        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.slug == workspace_slug,
                Workspace.status == WorkspaceStatus.active,
                Workspace.is_active.is_(True),
                Workspace.deleted_at.is_(None),
            )
        )
        if workspace is None:
            raise LookupError("Rota de webhook não encontrada.")
        await set_workspace_context(self.db, workspace.id)
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace.id,
                OmnichannelChannelConnection.provider == provider,
                OmnichannelChannelConnection.connection_key == connection_key,
                OmnichannelChannelConnection.status.not_in(
                    {"paused", "disconnected_local"}
                ),
            )
        )
        if connection is None:
            raise LookupError("Rota de webhook não encontrada.")
        credential = await self.db.scalar(
            select(OmnichannelWebhookCredential).where(
                OmnichannelWebhookCredential.workspace_id == workspace.id,
                OmnichannelWebhookCredential.connection_id == connection.id,
                OmnichannelWebhookCredential.state == "active",
            )
        )
        if credential is None:
            raise LookupError("Rota de webhook não encontrada.")
        return workspace, connection, credential

    @staticmethod
    def verify_challenge(
        credential: OmnichannelWebhookCredential,
        *,
        mode: str,
        verify_token: str,
    ) -> None:
        if mode != "subscribe":
            raise WebhookSignatureError("Modo de verificação Meta inválido.")
        expected = IntegrationSecretCipher().decrypt(credential.verify_token_encrypted) or ""
        if not expected or not hmac.compare_digest(expected, verify_token):
            raise WebhookSignatureError("Token de verificação inválido.")

    @staticmethod
    def verify_signature(
        credential: OmnichannelWebhookCredential,
        *,
        provider: str,
        body: bytes,
        meta_signature: str = "",
        tiktok_signature: str = "",
        klm_signature: str = "",
        klm_timestamp: str = "",
    ) -> tuple[datetime | None, str, str]:
        secret = IntegrationSecretCipher().decrypt(credential.signing_secret_encrypted) or ""
        if not secret:
            raise WebhookSignatureError("Credencial de assinatura indisponível.")

        if provider in META_INGRESS_PROVIDERS:
            supplied = meta_signature.strip()
            if not supplied.startswith("sha256="):
                raise WebhookSignatureError("Assinatura Meta ausente ou inválida.")
            expected = "sha256=" + hmac.new(
                secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, supplied):
                raise WebhookSignatureError("Assinatura Meta inválida.")
            return None, "meta_x_hub_signature_256", supplied

        if provider in TIKTOK_INGRESS_PROVIDERS:
            supplied = tiktok_signature.strip()
            parts = _parse_signature_parts(supplied)
            timestamp_value = parts.get("t", "")
            signature = parts.get("s", "")
            occurred_at = _parse_timestamp(
                timestamp_value,
                tolerance_seconds=credential.timestamp_tolerance_seconds,
            )
            signed_payload = f"{timestamp_value}.".encode() + body
            expected = hmac.new(
                secret.encode("utf-8"), signed_payload, hashlib.sha256
            ).hexdigest()
            if not signature or not hmac.compare_digest(expected, signature):
                raise WebhookSignatureError("Assinatura TikTok inválida.")
            return occurred_at, "tiktok_signature_v1", supplied

        supplied = klm_signature.strip()
        occurred_at = _parse_timestamp(
            klm_timestamp.strip(),
            tolerance_seconds=credential.timestamp_tolerance_seconds,
        )
        normalized_signature = supplied.removeprefix("sha256=")
        signed_payload = f"{klm_timestamp.strip()}.".encode() + body
        expected = hmac.new(
            secret.encode("utf-8"), signed_payload, hashlib.sha256
        ).hexdigest()
        if not normalized_signature or not hmac.compare_digest(
            expected, normalized_signature
        ):
            raise WebhookSignatureError("Assinatura do formulário inválida.")
        return occurred_at, "klm_timestamp_hmac_v1", supplied

    async def claim_ingress(
        self,
        *,
        workspace_id: UUID,
        connection: OmnichannelChannelConnection,
        external_event_id: str,
        external_lead_id: str | None,
        body: bytes,
        signature_material: str,
        signature_timestamp: datetime | None,
        signature_scheme: str,
        request_metadata: dict[str, Any],
    ) -> tuple[OmnichannelPublicIngressReceipt, bool]:
        payload_sha256 = hashlib.sha256(body).hexdigest()
        existing = await self.db.scalar(
            select(OmnichannelPublicIngressReceipt).where(
                OmnichannelPublicIngressReceipt.workspace_id == workspace_id,
                OmnichannelPublicIngressReceipt.provider == connection.provider,
                OmnichannelPublicIngressReceipt.connection_key == connection.connection_key,
                OmnichannelPublicIngressReceipt.external_event_id == external_event_id,
            )
        )
        if existing is not None:
            if existing.payload_sha256 != payload_sha256:
                raise PublicIngressConflictError(
                    "O evento externo já foi recebido com bytes diferentes."
                )
            return existing, True

        receipt = OmnichannelPublicIngressReceipt(
            workspace_id=workspace_id,
            connection_id=connection.id,
            provider=connection.provider,
            connection_key=connection.connection_key,
            external_event_id=external_event_id,
            external_lead_id=external_lead_id,
            payload_sha256=payload_sha256,
            signature_sha256=_signature_digest(signature_material),
            signature_timestamp=signature_timestamp,
            signature_scheme=signature_scheme,
            request_metadata=request_metadata,
            received_at=_now(),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(receipt)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(OmnichannelPublicIngressReceipt).where(
                    OmnichannelPublicIngressReceipt.workspace_id == workspace_id,
                    OmnichannelPublicIngressReceipt.provider == connection.provider,
                    OmnichannelPublicIngressReceipt.connection_key == connection.connection_key,
                    OmnichannelPublicIngressReceipt.external_event_id == external_event_id,
                )
            )
            if existing is None:
                raise
            if existing.payload_sha256 != payload_sha256:
                raise PublicIngressConflictError(
                    "O evento externo já foi recebido com bytes diferentes."
                )
            return existing, True
        credential = await self.db.scalar(
            select(OmnichannelWebhookCredential).where(
                OmnichannelWebhookCredential.workspace_id == workspace_id,
                OmnichannelWebhookCredential.connection_id == connection.id,
            )
        )
        if credential is not None:
            credential.last_verified_at = _now()
        return receipt, False
