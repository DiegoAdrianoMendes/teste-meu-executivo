"""S9: só o caso chega; o envio oficial não pode escolher um contato arbitrário."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.whatsapp import WhatsAppContact
from app.services.whatsapp_picker_service import WhatsAppPickerService
from tests.services.test_whatsapp_picker_service import _connection


async def _caso_com_dois_contatos(db_session, workspace_id, *, client_phone):
    connection = await _connection(db_session, workspace_id)
    client = Client(workspace_id=workspace_id, name="Cliente do caso", phone=client_phone)
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Picker",
    )
    db_session.add(matter)
    await db_session.flush()
    antigo = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5561{uuid4().int % 10**8:08d}",
        name="Procurador (mais antigo)",
        matter_id=matter.id,
    )
    db_session.add(antigo)
    await db_session.flush()
    do_cliente = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5561999990000",
        name="Telefone do cliente",
        matter_id=matter.id,
    )
    db_session.add(do_cliente)
    await db_session.commit()
    return matter, antigo, do_cliente


@pytest.mark.asyncio
async def test_envio_estrito_escolhe_o_contato_com_o_telefone_do_cliente(db_session, auth_context):
    matter, _antigo, do_cliente = await _caso_com_dois_contatos(
        db_session, auth_context.workspace_id, client_phone="(61) 99999-0000"
    )
    contact, _ = await WhatsAppPickerService(db_session)._resolve_context(
        auth_context.workspace_id, matter_id=matter.id, require_unambiguous_contact=True
    )
    assert contact is not None and contact.id == do_cliente.id


@pytest.mark.asyncio
async def test_envio_estrito_devolve_none_quando_o_caso_e_ambiguo(db_session, auth_context):
    matter, antigo, _ = await _caso_com_dois_contatos(
        db_session, auth_context.workspace_id, client_phone=None
    )
    picker = WhatsAppPickerService(db_session)
    estrito, _ = await picker._resolve_context(
        auth_context.workspace_id, matter_id=matter.id, require_unambiguous_contact=True
    )
    assert estrito is None
    # Variáveis e sugestões mantêm o comportamento antigo (o mais antigo).
    tolerante, _ = await picker._resolve_context(auth_context.workspace_id, matter_id=matter.id)
    assert tolerante is not None and tolerante.id == antigo.id


@pytest.mark.asyncio
async def test_caso_com_um_contato_so_continua_igual(db_session, auth_context):
    connection = await _connection(db_session, auth_context.workspace_id)
    client = Client(workspace_id=auth_context.workspace_id, name="Cliente único")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Picker",
    )
    db_session.add(matter)
    await db_session.flush()
    unico = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5561999990001",
        matter_id=matter.id,
    )
    db_session.add(unico)
    await db_session.commit()

    contact, _ = await WhatsAppPickerService(db_session)._resolve_context(
        auth_context.workspace_id, matter_id=matter.id, require_unambiguous_contact=True
    )
    assert contact is not None and contact.id == unico.id
