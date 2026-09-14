#!/usr/bin/env python3
"""Create and verify the single local KLM ChatterMate organization."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://chattermate-api:8000/api/v1"


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required local bootstrap variable: {name}")
    return value


def request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    form: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, dict[str, object]]:
    body = None
    content_type = "application/json"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    elif form is not None:
        body = urlencode(form).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": content_type},
    )
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except HTTPError as error:
        status = error.code
        raw = error.read()
    try:
        decoded = json.loads(raw or b"{}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"ChatterMate returned invalid JSON for {path}: HTTP {status}") from error
    if not isinstance(decoded, dict):
        raise SystemExit(f"ChatterMate returned an invalid contract for {path}")
    if status not in expected:
        detail = decoded.get("detail") or decoded
        raise SystemExit(f"ChatterMate request failed for {path}: HTTP {status}: {detail}")
    return status, decoded


admin_name = required("KLM_OSS_CHATTERMATE_ADMIN_NAME")
admin_email = required("KLM_OSS_CHATTERMATE_ADMIN_EMAIL")
admin_password = required("KLM_OSS_CHATTERMATE_ADMIN_PASSWORD")

_, setup = request_json("GET", "/organizations/setup-status")
created = not bool(setup.get("is_setup"))
if created:
    _, organization = request_json(
        "POST",
        "/organizations",
        payload={
            "name": "KLM Advogados",
            "domain": "klm.local",
            "timezone": "America/Sao_Paulo",
            "admin_email": admin_email,
            "admin_name": admin_name,
            "admin_password": admin_password,
            "settings": {"runtime": "klm-local", "source": "chattermate-public"},
        },
        expected=(201,),
    )
    organization_id = str(organization.get("id") or "")
else:
    organization_id = "existing"

_, login = request_json(
    "POST",
    "/users/login",
    form={"username": admin_email, "password": admin_password},
)
user = login.get("user") or {}
if not isinstance(user, dict) or user.get("email") != admin_email:
    raise SystemExit("ChatterMate bootstrap login returned an unexpected administrator")

print(
    json.dumps(
        {
            "created": created,
            "organizationId": organization_id,
            "adminVerified": True,
            "version": "1.0.14",
        },
        sort_keys=True,
    )
)
