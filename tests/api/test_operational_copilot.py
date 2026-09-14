from copy import deepcopy

import pytest

from app.core.deps import get_current_permissions
from app.main import app as fastapi_app
from app.services.playbook_service import PlaybookNotFoundError


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def no_effective_playbook(monkeypatch):
    async def _missing(*args, **kwargs):
        raise PlaybookNotFoundError("sem playbook publicado")

    monkeypatch.setattr(
        "app.services.operational_copilot_service.PlaybookService.effective",
        _missing,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "instruction", "expected_type"),
    [
        ("playbooks", "Crie um rascunho de triagem bancária.", "playbooks.create_draft"),
        ("tracking", "Mostre atribuição dos últimos 30 dias.", "tracking.select_tab"),
        (
            "connections",
            "Abra a parte do Instagram.",
            "connections.focus_section",
        ),
        (
            "social_comments",
            "Prepare uma regra para comentário orçamento.",
            "social.prepare_rule",
        ),
        ("agenda", "Prepare uma reunião em 25/07/2026 às 11h.", "agenda.open_create"),
    ],
)
async def test_operational_copilot_plans_typed_local_actions_only(
    client,
    domain,
    instruction,
    expected_type,
):
    response = await client.post(
        "/api/v1/operational-copilot/plan",
        json={
            "domain": domain,
            "instruction": instruction,
            "context_key": f"{domain}:default",
            "context_snapshot": {"practice_area": "saude", "count": 4},
            "attachments": [],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["domain"] == domain
    assert payload["status"] == "awaiting_approval"
    assert payload["approval_required"] is True
    assert payload["external_actions_executed"] == 0
    assert payload["actions"]
    assert payload["actions"][0]["payload"]["type"] == expected_type
    assert all(action["approval_required"] is True for action in payload["actions"])
    assert all(
        action["kind"] in {"ui_update", "local_read", "local_draft", "local_write"}
        for action in payload["actions"]
    )


@pytest.mark.asyncio
async def test_operational_copilot_rejects_audio_attachment(client):
    response = await client.post(
        "/api/v1/operational-copilot/plan",
        json={
            "domain": "agenda",
            "instruction": "Prepare uma reunião.",
            "attachments": [
                {
                    "name": "pedido.ogg",
                    "media_type": "audio/ogg",
                    "size_bytes": 100,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "não aceita arquivos de áudio" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret_context",
    [
        {"access_token": "não-deve-entrar"},
        {"connection": {"client-secret": "não-deve-entrar"}},
        {"items": [{"credentials": {"value": "não-deve-entrar"}}]},
    ],
)
async def test_operational_copilot_rejects_secret_bearing_context(
    client,
    secret_context,
):
    response = await client.post(
        "/api/v1/operational-copilot/plan",
        json={
            "domain": "connections",
            "instruction": "Mostre o status das conexões.",
            "context_snapshot": deepcopy(secret_context),
        },
    )

    assert response.status_code == 422
    assert "não pode conter credenciais ou segredos" in response.text


@pytest.mark.asyncio
async def test_agenda_copilot_uses_page_date_as_local_reference(client):
    response = await client.post(
        "/api/v1/operational-copilot/plan",
        json={
            "domain": "agenda",
            "instruction": "Prepare uma reunião amanhã às 11h.",
            "context_snapshot": {"selected_date": "2026-07-24"},
        },
    )

    assert response.status_code == 200, response.text
    action = response.json()["actions"][0]
    assert action["payload"]["date"] == "2026-07-25"
    assert action["payload"]["hour"] == 11


@pytest.mark.asyncio
async def test_operational_copilot_enforces_backend_permission(client):
    previous_overrides = dict(fastapi_app.dependency_overrides)
    fastapi_app.dependency_overrides[get_current_permissions] = lambda: []

    try:
        response = await client.post(
            "/api/v1/operational-copilot/plan",
            json={
                "domain": "connections",
                "instruction": "Mostre o status das conexões.",
            },
        )
    finally:
        fastapi_app.dependency_overrides.clear()
        fastapi_app.dependency_overrides.update(previous_overrides)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "COPILOT_PERMISSION_DENIED"
