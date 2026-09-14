from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.deps import can_view_sensitive_lead_data
from app.main import app as fastapi_app
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _contact(
    db_session,
    *,
    workspace_id,
    phone: str | None,
    bsuid: str,
    username: str,
) -> WhatsAppContact:
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-{uuid4().hex}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone=phone,
        bsuid=bsuid,
        parent_bsuid=f"parent-{bsuid}",
        username=username,
        name="Contato Identidade",
    )
    db_session.add(contact)
    await db_session.flush()
    return contact


@pytest.mark.asyncio
async def test_contact_identity_returns_all_identifiers_with_sensitive_permission(
    client,
    db_session,
    auth_context,
):
    contact = await _contact(
        db_session,
        workspace_id=auth_context.workspace_id,
        phone=None,
        bsuid="bsuid-addressable",
        username="usuario.meta",
    )

    response = await client.get(f"/api/v1/whatsapp/contacts/{contact.id}/identity")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(contact.id),
        "phone": None,
        "phoneMasked": False,
        "bsuid": "bsuid-addressable",
        "parentBsuid": "parent-bsuid-addressable",
        "username": "usuario.meta",
        "addressable": True,
    }


@pytest.mark.asyncio
async def test_contact_identity_masks_every_personal_identifier_without_permission(
    client,
    db_session,
    auth_context,
):
    contact = await _contact(
        db_session,
        workspace_id=auth_context.workspace_id,
        phone="5561999990000",
        bsuid="bsuid-private",
        username="identidade.privada",
    )
    fastapi_app.dependency_overrides[can_view_sensitive_lead_data] = lambda: False

    response = await client.get(f"/api/v1/whatsapp/contacts/{contact.id}/identity")
    contacts = await client.get("/api/v1/whatsapp/contacts")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(contact.id),
        "phone": "•••••",
        "phoneMasked": True,
        "bsuid": None,
        "parentBsuid": None,
        "username": None,
        "addressable": True,
    }
    assert contacts.status_code == 200, contacts.text
    listed = next(item for item in contacts.json() if item["id"] == str(contact.id))
    assert listed["phone"] == "Oculto pelo administrador"
    assert listed["bsuid"] is None
    assert listed["parentBsuid"] is None
    assert listed["username"] is None


@pytest.mark.asyncio
async def test_contact_identity_hides_cross_workspace_existence(
    client,
    db_session,
):
    other_workspace = Workspace(
        name="Workspace Identidade Externa",
        slug=f"workspace-identidade-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    contact = await _contact(
        db_session,
        workspace_id=other_workspace.id,
        phone="5561888880000",
        bsuid="bsuid-other-workspace",
        username="outro.workspace",
    )

    response = await client.get(f"/api/v1/whatsapp/contacts/{contact.id}/identity")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "WA-404"
