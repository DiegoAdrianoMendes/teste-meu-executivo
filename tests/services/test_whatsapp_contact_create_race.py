"""S3/S4: criação de contato sob corrida (savepoint) e busca com as formas `+`."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select

from app.models.whatsapp import WhatsAppContact
from tests.services.test_whatsapp_campaign_tier_quota import AlwaysOkMetaClient
from tests.services.test_whatsapp_campaign_tier_quota import _service as _campaign_service
from tests.services.test_whatsapp_contact_identity_2026 import _seed
from tests.services.test_whatsapp_contact_identity_2026 import _service as _webhook_service


def _concorrente_no_begin_nested(db_session, monkeypatch, *, values: dict) -> list[int]:
    """Entre a releitura e o SAVEPOINT, outra entrega insere o mesmo contato na
    transação externa. É o único gancho que reproduz a corrida com o código
    corrigido: dentro de `flush` o concorrente entraria no próprio savepoint e
    seria desfeito junto."""
    real = db_session.begin_nested
    hook_calls: list[int] = []

    class _Wrapper:
        def __init__(self, ctx):
            self.ctx = ctx

        async def __aenter__(self):
            await db_session.execute(insert(WhatsAppContact).values(**values))
            return await self.ctx.__aenter__()

        async def __aexit__(self, *exc):
            return await self.ctx.__aexit__(*exc)

    def hooked(*args, **kwargs):
        hook_calls.append(1)
        return _Wrapper(real(*args, **kwargs))

    monkeypatch.setattr(db_session, "begin_nested", hooked)
    return hook_calls


async def _contagem(db_session, workspace_id, phone) -> int:
    return await db_session.scalar(
        select(func.count()).select_from(WhatsAppContact).where(
            WhatsAppContact.workspace_id == workspace_id, WhatsAppContact.phone == phone
        )
    )


@pytest.mark.asyncio
async def test_webhook_converge_para_o_contato_que_venceu_a_corrida(db_session, monkeypatch):
    workspace, connection = await _seed(db_session)
    await db_session.commit()
    phone = "5561999990101"
    concorrente_id = uuid4()
    hook_calls = _concorrente_no_begin_nested(
        db_session,
        monkeypatch,
        values={
            "id": concorrente_id,
            "workspace_id": workspace.id,
            "whatsapp_connection_id": connection.id,
            "phone": phone,
            "name": "Concorrente",
        },
    )

    contact = await _webhook_service(db_session)._get_or_create_contact(
        phone, workspace.id, connection.id, "Chegou depois"
    )

    assert hook_calls == [1]
    assert contact.id == concorrente_id
    assert await _contagem(db_session, workspace.id, phone) == 1
    await db_session.flush()  # a transação continua viva depois da colisão


@pytest.mark.asyncio
async def test_envio_operado_converge_para_o_contato_que_venceu_a_corrida(
    db_session, monkeypatch
):
    workspace, connection = await _seed(db_session)
    await db_session.commit()
    phone = "5561999990102"
    concorrente_id = uuid4()
    hook_calls = _concorrente_no_begin_nested(
        db_session,
        monkeypatch,
        values={
            "id": concorrente_id,
            "workspace_id": workspace.id,
            "whatsapp_connection_id": connection.id,
            "phone": phone,
        },
    )

    service = _campaign_service(db_session, connection, AlwaysOkMetaClient())
    contact = await service.ensure_direct_contact(
        workspace_id=workspace.id, connection=connection, phone=f"+{phone}", name="Operador"
    )

    assert hook_calls == [1]
    assert contact.id == concorrente_id
    assert await _contagem(db_session, workspace.id, phone) == 1
    await db_session.flush()


@pytest.mark.asyncio
async def test_contato_gravado_com_mais_e_reconhecido_pelo_webhook_e_pelo_envio(db_session):
    workspace, connection = await _seed(db_session)
    legado = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="+5561999990103",
        name="Legado com +",
    )
    db_session.add(legado)
    await db_session.commit()

    via_webhook = await _webhook_service(db_session)._get_or_create_contact(
        "5561999990103", workspace.id, connection.id, None
    )
    assert via_webhook.id == legado.id

    service = _campaign_service(db_session, connection, AlwaysOkMetaClient())
    via_envio = await service.ensure_direct_contact(
        workspace_id=workspace.id, connection=connection, phone="5561999990103", name=None
    )
    assert via_envio.id == legado.id
    assert (
        await db_session.scalar(
            select(func.count()).select_from(WhatsAppContact).where(
                WhatsAppContact.workspace_id == workspace.id
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_com_as_duas_formas_na_conexao_o_envio_prefere_a_canonica_sem_colidir(db_session):
    workspace, connection = await _seed(db_session)
    canonico = WhatsAppContact(
        workspace_id=workspace.id, whatsapp_connection_id=connection.id, phone="5561999990104"
    )
    com_mais = WhatsAppContact(
        workspace_id=workspace.id, whatsapp_connection_id=connection.id, phone="+5561999990104"
    )
    db_session.add_all((canonico, com_mais))
    await db_session.commit()

    service = _campaign_service(db_session, connection, AlwaysOkMetaClient())
    contact = await service.ensure_direct_contact(
        workspace_id=workspace.id, connection=connection, phone="+5561999990104", name=None
    )
    assert contact.id == canonico.id
    await db_session.flush()
