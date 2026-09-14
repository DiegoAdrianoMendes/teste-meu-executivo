from types import SimpleNamespace

from starlette.requests import Request

from app.services.support_access_service import required_support_scope_for_request


def _request(method: str, route_template: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": route_template,
            "query_string": b"",
            "headers": [],
            "scheme": "https",
            "server": ("test", 443),
            "client": ("127.0.0.1", 1234),
            "route": SimpleNamespace(path=route_template),
        }
    )


def test_nested_matter_task_route_resolves_task_scope_before_matter_prefix() -> None:
    route = "/api/v1/matters/{matter_id}/tasks"

    assert required_support_scope_for_request(_request("GET", route)) == "tasks:read"
    assert required_support_scope_for_request(_request("POST", route)) == "tasks:write"
    assert (
        required_support_scope_for_request(_request("PATCH", "/api/v1/matters/{matter_id}"))
        == "matters:write"
    )


def test_meeting_and_internal_agenda_routes_use_explicit_support_scopes() -> None:
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/meetings"))
        == "meetings:read"
    )
    assert (
        required_support_scope_for_request(
            _request("PUT", "/api/v1/meetings/{meeting_id}/participants")
        )
        == "meetings:write"
    )
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agenda"))
        == "meetings:read"
    )
    assert (
        required_support_scope_for_request(
            _request("POST", "/api/v1/agenda/internal-bookings")
        )
        == "meetings:write"
    )
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agenda/availability"))
        == "meetings:read"
    )
    assert (
        required_support_scope_for_request(
            _request("GET", "/api/v1/agenda/meetings/{meeting_id}/briefing")
        )
        == "meetings:read"
    )
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agenda/daily-briefing"))
        == "meetings:read"
    )
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agenda/settings"))
        == "configurations:read"
    )
    assert (
        required_support_scope_for_request(_request("PUT", "/api/v1/agenda/settings"))
        == "configurations:write"
    )
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agenda/booking-links"))
        == "configurations:read"
    )
    assert (
        required_support_scope_for_request(
            _request("POST", "/api/v1/agenda/booking-links/{link_id}/revoke")
        )
        == "configurations:write"
    )
    assert (
        required_support_scope_for_request(
            _request("POST", "/api/v1/agenda/public/{token}")
        )
        is None
    )


def test_booking_link_configuration_routes_never_fall_through_to_meeting_scopes() -> None:
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agenda/booking-links"))
        != "meetings:read"
    )
    assert (
        required_support_scope_for_request(
            _request("POST", "/api/v1/agenda/booking-links/{link_id}/revoke")
        )
        != "meetings:write"
    )


def test_agent_platform_routes_use_configuration_scopes() -> None:
    assert (
        required_support_scope_for_request(_request("GET", "/api/v1/agent-platform/runs"))
        == "configurations:read"
    )
    assert (
        required_support_scope_for_request(
            _request("POST", "/api/v1/agent-platform/runs/{run_id}/retry")
        )
        == "configurations:write"
    )
