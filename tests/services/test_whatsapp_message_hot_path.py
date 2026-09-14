from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.whatsapp import (
    SendDocumentRequest,
    SendMessageRequest,
    SendStructuredMessageRequest,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.message_service import WhatsAppMessageService
from app.services.whatsapp.provider_media_receipt_service import ProviderMediaReceiptService
from app.services.whatsapp.types import WhatsAppSendResult


pytestmark = pytest.mark.usefixtures("active_subscription")


class _ConnectionService:
    def __init__(self, connection: WhatsAppConnection) -> None:
        self.connection = connection
        self.errors: list[tuple[str, str]] = []

    async def get_connection(self, _connection_id, _workspace_id):
        return self.connection

    async def get_default_connection(self, _workspace_id, connected_only=False):
        return self.connection

    async def mark_connection_error(self, _connection, code: str, message: str) -> None:
        self.errors.append((code, message))
        self.connection.status = WaConnectionStatus.error


class _Factory:
    def __init__(self, strategy) -> None:
        self.strategy = strategy
        self.build_calls = 0

    def build(self, _connection):
        self.build_calls += 1
        return self.strategy


class _ImmediateStrategy:
    def __init__(self) -> None:
        self.text_calls = 0
        self.document_calls = 0
        self.document_urls: list[str] = []

    async def send_text(self, _connection, _phone, _body):
        self.text_calls += 1
        return WhatsAppSendResult(wamid=f"wamid-text-{self.text_calls}")

    async def send_document(
        self,
        _connection,
        _phone,
        _url,
        _filename,
        _caption,
    ):
        self.document_calls += 1
        self.document_urls.append(_url)
        return WhatsAppSendResult(wamid=f"wamid-document-{self.document_calls}")


class _StructuredStrategy(_ImmediateStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.structured_calls: list[dict] = []

    async def send_structured(
        self,
        _connection,
        _phone,
        payload,
        *,
        opaque_callback_data=None,
        verified_provider_media_receipt_ids=None,
    ):
        self.structured_calls.append(
            {
                "payload": payload,
                "opaque_callback_data": opaque_callback_data,
                "verified_receipts": verified_provider_media_receipt_ids,
            }
        )
        return WhatsAppSendResult(wamid=f"wamid-structured-{len(self.structured_calls)}")


class _BlockingStrategy(_ImmediateStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(self, _connection, _phone, _body):
        self.text_calls += 1
        self.started.set()
        await self.release.wait()
        return WhatsAppSendResult(wamid="wamid-concurrent-winner")


class _ForbiddenStrategy:
    async def send_text(self, *_args, **_kwargs):
        raise AssertionError("an idempotent replay must not call the provider")

    async def send_document(self, *_args, **_kwargs):
        raise AssertionError("an idempotent replay must not call the provider")


class _ProcessCrash(BaseException):
    pass


class _CrashStrategy:
    async def send_text(self, *_args, **_kwargs):
        raise _ProcessCrash("process stopped after the durable request checkpoint")


class _KnownFailureStrategy:
    def __init__(self) -> None:
        self.calls = 0

    async def send_text(self, *_args, **_kwargs):
        self.calls += 1
        raise WhatsAppProviderError(
            code="WA-002",
            message="Payload rejeitado pelo provedor.",
            status_code=400,
            request_started=True,
            delivery_outcome_known=True,
        )


class _ConnectionAuthFailureStrategy:
    def __init__(self) -> None:
        self.calls = 0

    async def send_text(self, *_args, **_kwargs):
        self.calls += 1
        raise WhatsAppProviderError(
            code="WA-002",
            message="Credencial da conexão rejeitada pelo provedor.",
            status_code=401,
            request_started=True,
            delivery_outcome_known=True,
            connection_fault=True,
        )


class _UncertainFailureStrategy:
    def __init__(self) -> None:
        self.calls = 0

    async def send_text(self, *_args, **_kwargs):
        self.calls += 1
        raise WhatsAppProviderError(
            code="WA-DELIVERY-UNCERTAIN",
            message="O provedor não confirmou se aceitou a mensagem.",
            status_code=502,
            request_started=True,
            delivery_outcome_known=False,
        )


async def _connection_and_contact(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-hot-path",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999999999",
        name="Contato Hot Path",
    )
    db_session.add(contact)
    await db_session.commit()
    return connection, contact


def _service(db_session, connection, strategy) -> WhatsAppMessageService:
    return WhatsAppMessageService(
        db_session,
        connection_service=_ConnectionService(connection),
        strategy_factory=_Factory(strategy),
    )


def _structured_service(db_session, strategy) -> WhatsAppMessageService:
    return WhatsAppMessageService(
        db_session,
        connection_service=WhatsAppConnectionService(db_session),
        strategy_factory=_Factory(strategy),
    )


async def _provider_media_receipt(
    db_session,
    connection,
    *,
    media_id: str,
    media_type: str,
    expires_at=None,
):
    connection_service = WhatsAppConnectionService(db_session)
    await connection_service.ensure_omnichannel_projection(connection)
    receipt_service = ProviderMediaReceiptService(db_session)
    native = await receipt_service.resolve_native_connection(
        workspace_id=connection.workspace_id,
        legacy_connection_id=connection.id,
        provider="meta_cloud",
    )
    receipt = await receipt_service.record_verified_upload(
        workspace_id=connection.workspace_id,
        connection_id=native.id,
        provider="meta_cloud",
        provider_media_id=media_id,
        media_type=media_type,
        source_sha256="c" * 64,
        source_size_bytes=128,
        source_content_type=("application/pdf" if media_type == "document" else "image/jpeg"),
        provider_receipt={"id": media_id},
        expires_at=expires_at,
    )
    await db_session.commit()
    return receipt


@pytest.mark.asyncio
async def test_inbox_is_one_windowed_query_with_filter_order_and_pagination(
    db_session,
    auth_context,
):
    connection, _ = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    assignee = User(
        name="Atendente Inbox",
        email="inbox-query@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AI",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(assignee)
    await db_session.flush()
    base = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    contacts = [
        WhatsAppContact(
            workspace_id=auth_context.workspace_id,
            whatsapp_connection_id=connection.id,
            phone="5511000000001",
            name="Ana Mais Recente",
            assigned_user_id=assignee.id,
            updated_at=base + timedelta(minutes=3),
        ),
        WhatsAppContact(
            workspace_id=auth_context.workspace_id,
            whatsapp_connection_id=connection.id,
            phone="5511000000002",
            name="Bruno",
            updated_at=base + timedelta(minutes=2),
        ),
        WhatsAppContact(
            workspace_id=auth_context.workspace_id,
            whatsapp_connection_id=connection.id,
            phone="5511000000003",
            name="Ana Paginada",
            updated_at=base + timedelta(minutes=1),
        ),
    ]
    db_session.add_all(contacts)
    await db_session.flush()
    db_session.add_all(
        [
            WhatsAppMessage(
                contact_id=contacts[0].id,
                whatsapp_connection_id=connection.id,
                direction=WaMessageDirection.inbound,
                status=WaMessageStatus.delivered,
                body="primeira não lida",
                timestamp=base,
            ),
            WhatsAppMessage(
                contact_id=contacts[0].id,
                whatsapp_connection_id=connection.id,
                direction=WaMessageDirection.inbound,
                status=WaMessageStatus.read,
                body="já lida",
                timestamp=base + timedelta(seconds=1),
            ),
            WhatsAppMessage(
                contact_id=contacts[0].id,
                whatsapp_connection_id=connection.id,
                direction=WaMessageDirection.inbound,
                status=WaMessageStatus.delivered,
                body="última da Ana",
                timestamp=base + timedelta(seconds=2),
            ),
            WhatsAppMessage(
                contact_id=contacts[2].id,
                whatsapp_connection_id=connection.id,
                direction=WaMessageDirection.outbound,
                status=WaMessageStatus.sent,
                body="última paginada",
                timestamp=base + timedelta(seconds=3),
            ),
        ]
    )
    await db_session.commit()

    selects: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    sync_engine = db_session.bind.sync_engine
    event.listen(sync_engine, "before_cursor_execute", count_selects)
    try:
        result = await _service(
            db_session,
            connection,
            _ImmediateStrategy(),
        ).list_contacts(
            auth_context.workspace_id,
            search="ana",
            skip=0,
            limit=2,
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", count_selects)

    assert len(selects) == 1
    assert [item.name for item in result] == ["Ana Mais Recente", "Ana Paginada"]
    assert result[0].assignedUserName == "Atendente Inbox"
    assert result[0].unreadCount == 2
    assert result[0].lastMessage == "última da Ana"
    assert result[1].unreadCount == 0
    assert result[1].lastMessage == "última paginada"

    paginated = await _service(
        db_session,
        connection,
        _ImmediateStrategy(),
    ).list_contacts(
        auth_context.workspace_id,
        search="ana",
        skip=1,
        limit=1,
    )
    assert [item.name for item in paginated] == ["Ana Paginada"]


@pytest.mark.asyncio
async def test_inbox_query_compiles_for_sqlite_and_postgresql():
    class _EmptyRows:
        @staticmethod
        def all():
            return []

    class _CaptureSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return _EmptyRows()

    capture = _CaptureSession()
    service = WhatsAppMessageService(
        capture,
        connection_service=object(),
        strategy_factory=object(),
    )

    assert await service.list_contacts(
        uuid4(),
        search="ana",
        skip=2,
        limit=5,
    ) == []
    assert capture.statement is not None

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        compiled = str(capture.statement.compile(dialect=dialect)).lower()
        assert "row_number()" in compiled
        assert "count(" in compiled
        assert " limit " in compiled


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_row_and_one_provider_call(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _BlockingStrategy()
    first_service = _service(db_session, connection, strategy)
    payload = SendMessageRequest(
        contact_id=contact.id,
        body="Uma única entrega concorrente",
        idempotency_key="concurrent-message-0001",
    )

    first_call = asyncio.create_task(
        first_service.send_message(
            payload,
            auth_context.user_id,
            auth_context.workspace_id,
        )
    )
    await asyncio.wait_for(strategy.started.wait(), timeout=2)

    session_factory = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as second_session:
        second_result = await _service(
            second_session,
            connection,
            strategy,
        ).send_message(
            payload,
            auth_context.user_id,
            auth_context.workspace_id,
        )
        assert second_result.status == WaMessageStatus.delivery_uncertain

    strategy.release.set()
    first_result = await asyncio.wait_for(first_call, timeout=2)

    assert first_result.status == WaMessageStatus.sent
    assert first_result.messageId == second_result.messageId
    assert strategy.text_calls == 1
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 1


@pytest.mark.asyncio
async def test_http_idempotency_header_is_echoed_and_replayed(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _ImmediateStrategy()
    monkeypatch.setattr(
        "app.services.whatsapp.strategy_factory.WhatsAppStrategyFactory.build",
        lambda _self, _connection: strategy,
    )
    headers = {"Idempotency-Key": "http-message-0001"}
    body = {"contactId": str(contact.id), "body": "Envio HTTP idempotente"}

    first = await client.post("/api/v1/whatsapp/messages", json=body, headers=headers)
    replay = await client.post("/api/v1/whatsapp/messages", json=body, headers=headers)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert first.json()["messageId"] == replay.json()["messageId"]
    assert replay.json()["idempotencyKey"] == "http-message-0001"
    assert replay.json()["status"] == WaMessageStatus.sent.value
    assert strategy.text_calls == 1
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 1


@pytest.mark.asyncio
async def test_storage_document_replay_uses_stable_digest_not_reminted_signed_url(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _ImmediateStrategy()
    service = _service(db_session, connection, strategy)
    version_id = uuid4()
    storage_key = "tenants/workspace/objects/proposal/clean"
    common = {
        "sent_by_id": auth_context.user_id,
        "workspace_id": auth_context.workspace_id,
        "storage_object_version_id": version_id,
        "storage_key": storage_key,
    }

    first = await service.send_storage_document(
        SendDocumentRequest(
            contact_id=contact.id,
            url="https://signed.invalid/proposal?signature=first",
            filename="proposta.pdf",
            caption="Segue a proposta.",
            idempotency_key="generated-document-stable-digest",
        ),
        **common,
    )
    replay = await service.send_storage_document(
        SendDocumentRequest(
            contact_id=contact.id,
            url="https://signed.invalid/proposal?signature=reminted",
            filename="proposta.pdf",
            caption="Segue a proposta.",
            idempotency_key="generated-document-stable-digest",
        ),
        **common,
    )

    assert replay.messageId == first.messageId
    assert strategy.document_calls == 1
    assert strategy.document_urls == [
        "https://signed.invalid/proposal?signature=first"
    ]
    message = await db_session.get(WhatsAppMessage, first.messageId)
    assert message is not None
    assert message.media_url == storage_key
    assert "signed.invalid" not in str(message.message_metadata)


@pytest.mark.asyncio
async def test_http_payload_failure_does_not_poison_healthy_connection(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _KnownFailureStrategy()
    monkeypatch.setattr(
        "app.services.whatsapp.strategy_factory.WhatsAppStrategyFactory.build",
        lambda _self, _connection: strategy,
    )
    headers = {"Idempotency-Key": "http-failure-0001"}
    body = {"contactId": str(contact.id), "body": "Falha conhecida"}

    first = await client.post("/api/v1/whatsapp/messages", json=body, headers=headers)
    await db_session.refresh(connection)
    replay = await client.post("/api/v1/whatsapp/messages", json=body, headers=headers)

    assert first.status_code == 400
    assert replay.status_code == 400
    assert first.json()["detail"]["code"] == "WA-002"
    assert replay.json()["detail"]["code"] == "WA-002"
    assert connection.status == WaConnectionStatus.connected
    assert strategy.calls == 1
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 1


@pytest.mark.asyncio
async def test_connection_auth_failure_marks_connection_error_once(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _ConnectionAuthFailureStrategy()
    monkeypatch.setattr(
        "app.services.whatsapp.strategy_factory.WhatsAppStrategyFactory.build",
        lambda _self, _connection: strategy,
    )
    headers = {"Idempotency-Key": "http-auth-failure-0001"}
    body = {"contactId": str(contact.id), "body": "Falha de credencial"}

    first = await client.post("/api/v1/whatsapp/messages", json=body, headers=headers)
    await db_session.refresh(connection)
    replay = await client.post("/api/v1/whatsapp/messages", json=body, headers=headers)

    assert first.status_code == 401
    assert replay.status_code == 401
    assert connection.status == WaConnectionStatus.error
    assert strategy.calls == 1
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 1


@pytest.mark.asyncio
async def test_same_client_key_is_independent_between_workspaces(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: False,
    )
    first_connection, first_contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    second_workspace = Workspace(
        name="Workspace isolado",
        slug="workspace-whatsapp-isolado",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(second_workspace)
    await db_session.flush()
    second_connection = WhatsAppConnection(
        workspace_id=second_workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-hot-path-isolated",
        status=WaConnectionStatus.connected,
    )
    db_session.add(second_connection)
    await db_session.flush()
    second_contact = WhatsAppContact(
        workspace_id=second_workspace.id,
        whatsapp_connection_id=second_connection.id,
        phone="5511888888888",
        name="Contato isolado",
    )
    db_session.add(second_contact)
    await db_session.commit()
    shared_key = "same-visible-key-0001"

    first = await _service(
        db_session,
        first_connection,
        _ForbiddenStrategy(),
    ).send_message(
        SendMessageRequest(
            contact_id=first_contact.id,
            body="Mensagem do primeiro workspace",
            idempotency_key=shared_key,
        ),
        auth_context.user_id,
        auth_context.workspace_id,
    )
    second = await _service(
        db_session,
        second_connection,
        _ForbiddenStrategy(),
    ).send_message(
        SendMessageRequest(
            contact_id=second_contact.id,
            body="Mensagem do segundo workspace",
            idempotency_key=shared_key,
        ),
        auth_context.user_id,
        second_workspace.id,
    )

    rows = list((await db_session.scalars(select(WhatsAppMessage))).all())
    assert first.messageId != second.messageId
    assert len(rows) == 2
    assert rows[0].outbound_idempotency_key != rows[1].outbound_idempotency_key
    assert {row.status for row in rows} == {WaMessageStatus.pending}


@pytest.mark.asyncio
async def test_restart_after_request_checkpoint_never_redispatches(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    payload = SendMessageRequest(
        contact_id=contact.id,
        body="Não pode duplicar após reinício",
        idempotency_key="restart-message-0001",
    )

    with pytest.raises(_ProcessCrash):
        await _service(db_session, connection, _CrashStrategy()).send_message(
            payload,
            auth_context.user_id,
            auth_context.workspace_id,
        )

    stored = await db_session.scalar(select(WhatsAppMessage))
    assert stored is not None
    assert stored.status == WaMessageStatus.delivery_uncertain
    assert stored.outbound_request_started_at is not None
    assert stored.delivery_uncertain_at is not None

    replay_factory = _Factory(_ForbiddenStrategy())
    replay_service = WhatsAppMessageService(
        db_session,
        connection_service=_ConnectionService(connection),
        strategy_factory=replay_factory,
    )
    replay = await replay_service.send_message(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )

    assert replay.messageId == stored.id
    assert replay.status == WaMessageStatus.delivery_uncertain
    assert replay_factory.build_calls == 0
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 1


@pytest.mark.asyncio
async def test_document_replay_and_payload_conflict_are_fenced(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _ImmediateStrategy()
    service = _service(db_session, connection, strategy)
    payload = SendDocumentRequest(
        contact_id=contact.id,
        url="https://local.invalid/proposta.pdf",
        filename="proposta.pdf",
        caption="Sua proposta",
        idempotency_key="document-message-0001",
    )

    first = await service.send_document(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )
    replay = await service.send_document(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )

    assert first.messageId == replay.messageId
    assert replay.status == WaMessageStatus.sent
    assert strategy.document_calls == 1
    stored = await db_session.get(WhatsAppMessage, first.messageId)
    assert stored is not None
    assert stored.media_url.startswith("external-document:sha256:")
    assert "local.invalid" not in stored.media_url
    conversation = await service.get_conversation(contact.id, auth_context.workspace_id)
    assert conversation.messages[-1].mediaUrl is None

    with pytest.raises(HTTPException) as conflict:
        await service.send_document(
            payload.model_copy(update={"caption": "Conteúdo diferente"}),
            auth_context.user_id,
            auth_context.workspace_id,
        )
    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "WA-IDEMPOTENCY-CONFLICT"
    assert strategy.document_calls == 1


@pytest.mark.asyncio
async def test_system_direct_send_uses_durable_message_and_replays_once(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _ImmediateStrategy()
    service = _service(db_session, connection, strategy)

    first = await service.send_text_direct(
        phone=contact.phone,
        body="Resposta automática persistida",
        workspace_id=auth_context.workspace_id,
        idempotency_key="automation-reply:inbound-0001",
    )
    replay = await service.send_text_direct(
        phone=contact.phone,
        body="Resposta automática persistida",
        workspace_id=auth_context.workspace_id,
        idempotency_key="automation-reply:inbound-0001",
    )

    assert first is True
    assert replay is True
    assert strategy.text_calls == 1
    stored = (
        await db_session.scalars(
            select(WhatsAppMessage).where(
                WhatsAppMessage.contact_id == contact.id,
                WhatsAppMessage.direction == WaMessageDirection.outbound,
            )
        )
    ).all()
    assert len(stored) == 1
    assert stored[0].status == WaMessageStatus.sent
    assert stored[0].sent_by_id is None


@pytest.mark.asyncio
async def test_outbound_service_rejects_missing_idempotency_before_provider_resolution(
    db_session,
    auth_context,
):
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    factory = _Factory(_ForbiddenStrategy())
    service = WhatsAppMessageService(
        db_session,
        connection_service=_ConnectionService(connection),
        strategy_factory=factory,
    )

    with pytest.raises(HTTPException) as captured:
        await service.send_message(
            SendMessageRequest(contact_id=contact.id, body="Sem chave"),
            auth_context.user_id,
            auth_context.workspace_id,
        )

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == "WA-IDEMPOTENCY-REQUIRED"
    assert factory.build_calls == 0


@pytest.mark.asyncio
async def test_known_provider_failure_is_failed_and_not_retried(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _KnownFailureStrategy()
    service = _service(db_session, connection, strategy)
    payload = SendMessageRequest(
        contact_id=contact.id,
        body="Payload inválido",
        idempotency_key="known-failure-0001",
    )

    for _ in range(2):
        with pytest.raises(HTTPException) as failure:
            await service.send_message(
                payload,
                auth_context.user_id,
                auth_context.workspace_id,
            )
        assert failure.value.status_code == 400
        assert failure.value.detail["code"] == "WA-002"

    stored = await db_session.scalar(select(WhatsAppMessage))
    assert stored is not None
    assert stored.status == WaMessageStatus.failed
    assert stored.delivery_uncertain_at is None
    assert stored.outbound_error["deliveryOutcomeKnown"] is True
    assert strategy.calls == 1


@pytest.mark.asyncio
async def test_ambiguous_provider_outcome_is_persisted_and_never_redispatched(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    strategy = _UncertainFailureStrategy()
    service = _service(db_session, connection, strategy)
    payload = SendMessageRequest(
        contact_id=contact.id,
        body="Resultado ambíguo não pode ser reenviado",
        idempotency_key="ambiguous-message-0001",
    )

    with pytest.raises(HTTPException) as first_attempt:
        await service.send_message(
            payload,
            auth_context.user_id,
            auth_context.workspace_id,
        )
    replay = await service.send_message(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )

    stored = await db_session.scalar(select(WhatsAppMessage))
    assert first_attempt.value.status_code == 502
    assert stored is not None
    assert replay.messageId == stored.id
    assert replay.status == WaMessageStatus.delivery_uncertain
    assert stored.outbound_request_started_at is not None
    assert stored.delivery_uncertain_at is not None
    assert stored.outbound_error["requestStarted"] is True
    assert stored.outbound_error["deliveryOutcomeKnown"] is False
    assert strategy.calls == 1


@pytest.mark.asyncio
async def test_immediate_structured_media_requires_and_forwards_verified_receipt(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    receipt = await _provider_media_receipt(
        db_session,
        connection,
        media_id="meta-hot-path-image",
        media_type="image",
    )
    strategy = _StructuredStrategy()

    result = await _structured_service(db_session, strategy).send_structured(
        SendStructuredMessageRequest(
            contact_id=contact.id,
            connection_id=connection.id,
            message_payload={
                "kind": "media",
                "type": "image",
                "providerMediaId": "meta-hot-path-image",
                "providerMediaReceiptId": str(receipt.id),
            },
            idempotency_key="structured-media-valid-0001",
        ),
        auth_context.user_id,
        auth_context.workspace_id,
    )

    assert result.status == WaMessageStatus.sent
    assert strategy.structured_calls[0]["verified_receipts"] == {str(receipt.id)}


@pytest.mark.asyncio
async def test_immediate_template_media_forwards_nested_verified_receipt(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    receipt = await _provider_media_receipt(
        db_session,
        connection,
        media_id="meta-hot-path-document",
        media_type="document",
    )
    strategy = _StructuredStrategy()
    payload = SendStructuredMessageRequest(
        contact_id=contact.id,
        connection_id=connection.id,
        message_payload={
            "kind": "template",
            "name": "proposta_juridica",
            "language": "pt_BR",
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "document",
                            "document": {
                                "id": "meta-hot-path-document",
                                "filename": "proposta.pdf",
                                "providerMediaReceiptId": str(receipt.id),
                            },
                        }
                    ],
                }
            ],
        },
        idempotency_key="structured-template-valid-0001",
    )

    await _structured_service(db_session, strategy).send_structured(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )

    assert strategy.structured_calls[0]["verified_receipts"] == {str(receipt.id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload_kind", ["media", "template"])
async def test_invalid_immediate_provider_media_has_no_message_or_provider_checkpoint(
    db_session,
    auth_context,
    monkeypatch,
    payload_kind,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    missing_receipt_id = uuid4()
    media = {
        "kind": "media",
        "type": "image",
        "providerMediaId": "meta-without-proof",
        "providerMediaReceiptId": str(missing_receipt_id),
    }
    template = {
        "kind": "template",
        "name": "proposta_juridica",
        "language": "pt_BR",
        "components": [
            {
                "type": "header",
                "parameters": [
                    {
                        "type": "image",
                        "image": {
                            "id": "meta-without-proof",
                            "providerMediaReceiptId": str(missing_receipt_id),
                        },
                    }
                ],
            }
        ],
    }
    strategy = _StructuredStrategy()

    with pytest.raises(HTTPException) as caught:
        await _structured_service(db_session, strategy).send_structured(
            SendStructuredMessageRequest(
                contact_id=contact.id,
                connection_id=connection.id,
                message_payload=media if payload_kind == "media" else template,
                idempotency_key=f"structured-invalid-{payload_kind}-0001",
            ),
            auth_context.user_id,
            auth_context.workspace_id,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "provider_media_receipt_not_found"
    assert strategy.structured_calls == []
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 0
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.status == WaMessageStatus.delivery_uncertain
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_expired_immediate_receipt_is_rejected_before_message_creation(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    receipt = await _provider_media_receipt(
        db_session,
        connection,
        media_id="meta-expired-image",
        media_type="image",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    strategy = _StructuredStrategy()

    with pytest.raises(HTTPException) as caught:
        await _structured_service(db_session, strategy).send_structured(
            SendStructuredMessageRequest(
                contact_id=contact.id,
                connection_id=connection.id,
                message_payload={
                    "kind": "media",
                    "type": "image",
                    "providerMediaId": receipt.provider_media_id,
                    "providerMediaReceiptId": str(receipt.id),
                },
                idempotency_key="structured-expired-0001",
            ),
            auth_context.user_id,
            auth_context.workspace_id,
        )

    assert caught.value.detail["code"] == "provider_media_receipt_expired"
    assert strategy.structured_calls == []
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 0


@pytest.mark.asyncio
async def test_cross_workspace_receipt_is_rejected_before_message_creation(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    foreign_workspace = Workspace(
        name="Workspace mídia estrangeira",
        slug=f"foreign-provider-media-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_connection = WhatsAppConnection(
        workspace_id=foreign_workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        phone_number_id=f"foreign-media-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(foreign_connection)
    await db_session.commit()
    foreign_receipt = await _provider_media_receipt(
        db_session,
        foreign_connection,
        media_id="meta-foreign-image",
        media_type="image",
    )
    strategy = _StructuredStrategy()

    with pytest.raises(HTTPException) as caught:
        await _structured_service(db_session, strategy).send_structured(
            SendStructuredMessageRequest(
                contact_id=contact.id,
                connection_id=connection.id,
                message_payload={
                    "kind": "media",
                    "type": "image",
                    "providerMediaId": foreign_receipt.provider_media_id,
                    "providerMediaReceiptId": str(foreign_receipt.id),
                },
                idempotency_key="structured-cross-workspace-0001",
            ),
            auth_context.user_id,
            auth_context.workspace_id,
        )

    assert caught.value.detail["code"] == "provider_media_receipt_not_found"
    assert strategy.structured_calls == []
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 0


@pytest.mark.asyncio
async def test_sent_idempotent_replay_survives_later_receipt_expiration(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    connection, contact = await _connection_and_contact(
        db_session,
        auth_context.workspace_id,
    )
    receipt = await _provider_media_receipt(
        db_session,
        connection,
        media_id="meta-short-lived-image",
        media_type="image",
        expires_at=datetime.now(UTC) + timedelta(milliseconds=50),
    )
    payload = SendStructuredMessageRequest(
        contact_id=contact.id,
        connection_id=connection.id,
        message_payload={
            "kind": "media",
            "type": "image",
            "providerMediaId": receipt.provider_media_id,
            "providerMediaReceiptId": str(receipt.id),
        },
        idempotency_key="structured-replay-expiry-0001",
    )
    strategy = _StructuredStrategy()
    service = _structured_service(db_session, strategy)

    first = await service.send_structured(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )
    await asyncio.sleep(0.08)
    replay = await service.send_structured(
        payload,
        auth_context.user_id,
        auth_context.workspace_id,
    )

    assert first.messageId == replay.messageId
    assert replay.status == WaMessageStatus.sent
    assert len(strategy.structured_calls) == 1
    assert await db_session.scalar(select(func.count(WhatsAppMessage.id))) == 1
