from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.entitlement import EntitlementKey
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.workspace import Workspace
from app.schemas.whatsapp import (
    WhatsAppConnectionCreateManual,
    WhatsAppConnectionCreateQr,
    WhatsAppConnectionRead,
    WhatsAppConnectionStatus,
    WhatsAppConnectionStatusRead,
)
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.helpers import build_last_error, normalize_phone_number
from app.services.whatsapp.meta_cloud_client import MetaCloudClient, meta_request_scope
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.whatsapp.secrets import WhatsAppSecretCipher, WhatsAppSecretDecryptionError

logger = logging.getLogger(__name__)

# Graph API error_subcode: PIN de verificação em duas etapas não confere no
# re-registro do número (ver docs/AUDITORIA_META_CANAIS_2026-07-23.md, item de
# re-registro).
META_PIN_MISMATCH_SUBCODE = 133005


class WhatsAppConnectionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        cipher: Optional[WhatsAppSecretCipher] = None,
        meta_client: Optional[MetaCloudClient] = None,
        qr_client: Optional[QrGatewayClient] = None,
    ) -> None:
        self.db = db
        self.cipher = cipher or WhatsAppSecretCipher()
        self.meta_client = meta_client or MetaCloudClient()
        self.qr_client = qr_client or QrGatewayClient()

    async def list_connections(self, workspace_id: UUID) -> List[WhatsAppConnectionRead]:
        result = await self.db.execute(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.workspace_id == workspace_id)
            .order_by(WhatsAppConnection.is_default.desc(), WhatsAppConnection.created_at.asc())
        )
        return [self._to_read_model(conn) for conn in result.scalars().all()]

    async def get_connection(self, connection_id: UUID, workspace_id: UUID) -> WhatsAppConnection:
        result = await self.db.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.id == connection_id,
                WhatsAppConnection.workspace_id == workspace_id,
            )
        )
        connection = result.scalars().first()
        if not connection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-005", "message": "Conexão WhatsApp não encontrada."},
            )
        return connection

    async def get_default_connection(
        self,
        workspace_id: UUID,
        *,
        connected_only: bool = False,
    ) -> Optional[WhatsAppConnection]:
        query = select(WhatsAppConnection).where(
            WhatsAppConnection.workspace_id == workspace_id,
            WhatsAppConnection.is_default.is_(True),
        )
        if connected_only:
            query = query.where(WhatsAppConnection.status == WaConnectionStatus.connected)
        query = query.limit(1)
        result = await self.db.execute(query)
        connection = result.scalars().first()
        if connection:
            return connection

        if connected_only:
            result = await self.db.execute(
                select(WhatsAppConnection)
                .where(
                    WhatsAppConnection.workspace_id == workspace_id,
                    WhatsAppConnection.status == WaConnectionStatus.connected,
                )
                .order_by(WhatsAppConnection.created_at.asc())
                .limit(1)
            )
            return result.scalars().first()
        return None

    async def get_legacy_status(self, workspace_id: UUID) -> WhatsAppConnectionStatus:
        connection = await self.get_default_connection(workspace_id)
        webhook_url = f"{settings.PAPERCLIP_PUBLIC_URL.rstrip('/')}/api/v1/whatsapp/webhook"
        if not connection:
            return WhatsAppConnectionStatus(
                status=WaConnectionStatus.disconnected,
                phoneNumber=None,
                displayName=None,
                webhookUrl=webhook_url,
                verifyToken=settings.WHATSAPP_VERIFY_TOKEN,
                webhookRegistered=False,
            )

        if connection.connection_type == ConnectionType.QR_CODE:
            await self.refresh_qr_status(connection)

        return WhatsAppConnectionStatus(
            status=connection.status,
            connectionId=connection.id,
            connectionType=connection.connection_type,
            phoneNumberId=connection.phone_number_id,
            phoneNumber=connection.display_phone_number or connection.phone_number,
            displayName=connection.display_name,
            webhookUrl=webhook_url,
            verifyToken=settings.WHATSAPP_VERIFY_TOKEN,
            webhookRegistered=connection.webhook_registered,
            lastError=build_last_error(connection.last_error_code, connection.last_error_message),
        )

    async def create_or_update_manual_connection(
        self,
        *,
        workspace_id: UUID,
        payload: WhatsAppConnectionCreateManual,
        update_default: bool = False,
    ) -> WhatsAppConnectionRead:
        connection = await self._find_connection_for_upsert(
            workspace_id=workspace_id,
            phone_number_id=payload.phone_number_id,
            preferred_default=update_default,
        )
        await self._enforce_connection_capacity(workspace_id, connection)
        registration_pin = payload.registration_pin or self._reuse_or_generate_pin(connection)
        try:
            async with meta_request_scope(self.meta_client):
                details = await self.meta_client.get_phone_number_details(
                    payload.phone_number_id, payload.access_token
                )
                await self.meta_client.subscribe_app(
                    payload.business_account_id, payload.access_token
                )
                await self.meta_client.register_phone_number(
                    payload.phone_number_id,
                    payload.access_token,
                    registration_pin,
                )
        except WhatsAppProviderError as exc:
            raise self.translate_registration_error(exc) from exc

        default_connection = await self.get_default_connection(workspace_id)
        is_new_connection = connection.id is None
        is_default = payload.is_default or update_default or (is_new_connection and default_connection is None)
        self._apply_meta_connection_data(
            connection=connection,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            transport_mode=payload.transport_mode,
            access_token=payload.access_token,
            business_token=None,
            phone_number_id=payload.phone_number_id,
            business_account_id=payload.business_account_id,
            waba_id=payload.business_account_id,
            details=details,
            is_default=is_default,
        )
        connection.webhook_registered = True
        connection.registration_pin_encrypted = self.cipher.encrypt(registration_pin)
        if payload.app_secret is not None:
            # BYOA multi-tenant: o escritório colou o App Secret do próprio
            # app Meta; grava cifrado na conexão para a validação de
            # assinatura do webhook por conexão (webhook_service.py). Uma
            # string vazia limpa o segredo (volta a depender do
            # WHATSAPP_APP_SECRET global como fallback).
            stripped = payload.app_secret.strip()
            connection.app_secret_encrypted = self.cipher.encrypt(stripped) if stripped else None
        await self._cleanup_legacy_workspace_settings(workspace_id)
        await self._persist_connection(connection, workspace_id, is_default=is_default)
        return self._to_read_model(connection)

    async def create_qr_connection(
        self,
        *,
        workspace_id: UUID,
        payload: WhatsAppConnectionCreateQr,
    ) -> WhatsAppConnectionRead:
        await self._enforce_connection_capacity(workspace_id, None)
        try:
            instance = await self.qr_client.create_instance(
                workspace_id=workspace_id,
                display_name=payload.display_name,
                provider=payload.provider,
            )
        except WhatsAppProviderError as exc:
            raise exc.to_http_exception() from exc

        existing = None
        instance_id = instance.get("instance_id")
        if instance_id:
            result = await self.db.execute(
                select(WhatsAppConnection).where(WhatsAppConnection.instance_id == instance_id)
            )
            existing = result.scalars().first()
            if existing and existing.workspace_id != workspace_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WA-006", "message": "Esta instância QR já pertence a outro workspace."},
                )

        connection = existing or WhatsAppConnection(workspace_id=workspace_id)
        is_default = payload.is_default or await self.get_default_connection(workspace_id) is None
        connection.connection_type = ConnectionType.QR_CODE
        connection.transport_mode = "qr_gateway"
        connection.is_default = is_default
        connection.instance_id = instance.get("instance_id")
        connection.instance_token_encrypted = self.cipher.encrypt(instance.get("instance_token"))
        connection.qr_provider = payload.provider
        connection.qr_status = instance.get("status")
        connection.status = self._map_qr_status_to_connection_status(instance.get("status"))
        connection.display_name = payload.display_name or connection.display_name
        connection.phone_number = normalize_phone_number(instance.get("phone_number")) or connection.phone_number
        connection.display_phone_number = instance.get("display_phone_number") or connection.display_phone_number
        connection.connected_at = datetime.now(timezone.utc) if connection.status == WaConnectionStatus.connected else None
        connection.disconnected_at = None
        connection.last_error_code = None
        connection.last_error_message = None
        connection.qr_last_sync_at = datetime.now(timezone.utc)

        await self._persist_connection(connection, workspace_id, is_default=is_default)
        return self._to_read_model(connection)

    async def save_tech_provider_connection(
        self,
        *,
        workspace_id: UUID,
        business_token: str,
        phone_number_id: str,
        waba_id: str,
        registration_pin: str | None,
        token_expires_at: datetime | None = None,
        transport_mode: str = "official_api",
        is_default: bool = True,
    ) -> WhatsAppConnectionRead:
        connection = await self._find_connection_for_upsert(
            workspace_id=workspace_id,
            phone_number_id=phone_number_id,
            preferred_default=is_default,
        )
        await self._enforce_connection_capacity(workspace_id, connection)
        details = await self.meta_client.get_phone_number_details(phone_number_id, business_token)
        default_needed = is_default or (connection.id is None and await self.get_default_connection(workspace_id) is None)
        self._apply_meta_connection_data(
            connection=connection,
            connection_type=ConnectionType.TECH_PROVIDER,
            transport_mode=transport_mode,
            access_token=business_token,
            business_token=business_token,
            phone_number_id=phone_number_id,
            business_account_id=waba_id,
            waba_id=waba_id,
            details=details,
            is_default=default_needed,
        )
        connection.webhook_registered = True
        connection.registration_pin_encrypted = (
            self.cipher.encrypt(registration_pin) if registration_pin else None
        )
        connection.token_expires_at = token_expires_at
        await self._cleanup_legacy_workspace_settings(workspace_id)
        await self._persist_connection(connection, workspace_id, is_default=default_needed)
        return self._to_read_model(connection)

    async def record_coexistence_sync(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        contacts_request_id: str | None,
        history_request_id: str | None,
        error: WhatsAppProviderError | None = None,
    ) -> WhatsAppConnectionRead:
        connection = await self.get_connection(connection_id, workspace_id)
        connection.coexistence_contacts_sync_request_id = contacts_request_id
        connection.coexistence_history_sync_request_id = history_request_id
        connection.coexistence_sync_requested_at = datetime.now(UTC)
        if error is None:
            connection.coexistence_sync_status = "requested"
            connection.last_error_code = None
            connection.last_error_message = None
        else:
            connection.coexistence_sync_status = (
                "partial" if contacts_request_id or history_request_id else "failed"
            )
            connection.last_error_code = error.code
            connection.last_error_message = error.message
        await self._persist_connection(connection, workspace_id, is_default=connection.is_default)
        return self._to_read_model(connection)

    async def set_default_connection(self, workspace_id: UUID, connection_id: UUID) -> WhatsAppConnectionRead:
        connection = await self.get_connection(connection_id, workspace_id)
        await self._unset_default_connections(workspace_id)
        connection.is_default = True
        await self._sync_omnichannel_projection(connection)
        await self.db.commit()
        await self.db.refresh(connection)
        return self._to_read_model(connection)

    async def disconnect_default_connection(self, workspace_id: UUID) -> None:
        connection = await self.get_default_connection(workspace_id)
        if connection:
            await self._disconnect_connection(connection)

    async def delete_connection(self, workspace_id: UUID, connection_id: UUID) -> None:
        connection = await self.get_connection(connection_id, workspace_id)
        await self._disconnect_connection(connection, clear_secrets=True)
        if connection.is_default:
            replacement = await self.get_default_connection(workspace_id, connected_only=True)
            if replacement and replacement.id != connection.id:
                await self.set_default_connection(workspace_id, replacement.id)

    async def get_connection_status(self, workspace_id: UUID, connection_id: UUID) -> WhatsAppConnectionStatusRead:
        connection = await self.get_connection(connection_id, workspace_id)
        if connection.connection_type == ConnectionType.QR_CODE:
            await self.refresh_qr_status(connection)
        return WhatsAppConnectionStatusRead(
            id=connection.id,
            status=connection.status,
            connectionType=connection.connection_type,
            transportMode=connection.transport_mode,
            qrStatus=connection.qr_status,
            webhookRegistered=connection.webhook_registered,
            lastError=build_last_error(connection.last_error_code, connection.last_error_message),
            connectedAt=connection.connected_at,
            disconnectedAt=connection.disconnected_at,
            displayPhoneNumber=connection.display_phone_number,
            phoneNumber=connection.phone_number,
            phoneNumberId=connection.phone_number_id,
            instanceId=connection.instance_id,
        )

    async def refresh_qr_status(self, connection: WhatsAppConnection) -> WhatsAppConnection:
        if connection.connection_type != ConnectionType.QR_CODE:
            return connection
        if connection.status == WaConnectionStatus.disconnected:
            await self._enforce_connection_capacity(connection.workspace_id, connection)
        try:
            data = await self.qr_client.get_instance_status(connection, self.get_instance_token(connection))
        except WhatsAppProviderError as exc:
            await self.mark_connection_error(connection, exc.code, exc.message)
            return connection
        connection.qr_status = data.get("status")
        connection.status = self._map_qr_status_to_connection_status(connection.qr_status)
        connection.phone_number = normalize_phone_number(data.get("phone_number")) or connection.phone_number
        connection.display_phone_number = data.get("display_phone_number") or connection.display_phone_number
        connection.qr_last_sync_at = datetime.now(timezone.utc)
        if connection.status == WaConnectionStatus.connected and connection.connected_at is None:
            connection.connected_at = datetime.now(timezone.utc)
        connection.last_error_code = None
        connection.last_error_message = None
        await self.db.commit()
        await self.db.refresh(connection)
        return connection

    async def find_connection_by_phone_number_id(self, phone_number_id: Optional[str]) -> Optional[WhatsAppConnection]:
        if not phone_number_id:
            return None
        result = await self.db.execute(
            select(WhatsAppConnection).where(WhatsAppConnection.phone_number_id == phone_number_id)
        )
        return result.scalars().first()

    async def find_connection_by_display_phone_number(self, display_phone_number: Optional[str]) -> Optional[WhatsAppConnection]:
        normalized = normalize_phone_number(display_phone_number)
        if not normalized and not display_phone_number:
            return None
        conditions = []
        if display_phone_number:
            conditions.append(WhatsAppConnection.display_phone_number == display_phone_number)
            conditions.append(WhatsAppConnection.phone_number == display_phone_number)
        if normalized:
            conditions.append(WhatsAppConnection.phone_number == normalized)
        result = await self.db.execute(select(WhatsAppConnection).where(or_(*conditions)).limit(1))
        return result.scalars().first()

    async def mark_connection_error(self, connection: WhatsAppConnection, code: str, message: str) -> None:
        connection.last_error_code = code
        connection.last_error_message = message
        connection.status = WaConnectionStatus.error
        await self._sync_omnichannel_projection(connection)
        await self.db.commit()

    def get_meta_access_token(self, connection: WhatsAppConnection) -> str:
        encrypted = connection.business_token_encrypted if connection.connection_type == ConnectionType.TECH_PROVIDER else connection.access_token_encrypted
        try:
            token = self.cipher.decrypt_strict(encrypted) or self.cipher.decrypt_strict(
                connection.access_token_encrypted
            )
        except WhatsAppSecretDecryptionError as exc:
            raise WhatsAppProviderError(
                code="WA-SECRET-DECRYPT",
                message=(
                    "Não foi possível decifrar o token da conexão WhatsApp com nenhuma "
                    "chave configurada. A conexão será marcada como erro; rode a "
                    "rotação de chaves (scripts/rotate_whatsapp_secrets.py) ou "
                    "reconecte o número."
                ),
                status_code=503,
                connection_fault=True,
            ) from exc
        if not token:
            raise WhatsAppProviderError(
                code="WA-007",
                message="Token de acesso do WhatsApp não está disponível para esta conexão.",
                status_code=503,
                connection_fault=True,
            )
        return token

    async def resolve_meta_access_token(self, connection: WhatsAppConnection) -> str:
        """Same as `get_meta_access_token`, but on total decrypt failure also marks
        the connection as `error` (with a legible reason) and records a
        `KlmAuditEvent`, instead of leaving the caller to discover a 503 later."""
        try:
            return self.get_meta_access_token(connection)
        except WhatsAppProviderError as exc:
            if exc.code == "WA-SECRET-DECRYPT":
                await self._mark_secret_decrypt_error(connection, exc.message)
            raise

    async def _mark_secret_decrypt_error(
        self, connection: WhatsAppConnection, reason: str
    ) -> None:
        from app.models.klm_operations import KlmAuditEvent

        await self.mark_connection_error(connection, "WA-SECRET-DECRYPT", reason)
        self.db.add(
            KlmAuditEvent(
                workspace_id=connection.workspace_id,
                actor_type="system",
                actor_label="whatsapp_secret_cipher",
                event_type="whatsapp_connection_secret_decrypt_failed",
                entity_type="whatsapp_connection",
                entity_id=connection.id,
                summary=reason,
                after_data={"connectionId": str(connection.id), "status": "error"},
            )
        )
        await self.db.commit()

    def get_instance_token(self, connection: WhatsAppConnection) -> Optional[str]:
        try:
            return self.cipher.decrypt_strict(connection.instance_token_encrypted)
        except WhatsAppSecretDecryptionError as exc:
            raise WhatsAppProviderError(
                code="WA-SECRET-DECRYPT",
                message=(
                    "Não foi possível decifrar o token de instância QR com nenhuma "
                    "chave configurada. A conexão será marcada como erro."
                ),
                status_code=503,
                connection_fault=True,
            ) from exc

    def generate_registration_pin(self) -> str:
        return f"{secrets.randbelow(10**6):06d}"

    def _reuse_or_generate_pin(self, connection: WhatsAppConnection) -> str:
        """Reuse the PIN already stored for an existing connection instead of
        generating a new one on every re-registration (Meta ties the two-step
        verification PIN to the phone number; sending a different value than the
        one the number already has yields error_subcode 133005)."""
        if connection.id is not None and connection.registration_pin_encrypted:
            stored = self.cipher.decrypt(connection.registration_pin_encrypted)
            if stored:
                return stored
        return self.generate_registration_pin()

    async def get_stored_registration_pin(
        self, workspace_id: UUID, phone_number_id: str
    ) -> Optional[str]:
        connection = await self.find_connection_by_phone_number_id(phone_number_id)
        if connection is None or connection.workspace_id != workspace_id:
            return None
        return self._reuse_or_generate_pin(connection) if connection.registration_pin_encrypted else None

    @staticmethod
    def translate_registration_error(exc: WhatsAppProviderError) -> HTTPException:
        if exc.provider_error_subcode == META_PIN_MISMATCH_SUBCODE:
            return HTTPException(
                status_code=422,
                detail={
                    "code": "WA-PIN-MISMATCH",
                    "message": (
                        "PIN de verificação em duas etapas divergente do já cadastrado "
                        "para este número na Meta. Informe o PIN original definido no "
                        "primeiro registro (não é possível trocá-lo por aqui) ou "
                        "solicite a redefinição do PIN diretamente no WhatsApp Manager."
                    ),
                },
            )
        return exc.to_http_exception()

    async def _find_connection_for_upsert(
        self,
        *,
        workspace_id: UUID,
        phone_number_id: str,
        preferred_default: bool,
    ) -> WhatsAppConnection:
        by_phone = await self.find_connection_by_phone_number_id(phone_number_id)
        if by_phone:
            if by_phone.workspace_id != workspace_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WA-006", "message": "Este número já está conectado em outro workspace."},
                )
            return by_phone
        if preferred_default:
            connection = await self.get_default_connection(workspace_id)
            if connection:
                return connection
        return WhatsAppConnection(workspace_id=workspace_id)

    async def preflight_tech_provider_connection(
        self,
        *,
        workspace_id: UUID,
        phone_number_id: str | None,
        preferred_default: bool,
    ) -> None:
        connection = None
        if phone_number_id:
            connection = await self._find_connection_for_upsert(
                workspace_id=workspace_id,
                phone_number_id=phone_number_id,
                preferred_default=preferred_default,
            )
        await self._enforce_connection_capacity(workspace_id, connection)

    async def _enforce_connection_capacity(
        self,
        workspace_id: UUID,
        connection: WhatsAppConnection | None,
    ) -> None:
        if connection is not None and connection.id is not None:
            if connection.status != WaConnectionStatus.disconnected:
                return

        async def load_current_quantity() -> int:
            value = await self.db.scalar(
                select(func.count(WhatsAppConnection.id)).where(
                    WhatsAppConnection.workspace_id == workspace_id,
                    WhatsAppConnection.status != WaConnectionStatus.disconnected,
                )
            )
            return int(value or 0)

        await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.whatsapp_numbers,
            load_current_quantity=load_current_quantity,
        )

    def _apply_meta_connection_data(
        self,
        *,
        connection: WhatsAppConnection,
        connection_type: ConnectionType,
        transport_mode: str,
        access_token: Optional[str],
        business_token: Optional[str],
        phone_number_id: str,
        business_account_id: Optional[str],
        waba_id: Optional[str],
        details: dict,
        is_default: bool,
    ) -> None:
        display_phone_number = details.get("display_phone_number")
        verified_name = details.get("verified_name")
        connection.connection_type = connection_type
        connection.transport_mode = transport_mode
        connection.is_default = is_default
        connection.phone_number_id = phone_number_id
        connection.business_account_id = business_account_id
        connection.waba_id = waba_id or business_account_id
        connection.display_phone_number = display_phone_number
        connection.phone_number = normalize_phone_number(display_phone_number)
        connection.display_name = verified_name
        connection.access_token_encrypted = self.cipher.encrypt(access_token)
        if business_token:
            connection.business_token_encrypted = self.cipher.encrypt(business_token)
        if connection_type != ConnectionType.TECH_PROVIDER:
            connection.token_expires_at = None
        connection.instance_id = None
        connection.instance_token_encrypted = None
        connection.qr_provider = None
        connection.qr_status = None
        connection.qr_last_sync_at = None
        connection.webhook_registered = False
        connection.status = WaConnectionStatus.connected
        connection.connected_at = datetime.now(timezone.utc)
        connection.disconnected_at = None
        connection.last_error_code = None
        connection.last_error_message = None

    async def _persist_connection(
        self,
        connection: WhatsAppConnection,
        workspace_id: UUID,
        *,
        is_default: bool,
    ) -> None:
        if connection.workspace_id != workspace_id:
            connection.workspace_id = workspace_id
        if is_default:
            await self._unset_default_connections(workspace_id, exclude_id=connection.id)
            connection.is_default = True
        self.db.add(connection)
        try:
            await self.db.flush()
            await self._sync_omnichannel_projection(connection)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "WA-006", "message": "Já existe uma conexão com este identificador externo."},
            ) from exc
        await self.db.refresh(connection)

    async def _unset_default_connections(self, workspace_id: UUID, exclude_id: Optional[UUID] = None) -> None:
        result = await self.db.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.workspace_id == workspace_id,
                WhatsAppConnection.is_default.is_(True),
            )
        )
        for item in result.scalars().all():
            if exclude_id and item.id == exclude_id:
                continue
            item.is_default = False

    async def _disconnect_connection(self, connection: WhatsAppConnection, *, clear_secrets: bool = False) -> None:
        connection.status = WaConnectionStatus.disconnected
        connection.disconnected_at = datetime.now(timezone.utc)
        connection.connected_at = None
        connection.webhook_registered = False
        if clear_secrets:
            connection.access_token_encrypted = None
            connection.business_token_encrypted = None
            connection.token_expires_at = None
            connection.instance_token_encrypted = None
            connection.registration_pin_encrypted = None
            connection.app_secret_encrypted = None
            connection.webhook_verify_token_encrypted = None
        await self._sync_omnichannel_projection(connection)
        await self.db.commit()

    async def _sync_omnichannel_projection(self, connection: WhatsAppConnection) -> None:
        """Keep the native route authoritative without copying provider secrets."""
        if connection.id is None:
            return
        from app.models.klm_operations import OmnichannelChannel
        from app.models.omnichannel_v6 import OmnichannelChannelConnection
        from app.schemas.omnichannel_v6 import ChannelConnectionCreate
        from app.services.omnichannel_orchestration_service import (
            OmnichannelOrchestrationService,
        )

        is_qr = connection.connection_type == ConnectionType.QR_CODE
        provider = "qr_gateway" if is_qr else "meta_cloud"
        transport_mode = "qr_gateway" if is_qr else connection.transport_mode
        connection_key = f"whatsapp:{connection.id}"
        projection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == connection.workspace_id,
                OmnichannelChannelConnection.provider == provider,
                OmnichannelChannelConnection.connection_key == connection_key,
            )
        )
        configuration = {
            "legacyConnectionId": str(connection.id),
            "phoneNumberId": connection.phone_number_id,
            "phoneNumber": connection.phone_number,
            "displayPhoneNumber": connection.display_phone_number,
            "wabaId": connection.waba_id,
            "businessAccountId": connection.business_account_id,
            "instanceId": connection.instance_id,
            "qrProvider": connection.qr_provider,
            "coexistenceState": (
                "linked" if connection.transport_mode == "coexistence" else "not_applicable"
            ),
        }
        profile_payload = ChannelConnectionCreate(
            channel=OmnichannelChannel.whatsapp,
            provider=provider,
            connection_key=connection_key,
            display_name=connection.display_name or connection.display_phone_number or "WhatsApp",
            external_account_id=(connection.instance_id if is_qr else connection.phone_number_id),
            transport_mode=transport_mode,
            is_default=connection.is_default,
            configuration={key: value for key, value in configuration.items() if value is not None},
        )
        capabilities = OmnichannelOrchestrationService._connection_profile(profile_payload)
        capabilities["outboundIdempotency"] = {
            "supported": False,
            "enabled": False,
            "state": "provider_not_guaranteed",
            "source": provider,
            "note": (
                "A chave KLM é usada para correlação; a Meta não garante deduplicação do POST."
                if not is_qr
                else "A chave KLM protege o hot path; o gateway não declara deduplicação externa."
            ),
        }
        if projection is None:
            projection = OmnichannelChannelConnection(
                workspace_id=connection.workspace_id,
                channel="whatsapp",
                provider=provider,
                connection_key=connection_key,
                display_name=profile_payload.display_name,
                external_account_id=profile_payload.external_account_id,
                transport_mode=transport_mode,
                is_default=connection.is_default,
                capabilities=capabilities,
                limits={},
                routing_policy={
                    "canonicalConversation": True,
                    "transferEnabled": True,
                    "serviceWindowHours": 24,
                },
                cost_policy={"ledgerEnabled": True, "currency": "BRL"},
                configuration=profile_payload.configuration,
                credential_reference=f"whatsapp-connection://{connection.id}",
            )
            self.db.add(projection)
        else:
            projection.display_name = profile_payload.display_name
            projection.external_account_id = profile_payload.external_account_id
            projection.transport_mode = transport_mode
            projection.is_default = connection.is_default
            projection.capabilities = capabilities
            projection.configuration = profile_payload.configuration
            projection.credential_reference = f"whatsapp-connection://{connection.id}"
            projection.version += 1
        projection.status = (
            "connected"
            if connection.status == WaConnectionStatus.connected
            else "paused"
            if connection.status == WaConnectionStatus.pending
            else "disconnected_local"
            if connection.status == WaConnectionStatus.disconnected
            else "error"
        )
        projection.webhook_state = (
            "configured" if connection.webhook_registered else "not_configured"
        )
        projection.connected_at = connection.connected_at
        projection.health = {
            "state": projection.status,
            "lastErrorCode": connection.last_error_code,
            "externalChecks": 0,
        }
        if connection.is_default:
            other_defaults = await self.db.scalars(
                select(OmnichannelChannelConnection).where(
                    OmnichannelChannelConnection.workspace_id == connection.workspace_id,
                    OmnichannelChannelConnection.channel == "whatsapp",
                    OmnichannelChannelConnection.id != projection.id,
                    OmnichannelChannelConnection.is_default.is_(True),
                )
            )
            for other in other_defaults:
                other.is_default = False

    async def ensure_omnichannel_projection(
        self, connection: WhatsAppConnection
    ) -> WhatsAppConnection:
        """Self-heal legacy connections inside the caller's transaction."""
        persisted = await self.db.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.id == connection.id,
                WhatsAppConnection.workspace_id == connection.workspace_id,
            )
        )
        if persisted is None:
            raise LookupError("Conexão WhatsApp não encontrada no workspace.")
        await self._sync_omnichannel_projection(persisted)
        await self.db.flush()
        return persisted

    async def _cleanup_legacy_workspace_settings(self, workspace_id: UUID) -> None:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            return
        current_settings = dict(workspace.settings or {})
        if "whatsapp" in current_settings:
            current_settings.pop("whatsapp", None)
            workspace.settings = current_settings
            await self.db.flush()

    def _map_qr_status_to_connection_status(self, qr_status: Optional[str]) -> WaConnectionStatus:
        normalized = (qr_status or "").lower()
        if normalized in {"connected", "online", "open", "authenticated"}:
            return WaConnectionStatus.connected
        if normalized in {"error", "failed", "banned"}:
            return WaConnectionStatus.error
        if normalized in {"disconnected", "offline"}:
            return WaConnectionStatus.disconnected
        return WaConnectionStatus.pending

    def _to_read_model(self, connection: WhatsAppConnection) -> WhatsAppConnectionRead:
        token_lifecycle_state, token_expires_in_days = self._token_lifecycle(connection)
        return WhatsAppConnectionRead(
            id=connection.id,
            connectionType=connection.connection_type,
            transportMode=connection.transport_mode,
            isDefault=connection.is_default,
            phoneNumberId=connection.phone_number_id,
            phoneNumber=connection.phone_number,
            displayPhoneNumber=connection.display_phone_number,
            displayName=connection.display_name,
            businessAccountId=connection.business_account_id,
            wabaId=connection.waba_id,
            instanceId=connection.instance_id,
            qrProvider=connection.qr_provider,
            qrStatus=connection.qr_status,
            status=connection.status,
            webhookRegistered=connection.webhook_registered,
            connectedAt=connection.connected_at,
            disconnectedAt=connection.disconnected_at,
            updatedAt=connection.updated_at,
            lastError=build_last_error(connection.last_error_code, connection.last_error_message),
            appSecretConfigured=bool(connection.app_secret_encrypted),
            coexistenceSyncStatus=connection.coexistence_sync_status,
            coexistenceContactsSyncRequestId=connection.coexistence_contacts_sync_request_id,
            coexistenceHistorySyncRequestId=connection.coexistence_history_sync_request_id,
            coexistenceSyncRequestedAt=connection.coexistence_sync_requested_at,
            tokenExpiresAt=connection.token_expires_at,
            tokenLifecycleState=token_lifecycle_state,
            tokenExpiresInDays=token_expires_in_days,
        )

    @staticmethod
    def _token_lifecycle(connection: WhatsAppConnection) -> tuple[str, int | None]:
        if connection.connection_type != ConnectionType.TECH_PROVIDER:
            return "not_applicable", None
        if connection.token_expires_at is None:
            return "unknown", None
        expires_at = connection.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        seconds = (expires_at - datetime.now(UTC)).total_seconds()
        if seconds <= 0:
            return "expired", 0
        days = int((seconds + 86_399) // 86_400)
        if seconds <= 14 * 86_400:
            return "expiring", days
        return "valid", days
