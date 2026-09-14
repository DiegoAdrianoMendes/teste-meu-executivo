#!/usr/bin/env python3
"""Idempotently configure the local Rocket.Chat Community workspace."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "http://rocketchat:3000"
INTEGRATION_NAMES = {
    "public": "KLM Hub local public bridge",
    "private": "KLM Hub local case bridge",
}
CHANNELS = (
    "comercial",
    "juridico-saude",
    "secretaria",
    "paralegal",
    "peticoes",
    "financeiro",
    "marketing",
    "track",
    "ti",
    "incidentes",
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required local bootstrap variable: {name}")
    return value


def request_json(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            raw = response.read()
    except HTTPError as error:
        status = error.code
        raw = error.read()
    try:
        decoded = json.loads(raw or b"{}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"Rocket.Chat returned invalid JSON for {path}: HTTP {status}") from error
    if status not in expected:
        message = decoded.get("error") or decoded.get("message") or decoded
        raise SystemExit(f"Rocket.Chat request failed for {path}: HTTP {status}: {message}")
    return status, decoded


admin_username = required("KLM_OSS_ROCKETCHAT_ADMIN_USERNAME")
admin_password = required("KLM_OSS_ROCKETCHAT_ADMIN_PASSWORD")
webhook_token = required("KLM_OSS_ROCKETCHAT_WEBHOOK_TOKEN")
workspace_id = os.environ.get("KLM_OSS_KLM_WORKSPACE_ID", "").strip()

_, login = request_json(
    "POST",
    "/api/v1/login",
    payload={"user": admin_username, "password": admin_password},
)
auth = login.get("data") or {}
auth_headers = {
    "X-Auth-Token": str(auth.get("authToken") or ""),
    "X-User-Id": str(auth.get("userId") or ""),
}
if not all(auth_headers.values()):
    raise SystemExit("Rocket.Chat login did not return an auth token and user ID")

room_ids: dict[str, str] = {}
for channel in CHANNELS:
    query = urlencode({"roomName": channel})
    status, info = request_json(
        "GET",
        f"/api/v1/channels.info?{query}",
        headers=auth_headers,
        expected=(200, 400),
    )
    room = info.get("channel") if status == 200 else None
    if not isinstance(room, dict):
        _, created = request_json(
            "POST",
            "/api/v1/channels.create",
            headers=auth_headers,
            payload={"name": channel, "readOnly": False},
        )
        room = created.get("channel") or {}
    room_id = str(room.get("_id") or "")
    if not room_id:
        raise SystemExit(f"Rocket.Chat did not return an ID for channel {channel}")
    room_ids[channel] = room_id

integration_ids: dict[str, str] = {}
if workspace_id:
    _, listed = request_json(
        "GET",
        "/api/v1/integrations.list?count=100&offset=0",
        headers=auth_headers,
    )
    existing_integrations = {
        integration.get("name"): integration
        for integration in listed.get("integrations") or []
        if isinstance(integration, dict)
    }
    desired_integrations = {
        "public": ",".join(f"#{channel}" for channel in CHANNELS),
        "private": "all_private_groups",
    }
    for scope, channel_scope in desired_integrations.items():
        name = INTEGRATION_NAMES[scope]
        existing = existing_integrations.get(name) or {}
        integration_id = existing.get("_id")
        integration_payload = {
            "type": "webhook-outgoing",
            "username": admin_username,
            "channel": channel_scope,
            "event": "sendMessage",
            "urls": [
                "http://rocket-gateway:8080/__klm_bridge/api/v1/oss-adapters/"
                f"rocketchat/webhooks/{workspace_id}"
            ],
            "token": webhook_token,
            "scriptEnabled": False,
            "scriptEngine": "isolated-vm",
            "runOnEdits": True,
            "retryFailedCalls": True,
            "retryCount": 3,
            "retryDelay": "powers-of-ten",
            "impersonateUser": False,
            "name": name,
            "enabled": True,
        }
        if integration_id:
            integration_payload["integrationId"] = integration_id
            _, updated = request_json(
                "PUT",
                "/api/v1/integrations.update",
                headers=auth_headers,
                payload=integration_payload,
            )
            integration_id = (updated.get("integration") or {}).get("_id") or integration_id
        else:
            _, created = request_json(
                "POST",
                "/api/v1/integrations.create",
                headers=auth_headers,
                payload=integration_payload,
            )
            integration_id = (created.get("integration") or {}).get("_id")
        if integration_id:
            integration_ids[scope] = str(integration_id)

print(
    json.dumps(
        {
            "runtimeVersion": "6.13.1",
            "referenceVersion": "8.6.1",
            "runtimePolicy": "local_compatibility",
            "channels": room_ids,
            "integrationIds": integration_ids,
            "workspaceId": workspace_id or None,
        },
        sort_keys=True,
    )
)
