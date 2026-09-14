from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import WhatsAppMetaTemplate
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp.utility_template_catalog import UTILITY_TEMPLATE_CATALOG
from app.services.whatsapp.utility_template_provisioning_service import (
    UtilityTemplateProvisioningService,
)


async def _connection(db, workspace_id: UUID) -> WhatsAppConnection:
    cipher = WhatsAppSecretCipher()
    item = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_catalog",
        phone_number="5561999999999",
        display_phone_number="+55 61 99999-9999",
        access_token_encrypted=cipher.encrypt("catalog-token"),
        waba_id="waba_catalog",
        status=WaConnectionStatus.connected,
    )
    db.add(item)
    await db.flush()
    return item


def _service(db):
    cipher = WhatsAppSecretCipher()
    meta = MetaCloudClient()
    connections = WhatsAppConnectionService(db, cipher=cipher, meta_client=meta)
    return UtilityTemplateProvisioningService(
        db,
        connection_service=connections,
        meta_client=meta,
        inter_request_delay_seconds=0,
    )


def _gate(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(
        "app.services.whatsapp.utility_template_provisioning_service.external_delivery_allowed",
        lambda: value,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: value,
    )


@pytest.mark.asyncio
async def test_catalog_submits_every_item_with_body_text_examples(
    db_session, auth_context, monkeypatch
):
    _gate(monkeypatch, True)
    connection = await _connection(db_session, auth_context.workspace_id)
    connection_id = connection.id
    service = _service(db_session)
    calls: list[dict] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        return {"id": f"meta-{kwargs['name']}", "status": "PENDING"}

    monkeypatch.setattr(service.meta_client, "create_message_template", fake_create)
    result = await service.provision(
        workspace_id=auth_context.workspace_id,
        connection_id=connection_id,
    )

    assert result["submitted"] == len(UTILITY_TEMPLATE_CATALOG)
    assert len(calls) == len(UTILITY_TEMPLATE_CATALOG)
    for call in calls:
        assert call["category"] == "utility"
        body = next(component for component in call["components"] if component["type"] == "BODY")
        assert body["example"]["body_text"][0]


@pytest.mark.asyncio
async def test_catalog_reexecution_is_idempotent(db_session, auth_context, monkeypatch):
    _gate(monkeypatch, True)
    connection = await _connection(db_session, auth_context.workspace_id)
    connection_id = connection.id
    service = _service(db_session)
    calls = 0

    async def fake_create(**kwargs):
        nonlocal calls
        calls += 1
        return {"id": f"meta-{kwargs['name']}", "status": "PENDING"}

    monkeypatch.setattr(service.meta_client, "create_message_template", fake_create)
    await service.provision(
        workspace_id=auth_context.workspace_id, connection_id=connection_id
    )
    await service.provision(
        workspace_id=auth_context.workspace_id, connection_id=connection_id
    )

    count = await db_session.scalar(select(func.count(WhatsAppMetaTemplate.id)))
    assert count == len(UTILITY_TEMPLATE_CATALOG)
    assert calls == len(UTILITY_TEMPLATE_CATALOG)


@pytest.mark.asyncio
async def test_gate_closed_prepares_without_marking_submitted(
    db_session, auth_context, monkeypatch
):
    _gate(monkeypatch, False)
    connection = await _connection(db_session, auth_context.workspace_id)
    connection_id = connection.id
    service = _service(db_session)

    async def should_not_call(**_kwargs):
        raise AssertionError("Meta must not be called with the external gate closed")

    monkeypatch.setattr(service.meta_client, "create_message_template", should_not_call)
    result = await service.provision(
        workspace_id=auth_context.workspace_id, connection_id=connection_id
    )
    catalog = await service.list_catalog(
        workspace_id=auth_context.workspace_id, connection_id=connection_id
    )

    assert result["submitted"] == 0
    assert all(item.external_template_id is None for item in catalog.items)
    assert all(item.provisioning_status == "catalog_pending_gate" for item in catalog.items)


@pytest.mark.asyncio
async def test_reserved_name_is_a_structured_operator_state(
    db_session, auth_context, monkeypatch
):
    _gate(monkeypatch, True)
    connection = await _connection(db_session, auth_context.workspace_id)
    connection_id = connection.id
    service = _service(db_session)
    key = UTILITY_TEMPLATE_CATALOG[0].key

    async def reserved(**_kwargs):
        raise WhatsAppProviderError(
            code="WA-002",
            message="Template name was deleted and remains reserved for 30 days",
            status_code=400,
            request_started=True,
        )

    monkeypatch.setattr(service.meta_client, "create_message_template", reserved)
    result = await service.provision(
        workspace_id=auth_context.workspace_id,
        connection_id=connection_id,
        only_keys={key},
    )
    catalog = await service.list_catalog(
        workspace_id=auth_context.workspace_id, connection_id=connection_id
    )
    item = next(entry for entry in catalog.items if entry.key == key)

    assert result["failed"] == 1
    assert item.provisioning_status == "catalog_name_reserved"
    assert item.error_code == "WA-TPL-CATALOG-NAME-RESERVED"


@pytest.mark.asyncio
async def test_reserved_name_retry_uses_a_new_versioned_name(
    db_session, auth_context, monkeypatch
):
    _gate(monkeypatch, True)
    connection = await _connection(db_session, auth_context.workspace_id)
    connection_id = connection.id
    service = _service(db_session)
    definition = UTILITY_TEMPLATE_CATALOG[0]
    calls: list[str] = []

    async def reserve_then_accept(**kwargs):
        calls.append(kwargs["name"])
        if len(calls) == 1:
            raise WhatsAppProviderError(
                code="WA-002",
                message="Template name remains reserved for 30 days",
                status_code=400,
                request_started=True,
            )
        return {"id": "meta-retry", "status": "PENDING"}

    monkeypatch.setattr(service.meta_client, "create_message_template", reserve_then_accept)
    await service.provision(
        workspace_id=auth_context.workspace_id,
        connection_id=connection_id,
        only_keys={definition.key},
    )
    await service.provision(
        workspace_id=auth_context.workspace_id,
        connection_id=connection_id,
        only_keys={definition.key},
        retry_rejected=True,
    )

    assert calls == [definition.meta_name, f"{definition.meta_name}_r2"]


@pytest.mark.asyncio
async def test_new_catalog_version_supersedes_failed_older_attempts(
    db_session, auth_context, monkeypatch
):
    # V33: a v1 recusada pela Meta (sem id externo) não pode continuar submetível à mão
    # depois que o catálogo reescreveu o corpo na v2.
    _gate(monkeypatch, True)
    connection = await _connection(db_session, auth_context.workspace_id)
    definition = next(
        item for item in UTILITY_TEMPLATE_CATALOG if item.key == "lembrete_audiencia"
    )
    assert definition.version == 2
    stale = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id=None,
        name="meu_executivo_lembrete_audiencia_v1",
        language="pt_BR",
        category="utility",
        status=definition_status_pending(),
        components=[],
        raw_payload={
            "source": "local_draft",
            "local_status": "catalog_failed",
            "utility_catalog": {
                "source": "meu_executivo_utility_catalog",
                "key": "lembrete_audiencia",
                "item_version": 1,
                "attempt": 1,
                "last_error_code": "WA-002",
            },
        },
    )
    db_session.add(stale)
    await db_session.commit()
    service = _service(db_session)

    prepared = await service.prepare(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        only_keys={"lembrete_audiencia"},
        retry_rejected=True,
    )

    assert [item.name for item in prepared] == ["meu_executivo_lembrete_audiencia_v2"]
    await db_session.refresh(stale)
    assert (stale.raw_payload or {}).get("local_status") == "catalog_superseded"
    catalog = await service.list_catalog(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )
    listed = next(item for item in catalog.items if item.key == "lembrete_audiencia")
    assert listed.meta_name == "meu_executivo_lembrete_audiencia_v2"
    assert listed.item_version == 2


def definition_status_pending():
    from app.models.whatsapp_campaign import MetaTemplateStatus

    return MetaTemplateStatus.pending
