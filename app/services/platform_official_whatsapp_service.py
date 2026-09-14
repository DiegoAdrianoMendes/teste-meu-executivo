from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging_privacy import safe_error_type
from app.db.tenant import TenantSession
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppDelivery,
    PlatformOfficialWhatsAppDeliveryState,
    PlatformOfficialWhatsAppRole,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus
from app.schemas.platform_whatsapp import (
    PlatformOfficialWhatsAppActiveUpdate,
    PlatformOfficialWhatsAppConnectionRead,
    PlatformOfficialWhatsAppConnectManual,
    PlatformOfficialWhatsAppEmbeddedCallback,
    PlatformOfficialWhatsAppTestResult,
)
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.helpers import build_last_error, normalize_phone_number
from app.services.whatsapp.meta_cloud_client import (
    MetaBusinessTokenGrant,
    MetaCloudClient,
    meta_request_scope,
)
from app.services.whatsapp.secrets import (
    WhatsAppSecretCipher,
    WhatsAppSecretDecryptionError,
)

_DELIVERY_ACTION = "platform_official_whatsapp_text"
_TERMINAL_DELIVERY_STATES = {
    PlatformOfficialWhatsAppDeliveryState.sent,
    PlatformOfficialWhatsAppDeliveryState.delivery_uncertain,
    PlatformOfficialWhatsAppDeliveryState.failed,
}


def _require_external_delivery(action: str) -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KLM-EXT-001",
                "message": (
                    f"{action} bloqueado enquanto entregas externas estiverem desabilitadas."
                ),
            },
        )


class PlatformOfficialWhatsAppService:
    ROLE_ORDER = [
        PlatformOfficialWhatsAppRole.notifications,
        PlatformOfficialWhatsAppRole.outbound,
        PlatformOfficialWhatsAppRole.support,
    ]

    def __init__(
        self,
        db: AsyncSession,
        *,
        cipher: WhatsAppSecretCipher | None = None,
        meta_client: MetaCloudClient | None = None,
        delivery_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self.db = db
        self.cipher = cipher or WhatsAppSecretCipher()
        self.meta_client = meta_client or MetaCloudClient()
        if delivery_session_factory is None:
            if db.bind is None:
                raise RuntimeError(
                    "A sessão do WhatsApp oficial precisa estar vinculada a um banco."
                )
            # Delivery is a separate control-plane transaction.  In particular,
            # it must never commit pending ORM changes owned by the caller just
            # to make the provider-effect intent durable.  Reusing the bind keeps
            # tests and alternate runtimes on their configured database while a
            # fresh TenantSession preserves the normal PostgreSQL session hooks.
            delivery_bind = db.bind.engine if isinstance(db.bind, AsyncConnection) else db.bind
            delivery_session_factory = async_sessionmaker(
                bind=delivery_bind,
                class_=AsyncSession,
                sync_session_class=TenantSession,
                expire_on_commit=False,
                autoflush=False,
            )
        self.delivery_session_factory = delivery_session_factory

    async def list_connections(self) -> list[PlatformOfficialWhatsAppConnectionRead]:
        result = await self.db.execute(
            select(PlatformOfficialWhatsAppConnection).order_by(
                PlatformOfficialWhatsAppConnection.created_at.asc()
            )
        )
        existing = {item.role: item for item in result.scalars().all()}
        return [self._to_read(existing.get(role), role) for role in self.ROLE_ORDER]

    async def connect_manual(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        payload: PlatformOfficialWhatsAppConnectManual,
        user_id: UUID,
    ) -> PlatformOfficialWhatsAppConnectionRead:
        _require_external_delivery("Conexão manual do WhatsApp oficial")
        try:
            async with meta_request_scope(self.meta_client):
                details = await self.meta_client.get_phone_number_details(
                    payload.phone_number_id, payload.access_token
                )
                registration_pin = payload.registration_pin or self.generate_registration_pin()
                await self.meta_client.subscribe_app(
                    payload.business_account_id, payload.access_token
                )
                await self.meta_client.register_phone_number(
                    payload.phone_number_id,
                    payload.access_token,
                    registration_pin,
                )
        except WhatsAppProviderError as exc:
            raise exc.to_http_exception() from exc

        connection = await self._get_or_create(role)
        connection.connection_type = ConnectionType.OFFICIAL_MANUAL
        connection.transport_mode = "official_api"
        connection.coexistence_sync_status = "not_applicable"
        connection.coexistence_contacts_sync_request_id = None
        connection.coexistence_history_sync_request_id = None
        connection.coexistence_sync_requested_at = None
        connection.is_active = payload.is_active
        connection.phone_number_id = payload.phone_number_id
        connection.business_account_id = payload.business_account_id
        connection.waba_id = payload.business_account_id
        connection.display_phone_number = details.get("display_phone_number")
        connection.phone_number = (
            normalize_phone_number(details.get("display_phone_number")) or connection.phone_number
        )
        connection.display_name = (
            payload.display_name or details.get("verified_name") or connection.display_name
        )
        connection.access_token_encrypted = self.cipher.encrypt(payload.access_token)
        connection.business_token_encrypted = None
        connection.token_expires_at = None
        connection.registration_pin_encrypted = self.cipher.encrypt(registration_pin)
        connection.webhook_registered = True
        connection.status = WaConnectionStatus.connected
        connection.connected_at = datetime.now(UTC)
        connection.disconnected_at = None
        connection.last_error_code = None
        connection.last_error_message = None
        connection.updated_by_user_id = user_id
        await self._save(connection)
        return self._to_read(connection, role)

    async def complete_embedded_signup(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        payload: PlatformOfficialWhatsAppEmbeddedCallback,
        user_id: UUID,
    ) -> PlatformOfficialWhatsAppConnectionRead:
        _require_external_delivery("Embedded Signup do WhatsApp oficial")
        try:
            async with meta_request_scope(self.meta_client):
                token_grant = await self.meta_client.exchange_code_for_business_token(
                    payload.code
                )
                if isinstance(token_grant, MetaBusinessTokenGrant):
                    business_token = token_grant.access_token
                    token_expires_at = token_grant.expires_at
                else:  # Compatibility for injected test doubles from the former string contract.
                    business_token = str(token_grant)
                    token_expires_at = None
                waba_id = payload.waba_id or await self.meta_client.resolve_waba_id_from_token(
                    business_token
                )
                if not waba_id:
                    raise WhatsAppProviderError(
                        code="WA-013",
                        message="Não foi possível descobrir o WABA ID desta autorização da Meta.",
                        status_code=422,
                    )

                phone_number_id = payload.phone_number_id
                if not phone_number_id:
                    phone_numbers = await self.meta_client.get_waba_phone_numbers(
                        waba_id, business_token
                    )
                    if not phone_numbers:
                        raise WhatsAppProviderError(
                            code="WA-014",
                            message=(
                                "Nenhum número de WhatsApp Business foi encontrado para este WABA."
                            ),
                            status_code=422,
                        )
                    phone_number_id = str(phone_numbers[0]["id"])

                await self.meta_client.subscribe_app(waba_id, business_token)
                registration_pin: str | None = None
                if payload.transport_mode != "coexistence":
                    registration_pin = payload.registration_pin or self.generate_registration_pin()
                    await self.meta_client.register_phone_number(
                        phone_number_id, business_token, registration_pin
                    )
                details = await self.meta_client.get_phone_number_details(
                    phone_number_id, business_token
                )
        except WhatsAppProviderError as exc:
            raise exc.to_http_exception() from exc

        connection = await self._get_or_create(role)
        connection.connection_type = ConnectionType.TECH_PROVIDER
        connection.transport_mode = payload.transport_mode
        connection.is_active = payload.is_active
        connection.phone_number_id = phone_number_id
        connection.business_account_id = waba_id
        connection.waba_id = waba_id
        connection.display_phone_number = details.get("display_phone_number")
        connection.phone_number = (
            normalize_phone_number(details.get("display_phone_number")) or connection.phone_number
        )
        connection.display_name = details.get("verified_name") or connection.display_name
        connection.access_token_encrypted = self.cipher.encrypt(business_token)
        connection.business_token_encrypted = self.cipher.encrypt(business_token)
        connection.token_expires_at = token_expires_at
        connection.registration_pin_encrypted = (
            self.cipher.encrypt(registration_pin) if registration_pin else None
        )
        connection.webhook_registered = True
        connection.status = WaConnectionStatus.connected
        connection.connected_at = datetime.now(UTC)
        connection.disconnected_at = None
        connection.last_error_code = None
        connection.last_error_message = None
        connection.updated_by_user_id = user_id
        if payload.transport_mode == "coexistence":
            contacts_request_id: str | None = None
            history_request_id: str | None = None
            try:
                async with meta_request_scope(self.meta_client):
                    contacts_request_id = await self.meta_client.request_business_app_sync(
                        phone_number_id,
                        business_token,
                        sync_type="smb_app_state_sync",
                    )
                    history_request_id = await self.meta_client.request_business_app_sync(
                        phone_number_id,
                        business_token,
                        sync_type="history",
                    )
                connection.coexistence_sync_status = "requested"
            except WhatsAppProviderError as sync_exc:
                connection.coexistence_sync_status = (
                    "partial" if contacts_request_id or history_request_id else "failed"
                )
                connection.last_error_code = sync_exc.code
                connection.last_error_message = sync_exc.message
            connection.coexistence_contacts_sync_request_id = contacts_request_id
            connection.coexistence_history_sync_request_id = history_request_id
            connection.coexistence_sync_requested_at = datetime.now(UTC)
        else:
            connection.coexistence_sync_status = "not_applicable"
            connection.coexistence_contacts_sync_request_id = None
            connection.coexistence_history_sync_request_id = None
            connection.coexistence_sync_requested_at = None
        await self._save(connection)
        return self._to_read(connection, role)

    async def set_active(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        payload: PlatformOfficialWhatsAppActiveUpdate,
        user_id: UUID,
    ) -> PlatformOfficialWhatsAppConnectionRead:
        connection = await self._require_connection(role)
        connection.is_active = payload.is_active
        connection.updated_by_user_id = user_id
        await self._save(connection)
        return self._to_read(connection, role)

    async def disconnect(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        user_id: UUID,
    ) -> PlatformOfficialWhatsAppConnectionRead:
        connection = await self._require_connection(role)
        connection.is_active = False
        connection.status = WaConnectionStatus.disconnected
        connection.webhook_registered = False
        connection.access_token_encrypted = None
        connection.business_token_encrypted = None
        connection.token_expires_at = None
        connection.registration_pin_encrypted = None
        connection.disconnected_at = datetime.now(UTC)
        connection.last_error_code = None
        connection.last_error_message = None
        connection.updated_by_user_id = user_id
        await self._save(connection)
        return self._to_read(connection, role)

    async def send_test_message(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        to: str,
        body: str,
        idempotency_key: str,
        requested_by_user_id: UUID | None,
    ) -> PlatformOfficialWhatsAppTestResult:
        _require_external_delivery("Envio de teste pelo WhatsApp oficial")
        success = await self.send_text(
            role=role,
            to=to,
            body=body,
            idempotency_key=idempotency_key,
            requested_by_user_id=requested_by_user_id,
            source_type="test_send",
            source_id=role.value,
        )
        return PlatformOfficialWhatsAppTestResult(
            success=success,
            message="Mensagem de teste enviada com sucesso."
            if success
            else "Canal oficial indisponível ou sem credenciais válidas.",
        )

    async def send_text(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        to: str,
        body: str,
        idempotency_key: str,
        requested_by_user_id: UUID | None = None,
        source_type: str = "system_alert",
        source_id: str | None = None,
    ) -> bool:
        success, _external_id = await self.send_text_result(
            role=role,
            to=to,
            body=body,
            idempotency_key=idempotency_key,
            requested_by_user_id=requested_by_user_id,
            source_type=source_type,
            source_id=source_id,
        )
        return success

    async def send_text_result(
        self,
        *,
        role: PlatformOfficialWhatsAppRole,
        to: str,
        body: str,
        idempotency_key: str,
        requested_by_user_id: UUID | None = None,
        source_type: str = "platform_inbox",
        source_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Persist and dispatch without owning the caller's transaction.

        The delivery session is deliberately separate from ``self.db``.  Its
        immutable request contract is committed before Meta is called, while
        unrelated pending changes in the endpoint/command session remain under
        that caller's commit or rollback boundary.
        """
        _require_external_delivery("Envio pelo WhatsApp oficial")
        async with self.delivery_session_factory() as delivery_db:
            delivery_service = PlatformOfficialWhatsAppService(
                delivery_db,
                cipher=self.cipher,
                meta_client=self.meta_client,
                delivery_session_factory=self.delivery_session_factory,
            )
            connection = await delivery_service.get_connection(
                role,
                connected_only=True,
                active_only=True,
            )
            if not connection:
                return False, None
            delivery = await delivery_service._prepare_text_delivery(
                connection=connection,
                role=role,
                to=to,
                body=body,
                idempotency_key=idempotency_key,
                requested_by_user_id=requested_by_user_id,
                source_type=source_type,
                source_id=source_id,
            )
            return await delivery_service.dispatch_delivery(delivery.id)

    async def dispatch_delivery(
        self,
        delivery_id: UUID,
    ) -> tuple[bool, str | None]:
        """Dispatch one previously committed outbox row exactly once."""
        delivery = await self.db.scalar(
            select(PlatformOfficialWhatsAppDelivery)
            .where(PlatformOfficialWhatsAppDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None:
            return False, None
        if delivery.state is PlatformOfficialWhatsAppDeliveryState.sent:
            return True, delivery.external_message_id
        if delivery.state is PlatformOfficialWhatsAppDeliveryState.failed:
            return False, None
        if delivery.state in {
            PlatformOfficialWhatsAppDeliveryState.provider_call_started,
            PlatformOfficialWhatsAppDeliveryState.delivery_uncertain,
        }:
            self._raise_delivery_uncertain(delivery.id)

        # Recovery never bypasses the global external-delivery kill switch.
        # A prepared row remains replayable when the gate is closed because no
        # provider call has started yet.
        _require_external_delivery("Envio pelo WhatsApp oficial")

        connection = await self.db.get(
            PlatformOfficialWhatsAppConnection,
            delivery.connection_id,
        )
        if (
            connection is None
            or not connection.is_active
            or connection.status is not WaConnectionStatus.connected
            or not connection.phone_number_id
        ):
            await self._settle_delivery(
                delivery,
                state=PlatformOfficialWhatsAppDeliveryState.failed,
                error_code="PLATFORM-WA-CONNECTION-UNAVAILABLE",
                error_type="ConnectionUnavailable",
            )
            return False, None

        try:
            plaintext = self.cipher.decrypt_strict(delivery.payload_ciphertext)
            payload = json.loads(plaintext or "")
            destination = str(payload["to"])
            body = str(payload["body"])
            observed_digest = self._payload_digest(
                connection_id=delivery.connection_id,
                role=delivery.role,
                destination=destination,
                body=body,
                requested_by_user_id=delivery.requested_by_user_id,
                source_type=delivery.source_type,
                source_id=delivery.source_id,
            )
            if not hmac.compare_digest(delivery.payload_digest, observed_digest):
                await self._settle_delivery(
                    delivery,
                    state=PlatformOfficialWhatsAppDeliveryState.failed,
                    error_code="PLATFORM-WA-PAYLOAD-INTEGRITY",
                    error_type="PayloadDigestMismatch",
                )
                return False, None
            token = self._get_access_token(connection)
            if not token:
                raise WhatsAppSecretDecryptionError(
                    "Credencial do canal oficial não pôde ser decifrada."
                )
        except (
            WhatsAppSecretDecryptionError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            await self._settle_delivery(
                delivery,
                state=PlatformOfficialWhatsAppDeliveryState.failed,
                error_code="PLATFORM-WA-PAYLOAD-UNAVAILABLE",
                error_type=safe_error_type(exc),
            )
            return False, None

        delivery.state = PlatformOfficialWhatsAppDeliveryState.provider_call_started
        delivery.provider_call_started_at = datetime.now(UTC)
        delivery.attempt_count += 1
        await self.db.commit()

        try:
            result = await self.meta_client.send_text_message(
                phone_number_id=connection.phone_number_id,
                access_token=token,
                to=destination,
                body=body,
            )
            messages = result.get("messages") if isinstance(result, dict) else None
            external_id = str(messages[0].get("id")) if messages and messages[0].get("id") else None
            if external_id is None:
                raise WhatsAppProviderError(
                    code="PLATFORM-WA-DELIVERY-UNCERTAIN",
                    message="A Meta não retornou o identificador da mensagem.",
                    status_code=502,
                    request_started=True,
                    delivery_outcome_known=False,
                )
        except WhatsAppProviderError as exc:
            uncertain = exc.delivery_uncertain
            connection.last_error_code = exc.code
            connection.last_error_message = exc.message[:1000]
            await self._settle_delivery(
                delivery,
                state=(
                    PlatformOfficialWhatsAppDeliveryState.delivery_uncertain
                    if uncertain
                    else PlatformOfficialWhatsAppDeliveryState.failed
                ),
                error_code=exc.code,
                error_type=safe_error_type(exc),
            )
            if uncertain:
                self._raise_delivery_uncertain(delivery.id)
            return False, None
        except Exception as exc:
            await self._settle_delivery(
                delivery,
                state=PlatformOfficialWhatsAppDeliveryState.delivery_uncertain,
                error_code="PLATFORM-WA-DELIVERY-UNCERTAIN",
                error_type=safe_error_type(exc),
            )
            self._raise_delivery_uncertain(delivery.id)

        connection.last_error_code = None
        connection.last_error_message = None
        await self._settle_delivery(
            delivery,
            state=PlatformOfficialWhatsAppDeliveryState.sent,
            external_message_id=external_id,
        )
        return True, external_id

    async def dispatch_prepared(self, *, limit: int = 50) -> int:
        """Recover committed intents whose request died before provider dispatch."""
        ids = list(
            (
                await self.db.scalars(
                    select(PlatformOfficialWhatsAppDelivery.id)
                    .where(
                        PlatformOfficialWhatsAppDelivery.state
                        == PlatformOfficialWhatsAppDeliveryState.prepared
                    )
                    .order_by(
                        PlatformOfficialWhatsAppDelivery.created_at,
                        PlatformOfficialWhatsAppDelivery.id,
                    )
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
        )
        sent = 0
        for delivery_id in ids:
            try:
                delivered, _external_id = await self.dispatch_delivery(delivery_id)
                sent += int(delivered)
            except HTTPException as exc:
                if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
                    break
                if exc.status_code != status.HTTP_409_CONFLICT:
                    raise
        return sent

    async def mark_stale_started_uncertain(
        self,
        *,
        older_than: timedelta = timedelta(minutes=5),
    ) -> int:
        """Fence crashed provider calls without ever retrying their side effect."""
        cutoff = datetime.now(UTC) - older_than
        result = await self.db.execute(
            update(PlatformOfficialWhatsAppDelivery)
            .where(
                PlatformOfficialWhatsAppDelivery.state
                == PlatformOfficialWhatsAppDeliveryState.provider_call_started,
                PlatformOfficialWhatsAppDelivery.provider_call_started_at < cutoff,
            )
            .values(
                state=PlatformOfficialWhatsAppDeliveryState.delivery_uncertain,
                delivery_uncertain_at=datetime.now(UTC),
                payload_ciphertext=None,
                last_error_code="PLATFORM-WA-DISPATCH-LEASE-EXPIRED",
                last_error_type="DispatchLeaseExpired",
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    async def _prepare_text_delivery(
        self,
        *,
        connection: PlatformOfficialWhatsAppConnection,
        role: PlatformOfficialWhatsAppRole,
        to: str,
        body: str,
        idempotency_key: str,
        requested_by_user_id: UUID | None,
        source_type: str,
        source_id: str | None,
    ) -> PlatformOfficialWhatsAppDelivery:
        normalized_key = idempotency_key.strip()
        if not 8 <= len(normalized_key) <= 160:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PLATFORM-WA-IDEMPOTENCY-INVALID",
                    "message": "Idempotency-Key deve conter entre 8 e 160 caracteres.",
                },
            )
        destination = normalize_phone_number(to)
        normalized_body = body.strip()
        if not destination or not 1 <= len(normalized_body) <= 4096:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PLATFORM-WA-PAYLOAD-INVALID",
                    "message": "Destino ou corpo da mensagem oficial é inválido.",
                },
            )
        normalized_source_type = source_type.strip()
        normalized_source_id = source_id.strip() if source_id else None
        if not normalized_source_type or len(normalized_source_type) > 40:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "PLATFORM-WA-SOURCE-INVALID"},
            )
        if normalized_source_id and len(normalized_source_id) > 120:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "PLATFORM-WA-SOURCE-INVALID"},
            )

        payload_digest = self._payload_digest(
            connection_id=connection.id,
            role=role,
            destination=destination,
            body=normalized_body,
            requested_by_user_id=requested_by_user_id,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
        )
        key_hash = hashlib.sha256(
            f"platform-official-whatsapp-v1:{role.value}:{normalized_key}".encode()
        ).hexdigest()
        existing = await self.db.scalar(
            select(PlatformOfficialWhatsAppDelivery).where(
                PlatformOfficialWhatsAppDelivery.idempotency_key_hash == key_hash
            )
        )
        if existing is not None:
            self._validate_delivery_replay(existing, payload_digest)
            return existing

        encrypted_payload = self.cipher.encrypt(
            json.dumps(
                {"to": destination, "body": normalized_body},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if encrypted_payload is None:
            raise RuntimeError("Falha ao cifrar payload do outbox oficial.")
        delivery = PlatformOfficialWhatsAppDelivery(
            connection_id=connection.id,
            role=role,
            requested_by_user_id=requested_by_user_id,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
            idempotency_key_hash=key_hash,
            payload_digest=payload_digest,
            payload_ciphertext=encrypted_payload,
            destination_last4=(destination[-4:] if len(destination) >= 4 else None),
            body_length=len(normalized_body),
            state=PlatformOfficialWhatsAppDeliveryState.prepared,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(delivery)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(PlatformOfficialWhatsAppDelivery).where(
                    PlatformOfficialWhatsAppDelivery.idempotency_key_hash == key_hash
                )
            )
            if existing is None:
                raise
            self._validate_delivery_replay(existing, payload_digest)
            return existing
        # This commit is the external-effect boundary. No Meta client can be
        # reached before the immutable operation contract exists durably.
        await self.db.commit()
        return delivery

    @staticmethod
    def _payload_digest(
        *,
        connection_id: UUID,
        role: PlatformOfficialWhatsAppRole,
        destination: str,
        body: str,
        requested_by_user_id: UUID | None,
        source_type: str,
        source_id: str | None,
    ) -> str:
        """Bind encrypted contents to the immutable request metadata."""
        canonical_payload = json.dumps(
            {
                "version": 1,
                "action": _DELIVERY_ACTION,
                "connectionId": str(connection_id),
                "role": role.value,
                "to": destination,
                "body": body,
                "requestedByUserId": (str(requested_by_user_id) if requested_by_user_id else None),
                "sourceType": source_type,
                "sourceId": source_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical_payload.encode()).hexdigest()

    @staticmethod
    def _validate_delivery_replay(
        delivery: PlatformOfficialWhatsAppDelivery,
        payload_digest: str,
    ) -> None:
        if delivery.payload_digest != payload_digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PLATFORM-WA-IDEMPOTENCY-CONFLICT",
                    "message": "Idempotency-Key já pertence a outra entrega oficial.",
                },
            )

    async def _settle_delivery(
        self,
        delivery: PlatformOfficialWhatsAppDelivery,
        *,
        state: PlatformOfficialWhatsAppDeliveryState,
        external_message_id: str | None = None,
        error_code: str | None = None,
        error_type: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        delivery.state = state
        delivery.external_message_id = external_message_id
        delivery.last_error_code = error_code
        delivery.last_error_type = error_type
        if state in _TERMINAL_DELIVERY_STATES:
            delivery.payload_ciphertext = None
        if state is PlatformOfficialWhatsAppDeliveryState.sent:
            delivery.sent_at = now
        elif state is PlatformOfficialWhatsAppDeliveryState.delivery_uncertain:
            delivery.delivery_uncertain_at = now
        elif state is PlatformOfficialWhatsAppDeliveryState.failed:
            delivery.failed_at = now
        await self.db.commit()

    @staticmethod
    def _raise_delivery_uncertain(delivery_id: UUID) -> None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PLATFORM-WA-DELIVERY-UNCERTAIN",
                "message": (
                    "A Meta pode ter aceitado a mensagem. Reconcilie a operação "
                    f"{delivery_id} antes de qualquer novo envio."
                ),
                "operationId": str(delivery_id),
            },
        )

    async def get_connection(
        self,
        role: PlatformOfficialWhatsAppRole,
        *,
        connected_only: bool = False,
        active_only: bool = False,
    ) -> PlatformOfficialWhatsAppConnection | None:
        query = select(PlatformOfficialWhatsAppConnection).where(
            PlatformOfficialWhatsAppConnection.role == role
        )
        if connected_only:
            query = query.where(
                PlatformOfficialWhatsAppConnection.status == WaConnectionStatus.connected
            )
        if active_only:
            query = query.where(PlatformOfficialWhatsAppConnection.is_active.is_(True))
        return await self.db.scalar(query.limit(1))

    async def mark_connection_error(
        self,
        connection: PlatformOfficialWhatsAppConnection,
        code: str,
        message: str,
        *,
        commit: bool = True,
    ) -> None:
        connection.status = WaConnectionStatus.error
        connection.last_error_code = code
        connection.last_error_message = message
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()

    def generate_registration_pin(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    async def _get_or_create(
        self, role: PlatformOfficialWhatsAppRole
    ) -> PlatformOfficialWhatsAppConnection:
        existing = await self.get_connection(role)
        if existing:
            return existing
        connection = PlatformOfficialWhatsAppConnection(role=role)
        self.db.add(connection)
        return connection

    async def _require_connection(
        self, role: PlatformOfficialWhatsAppRole
    ) -> PlatformOfficialWhatsAppConnection:
        connection = await self.get_connection(role)
        if not connection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Canal oficial ainda não foi configurado.",
            )
        return connection

    async def _save(self, connection: PlatformOfficialWhatsAppConnection) -> None:
        # O commit pertence à dependência de request para manter configuração e
        # auditoria canônica na mesma transação. Efeitos já aceitos pela Meta
        # são evidência externa separada e não são apresentados como rollback.
        await self.db.flush()
        await self.db.refresh(connection)

    def _get_access_token(self, connection: PlatformOfficialWhatsAppConnection) -> str | None:
        encrypted = (
            connection.business_token_encrypted
            if connection.connection_type == ConnectionType.TECH_PROVIDER
            else connection.access_token_encrypted
        )
        return self.cipher.decrypt(encrypted) or self.cipher.decrypt(
            connection.access_token_encrypted
        )

    def _to_read(
        self,
        connection: PlatformOfficialWhatsAppConnection | None,
        role: PlatformOfficialWhatsAppRole,
    ) -> PlatformOfficialWhatsAppConnectionRead:
        if not connection:
            return PlatformOfficialWhatsAppConnectionRead(
                role=role,
                status=WaConnectionStatus.disconnected,
                isActive=False,
            )
        token_lifecycle_state, token_expires_in_days = self._token_lifecycle(connection)
        return PlatformOfficialWhatsAppConnectionRead(
            id=connection.id,
            role=role,
            connectionType=connection.connection_type,
            isActive=connection.is_active,
            status=connection.status,
            phoneNumberId=connection.phone_number_id,
            phoneNumber=connection.phone_number,
            displayPhoneNumber=connection.display_phone_number,
            displayName=connection.display_name,
            businessAccountId=connection.business_account_id,
            wabaId=connection.waba_id,
            webhookRegistered=connection.webhook_registered,
            connectedAt=connection.connected_at,
            disconnectedAt=connection.disconnected_at,
            updatedAt=connection.updated_at,
            lastError=build_last_error(connection.last_error_code, connection.last_error_message),
            transportMode=connection.transport_mode,
            coexistenceSyncStatus=connection.coexistence_sync_status,
            coexistenceContactsSyncRequestId=connection.coexistence_contacts_sync_request_id,
            coexistenceHistorySyncRequestId=connection.coexistence_history_sync_request_id,
            coexistenceSyncRequestedAt=connection.coexistence_sync_requested_at,
            tokenExpiresAt=connection.token_expires_at,
            tokenLifecycleState=token_lifecycle_state,
            tokenExpiresInDays=token_expires_in_days,
        )

    @staticmethod
    def _token_lifecycle(
        connection: PlatformOfficialWhatsAppConnection,
    ) -> tuple[str, int | None]:
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
