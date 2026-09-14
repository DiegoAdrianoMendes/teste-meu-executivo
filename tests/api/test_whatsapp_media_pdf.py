from __future__ import annotations

import io

import pytest
from PIL import Image

from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.services.s3_service import S3Service


pytestmark = pytest.mark.usefixtures("active_subscription")


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 16), color=(218, 167, 76)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_image_message_is_converted_to_pdf_and_exposes_transcripts(
    client,
    db_session,
    auth_context,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path)
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-local-image",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999990001",
        name="Cliente Imagem",
    )
    db_session.add(contact)
    await db_session.flush()
    object_key = f"whatsapp/{contact.id}/imagem.png"
    local_path = S3Service.local_object_path(object_key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_png_bytes())
    message = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        direction=WaMessageDirection.inbound,
        status=WaMessageStatus.delivered,
        body="Prescrição fotografada",
        media_url=object_key,
        media_type="image",
        transcript="Transcrição de áudio ausente",
        image_transcript="Medicamento 40 mg",
    )
    db_session.add(message)
    await db_session.commit()

    conversation = await client.get(f"/api/v1/whatsapp/contacts/{contact.id}")
    assert conversation.status_code == 200, conversation.text
    persisted_message = conversation.json()["messages"][0]
    assert persisted_message["mediaType"] == "image"
    assert persisted_message["imageTranscript"] == "Medicamento 40 mg"
    assert persisted_message["transcript"] == "Transcrição de áudio ausente"

    response = await client.post(f"/api/v1/whatsapp/messages/{message.id}/to-pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith(f'mensagem-{message.id}.pdf"')
    assert response.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_image_pdf_conversion_is_scoped_to_workspace(
    client,
    db_session,
    auth_context,
):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    other_workspace = Workspace(
        name="Outro workspace",
        slug="outro-workspace-pdf",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=other_workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-other-image",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=other_workspace.id,
        whatsapp_connection_id=connection.id,
        phone="5511999990002",
    )
    db_session.add(contact)
    await db_session.flush()
    message = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        direction=WaMessageDirection.inbound,
        status=WaMessageStatus.delivered,
        body="Imagem de outro workspace",
        media_url="whatsapp/outro/imagem.png",
        media_type="image",
    )
    db_session.add(message)
    await db_session.commit()

    response = await client.post(f"/api/v1/whatsapp/messages/{message.id}/to-pdf")
    assert response.status_code == 404
