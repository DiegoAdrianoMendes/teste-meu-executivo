"""S19: o motivo de indisponibilidade chega ao frontend pela chave que ele lê."""

from typing import get_args, get_origin

import pytest

from app.schemas.flow import FlowNodeCatalogEntryRead


def _valor_minimo(annotation):
    origin = get_origin(annotation)
    if origin is not None and type(None) in get_args(annotation):
        return None
    if annotation is str:
        return "x"
    if annotation is int:
        return 1
    if annotation is bool:
        return False
    if origin is list or annotation is list:
        return []
    if origin is dict or annotation is dict:
        return {}
    args = get_args(annotation)
    if args and all(isinstance(a, str) for a in args):  # Literal
        return args[0]
    return "x"


def _entrada(**overrides):
    payload = {
        name: _valor_minimo(field.annotation)
        for name, field in FlowNodeCatalogEntryRead.model_fields.items()
        if field.is_required()
    }
    payload.update(overrides)
    return FlowNodeCatalogEntryRead.model_validate(payload)


def test_unavailable_reason_is_mirrored_into_the_key_the_frontend_reads():
    entry = _entrada(availability="unavailable", unavailable_reason="Executor ainda não existe.")
    dumped = entry.model_dump(by_alias=True)
    assert dumped["availabilityReason"] == "Executor ainda não existe."
    assert dumped["unavailable_reason"] == "Executor ainda não existe."


def test_explicit_availability_reason_wins_and_empty_reason_stays_none():
    explicito = _entrada(unavailable_reason="a", availability_reason="b")
    assert explicito.availability_reason == "b"
    vazio = _entrada(unavailable_reason="")
    assert vazio.availability_reason is None


@pytest.mark.asyncio
# O catálogo de flows passa pela matriz da assinatura; em APP_ENV=test (como no CI) não há
# isenção de assinatura ausente, então o workspace precisa de assinatura ativa.
@pytest.mark.usefixtures("active_subscription")
async def test_catalog_endpoint_serializes_the_alias_when_a_node_is_unavailable(client):
    response = await client.get("/api/v1/flows/catalog")
    assert response.status_code == 200, response.text
    for entry in response.json()["nodes"]:
        if entry.get("availability") == "unavailable":
            assert entry["availabilityReason"] == entry["unavailableReason"]
