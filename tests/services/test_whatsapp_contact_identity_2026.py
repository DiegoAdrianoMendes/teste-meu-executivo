"""Resolução de contato na convivência telefone / BSUID (WhatsApp 2026).

Prova de integração da mudança de identidade: exercita `_get_or_create_contact`
contra banco real, com as três formas de evento que a Meta passa a enviar.

O que cada teste protege:

- **contato sem telefone nasce** — cenário novo, impossível antes porque a
  coluna era `NOT NULL`;
- **o mesmo cliente não vira dois cadastros** quando volta sem o número, que é
  a falha silenciosa mais cara da transição;
- **identificador novo enriquece o cadastro existente**, para a base se
  atualizar sozinha durante a convivência, sem varredura;
- **evento sem nenhum identificador falha alto**, em vez de sumir.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

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
from app.services.whatsapp.identity_resolution import WhatsAppIdentityRef
from app.services.whatsapp.webhook_service import (
    WhatsAppContactWithoutIdentifierError,
    WhatsAppWebhookService,
)

BSUID = "BR.13491208655302741918"
OUTRO_BSUID = "BR.99999999999999999999"
PHONE = "5561999990000"


def _service(db) -> WhatsAppWebhookService:
    """Constrói o serviço só com a sessão.

    Os quatro colaboradores são exigidos pelo construtor mas NÃO são tocados
    por `_get_or_create_contact`, que é o único método sob teste aqui. Passar
    `None` deixa isso explícito: se algum deles passar a ser usado nesse
    caminho, o teste quebra com AttributeError em vez de passar por acaso com
    um dublê permissivo.
    """
    return WhatsAppWebhookService(
        db,
        connection_service=None,  # type: ignore[arg-type]
        message_service=None,  # type: ignore[arg-type]
        automation_rule_service=None,  # type: ignore[arg-type]
        campaign_service=None,  # type: ignore[arg-type]
    )


async def _seed(db):
    suffix = uuid4().hex[:8]
    workspace = Workspace(
        name=f"Identidade {suffix}",
        slug=f"identidade-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()

    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-{suffix}",
        status=WaConnectionStatus.connected,
    )
    db.add(connection)
    await db.flush()
    return workspace, connection


async def _contagem(db, workspace_id) -> int:
    return await db.scalar(
        select(func.count()).select_from(WhatsAppContact).where(
            WhatsAppContact.workspace_id == workspace_id
        )
    )


@pytest.mark.asyncio
async def test_contato_nasce_sem_telefone_apenas_com_bsuid(db_session):
    """Cenário novo de 2026. Antes, a coluna NOT NULL tornava isto impossível:
    a mensagem chegava e não tinha onde pousar."""
    workspace, connection = await _seed(db_session)
    service = _service(db_session)

    contact = await service._get_or_create_contact(
        "",
        workspace.id,
        connection.id,
        None,
        identity=WhatsAppIdentityRef(bsuid=BSUID, username="cliente", display_name="Cliente"),
    )

    assert contact.phone is None
    assert contact.bsuid == BSUID
    assert contact.username == "cliente"
    assert contact.name == "Cliente"


@pytest.mark.asyncio
async def test_mesmo_cliente_sem_telefone_nao_vira_segundo_cadastro(db_session):
    """A falha silenciosa mais cara da transição: o cliente volta com o número
    oculto e o CRM abre outro cadastro, perdendo o histórico."""
    workspace, connection = await _seed(db_session)
    service = _service(db_session)

    primeiro = await service._get_or_create_contact(
        PHONE, workspace.id, connection.id, "Cliente", identity=WhatsAppIdentityRef(bsuid=BSUID)
    )
    await db_session.flush()

    # Mesmo cliente volta depois, agora sem telefone nenhum no evento.
    segundo = await service._get_or_create_contact(
        "", workspace.id, connection.id, None, identity=WhatsAppIdentityRef(bsuid=BSUID)
    )

    assert segundo.id == primeiro.id
    assert await _contagem(db_session, workspace.id) == 1
    # E o telefone que já conhecíamos não é apagado pelo evento sem número.
    assert segundo.phone == PHONE


@pytest.mark.asyncio
async def test_bsuid_novo_enriquece_contato_que_so_tinha_telefone(db_session):
    """É assim que a base migra sozinha durante a convivência: sem varredura,
    sem script de backfill, sem janela de manutenção."""
    workspace, connection = await _seed(db_session)
    service = _service(db_session)

    antigo = await service._get_or_create_contact(PHONE, workspace.id, connection.id, "Cliente")
    await db_session.flush()
    assert antigo.bsuid is None

    mesmo = await service._get_or_create_contact(
        PHONE,
        workspace.id,
        connection.id,
        None,
        identity=WhatsAppIdentityRef(phone=PHONE, bsuid=BSUID, username="cliente"),
    )

    assert mesmo.id == antigo.id
    assert mesmo.bsuid == BSUID
    assert mesmo.username == "cliente"
    assert await _contagem(db_session, workspace.id) == 1


@pytest.mark.asyncio
async def test_nono_digito_meta_resolve_contato_existente_sem_duplicar(db_session):
    workspace, connection = await _seed(db_session)
    legacy = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="556182502136",
        name="Kleist teste",
    )
    db_session.add(legacy)
    await db_session.flush()

    resolved = await _service(db_session)._get_or_create_contact(
        "5561982502136",
        workspace.id,
        connection.id,
        "Kleist Monteiro",
    )

    assert resolved.id == legacy.id
    assert resolved.phone == "5561982502136"
    assert await _contagem(db_session, workspace.id) == 1


@pytest.mark.asyncio
async def test_nono_digito_consolida_duplicados_historicos_e_preserva_mensagens(db_session):
    workspace, connection = await _seed(db_session)
    canonical = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="5561982502136",
        name="Kleist teste",
    )
    legacy = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="556182502136",
        name="Kleist Monteiro",
    )
    db_session.add_all([canonical, legacy])
    await db_session.flush()
    db_session.add(
        WhatsAppMessage(
            contact_id=legacy.id,
            whatsapp_connection_id=connection.id,
            wamid="wamid.br.alias.previous",
            direction=WaMessageDirection.inbound,
            status=WaMessageStatus.delivered,
            body="Mensagem anterior.",
        )
    )
    await db_session.flush()

    resolved = await _service(db_session)._get_or_create_contact(
        "556182502136",
        workspace.id,
        connection.id,
        "Kleist Monteiro",
    )
    await db_session.flush()

    assert resolved.id == canonical.id
    assert resolved.phone == "5561982502136"
    assert await _contagem(db_session, workspace.id) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(WhatsAppMessage).where(
            WhatsAppMessage.contact_id == canonical.id
        )
    ) == 1


@pytest.mark.asyncio
async def test_bsuid_consolida_alias_antes_de_canonizar_telefone(db_session):
    workspace, connection = await _seed(db_session)
    canonical = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="5561982502136",
        name="Kleist teste",
    )
    identified = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="556182502136",
        bsuid=BSUID,
        name="Kleist Monteiro",
    )
    db_session.add_all([canonical, identified])
    await db_session.flush()

    resolved = await _service(db_session)._get_or_create_contact(
        "556182502136",
        workspace.id,
        connection.id,
        "Kleist Monteiro",
        identity=WhatsAppIdentityRef(phone="556182502136", bsuid=BSUID),
    )
    await db_session.flush()

    assert resolved.id == identified.id
    assert resolved.phone == "5561982502136"
    assert await _contagem(db_session, workspace.id) == 1


@pytest.mark.asyncio
async def test_bsuid_vence_telefone_na_busca(db_session):
    """Se o telefone fosse consultado primeiro, um número reciclado por outra
    pessoa entregaria a conversa errada ao contato errado."""
    workspace, connection = await _seed(db_session)
    service = _service(db_session)

    dono_do_bsuid = await service._get_or_create_contact(
        "", workspace.id, connection.id, "Dono do BSUID", identity=WhatsAppIdentityRef(bsuid=BSUID)
    )
    await service._get_or_create_contact(
        PHONE, workspace.id, connection.id, "Dono do telefone",
        identity=WhatsAppIdentityRef(bsuid=OUTRO_BSUID),
    )
    await db_session.flush()

    resolvido = await service._get_or_create_contact(
        PHONE, workspace.id, connection.id, None, identity=WhatsAppIdentityRef(bsuid=BSUID)
    )

    assert resolvido.id == dono_do_bsuid.id


@pytest.mark.asyncio
async def test_username_sempre_sobrescreve_porque_muda(db_session):
    workspace, connection = await _seed(db_session)
    service = _service(db_session)

    contact = await service._get_or_create_contact(
        "", workspace.id, connection.id, None,
        identity=WhatsAppIdentityRef(bsuid=BSUID, username="antigo"),
    )
    await db_session.flush()

    atualizado = await service._get_or_create_contact(
        "", workspace.id, connection.id, None,
        identity=WhatsAppIdentityRef(bsuid=BSUID, username="novo"),
    )

    assert atualizado.id == contact.id
    assert atualizado.username == "novo"


@pytest.mark.asyncio
async def test_evento_sem_identificador_falha_alto_em_vez_de_sumir(db_session):
    """Mensagem que some em silêncio é cliente perdido sem ninguém saber que
    perdeu. Precisa virar exceção operacional visível."""
    workspace, connection = await _seed(db_session)
    service = _service(db_session)

    with pytest.raises(WhatsAppContactWithoutIdentifierError):
        await service._get_or_create_contact("", workspace.id, connection.id, None)

    assert await _contagem(db_session, workspace.id) == 0


@pytest.mark.asyncio
async def test_contatos_de_conexoes_diferentes_nao_se_misturam(db_session):
    """BSUID é escopado por portfólio: o mesmo valor em conexões distintas são
    pessoas potencialmente diferentes, e juntá-las misturaria dois casos."""
    workspace, conexao_a = await _seed(db_session)
    conexao_b = WhatsAppConnection(
        workspace_id=workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=False,
        phone_number_id=f"pn-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(conexao_b)
    await db_session.flush()

    service = _service(db_session)
    em_a = await service._get_or_create_contact(
        "", workspace.id, conexao_a.id, None, identity=WhatsAppIdentityRef(bsuid=BSUID)
    )
    await db_session.flush()
    em_b = await service._get_or_create_contact(
        "", workspace.id, conexao_b.id, None, identity=WhatsAppIdentityRef(bsuid=BSUID)
    )

    assert em_a.id != em_b.id
    assert await _contagem(db_session, workspace.id) == 2
