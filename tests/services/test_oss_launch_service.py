"""Security contracts for short-lived local OSS launch tickets."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from app.core.config import settings
from app.services import oss_launch_service
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_launch_service import OSSLaunchError
from app.services.oss_motor_routes import ROCKETCHAT_ROOM_TABS

ROOT = Path(__file__).resolve().parents[2]
ACTOR_USER_ID = UUID("0d468737-a05f-4b2a-8847-84d093c7852a")
MEMBERSHIP_ID = UUID("a0843a76-218f-40bc-a374-fcd19743cf4c")


@pytest.fixture
def launch_workspace(monkeypatch, tmp_path) -> UUID:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    monkeypatch.setattr(settings, "KLM_OSS_LOCAL_BRIDGES_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_OSS_WORKSPACE_ID", str(workspace_id))
    monkeypatch.setattr(settings, "KLM_OSS_LAUNCH_SECRET", "a" * 64)
    monkeypatch.setattr(settings, "KLM_CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.setattr(
        oss_launch_service,
        "_NONCE_LEDGER_PATH",
        tmp_path / "local" / "oss-launch-nonces.json",
    )
    return workspace_id


def _ticket(launch: dict) -> str:
    return parse_qs(urlsplit(launch["launchUrl"]).query)["ticket"][0]


def _issue(service: str, workspace_id: UUID, **kwargs) -> dict:
    return oss_launch_service.issue_launch_ticket(
        service,
        workspace_id,
        actor_user_id=ACTOR_USER_ID,
        membership_id=MEMBERSHIP_ID,
        actor_role="workspace_owner",
        **kwargs,
    )


def test_launch_ticket_rejects_workspace_not_bound_to_sidecars(launch_workspace) -> None:
    with pytest.raises(OSSLaunchError, match="Workspace não autorizado"):
        _issue(
            "chatwoot",
            UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069"),
        )


def test_launch_ticket_is_rejected_when_runtime_workspace_changes(
    launch_workspace,
    monkeypatch,
) -> None:
    ticket = _ticket(_issue("chatwoot", launch_workspace))
    monkeypatch.setattr(
        settings,
        "KLM_OSS_WORKSPACE_ID",
        "3c20e4c7-d8f3-4336-93af-733d4a7b9069",
    )

    with pytest.raises(OSSLaunchError, match="outro workspace"):
        oss_launch_service.consume_launch_ticket(ticket, "chatwoot")


def test_launch_ticket_replay_is_rejected_by_durable_ledger(launch_workspace) -> None:
    ticket = _ticket(_issue("bottlecrm", launch_workspace))

    assert oss_launch_service.consume_launch_ticket(ticket, "bottlecrm")["workspaceId"] == str(
        launch_workspace
    )
    with pytest.raises(OSSLaunchError, match="já utilizado"):
        oss_launch_service.consume_launch_ticket(ticket, "bottlecrm")


def test_launch_ticket_concurrent_consumption_has_one_winner(launch_workspace) -> None:
    ticket = _ticket(_issue("rocketchat", launch_workspace))

    def consume() -> str:
        try:
            oss_launch_service.consume_launch_ticket(ticket, "rocketchat")
            return "accepted"
        except OSSLaunchError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: consume(), range(2)))

    assert sorted(outcomes) == ["accepted", "rejected"]


def test_launch_ticket_carries_allowlisted_capability_context(launch_workspace) -> None:
    launch = _issue(
        "chatwoot",
        launch_workspace,
        context={
            "returnTo": "/app/conversas?thread=local",
            "capabilityId": "chatwoot.conversation-composer",
            "matterId": "1cc6c830-b0ab-4bc3-98b6-9c536be6451f",
            "surface": "Conversas",
        },
    )

    assert launch["context"]["destinationPath"] == "/app/accounts/1/dashboard"
    claims = oss_launch_service.consume_launch_ticket(_ticket(launch), "chatwoot")
    assert claims["context"] == launch["context"]
    assert claims["context"]["returnTo"] == "/app/conversas?thread=local"
    assert claims["scope"] == "oss-admin-session"
    assert claims["actorUserId"] == str(ACTOR_USER_ID)
    assert claims["membershipId"] == str(MEMBERSHIP_ID)
    assert claims["actorRole"] == "workspace_owner"


def test_launch_ticket_rejects_cross_provider_capability(launch_workspace) -> None:
    with pytest.raises(OSSLaunchError, match="não pertence"):
        _issue(
            "chatwoot",
            launch_workspace,
            context={"capabilityId": "bottlecrm.opportunities-kanban"},
        )


def test_launch_ticket_rejects_blocked_capability(launch_workspace) -> None:
    with pytest.raises(OSSLaunchError, match="bloqueada"):
        _issue(
            "bottlecrm",
            launch_workspace,
            context={"capabilityId": "bottlecrm.orders"},
        )


def test_every_launchable_capability_has_a_specific_motor_destination() -> None:
    for capability in oss_capability_registry()["capabilities"]:
        if not capability["launchable"]:
            continue
        assert capability["key"] in oss_launch_service._CAPABILITY_DESTINATIONS[
            capability["provider"]
        ], capability["id"]


def test_blocked_capabilities_never_have_motor_destinations() -> None:
    for capability in oss_capability_registry()["capabilities"]:
        is_blocked = (
            capability["disposition"].startswith("blocked_")
            or capability["disposition"] == "not_applicable"
        )
        if is_blocked:
            assert not capability["launchable"], capability["id"]
            assert capability["motorPath"] is None, capability["id"]


def test_bottlecrm_destinations_exist_in_the_pinned_frontend() -> None:
    routes_root = ROOT / ".vendor/upstreams/bottlecrm/frontend/src/routes"
    routes = {
        "/" + str(path.parent.relative_to(routes_root)).replace("(app)/", "").replace(
            "(no-layout)/", ""
        ).strip("/")
        for path in routes_root.rglob("+page.svelte")
    }
    routes.add("/")

    for capability, destination in oss_launch_service._CAPABILITY_DESTINATIONS[
        "bottlecrm"
    ].items():
        assert destination in routes, f"{capability}: {destination}"


def test_chattermate_destinations_exist_in_the_pinned_router() -> None:
    router_source = (
        ROOT / ".vendor/upstreams/chattermate/frontend/src/router/index.ts"
    ).read_text()

    for capability, destination in oss_launch_service._CAPABILITY_DESTINATIONS[
        "chattermate"
    ].items():
        path = destination.partition("?")[0]
        assert f"path: '{path}'" in router_source, f"{capability}: {destination}"


def test_chatwoot_destinations_match_the_audited_route_contract() -> None:
    expected = {
        "dashboard",
        "contacts",
        "settings/macros",
        "settings/automation/list",
        "campaigns/live_chat",
        "reports/overview",
        "settings/custom-attributes/list",
        "settings/agents/list",
        "settings/integrations",
        "portals",
        "settings/integrations/webhook",
        "search",
        "notifications",
        "settings/canned-response/list",
        "settings/teams/list",
        "settings/agent-bots",
        "settings/inboxes/list",
        "settings/assignment-policy/index",
        "settings/conversation-workflow",
        "profile/settings",
        "settings/general",
        "settings/labels/list",
        "settings/integrations/dashboard_apps",
        "reports/csat",
    }

    assert set(oss_launch_service._CAPABILITY_DESTINATIONS["chatwoot"].values()) == expected
    assert "settings/attributes/list" not in expected
    assert "inbox-view" not in expected


def test_rocketchat_generic_home_is_only_a_contextual_room_fallback() -> None:
    destinations = oss_launch_service._CAPABILITY_DESTINATIONS["rocketchat"]
    home_capabilities = {key for key, path in destinations.items() if path == "/home"}

    assert home_capabilities == set(ROCKETCHAT_ROOM_TABS)
    assert destinations["teams-membership"] == "/directory/teams"
    assert destinations["apps-engine-custom-apps"] == "/marketplace/installed/list"
    assert destinations["emoji-administration"] == "/admin/emoji"
    assert destinations["oauth-providers"] == "/admin/settings/OAuth"
    assert "rest-webhooks-bridge" not in destinations


@pytest.mark.parametrize("return_to", ["https://example.test", "//example.test", "/login"])
def test_launch_ticket_rejects_non_klm_return_route(launch_workspace, return_to) -> None:
    with pytest.raises(OSSLaunchError, match="retorno"):
        _issue(
            "bottlecrm",
            launch_workspace,
            context={"returnTo": return_to},
        )
