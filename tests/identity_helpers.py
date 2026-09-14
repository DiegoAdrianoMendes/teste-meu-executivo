"""Helpers de teste para o lifecycle real de convites locais."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


def invitation_headers(prefix: str = "invite") -> dict[str, str]:
    return {"Idempotency-Key": f"{prefix}-{uuid4().hex}"}


async def create_team_invitation(
    client,
    *,
    idempotency_key: str | None = None,
    **overrides,
):
    payload = {
        "name": "Atendente Comercial",
        "email": f"atendente-{uuid4().hex[:8]}@example.com",
        "workspaceRole": "operator",
        "permissionGrants": [],
        "permissionDenials": [],
    }
    payload.update(overrides)
    response = await client.post(
        "/api/v1/admin/team-members",
        json=payload,
        headers={"Idempotency-Key": idempotency_key}
        if idempotency_key
        else invitation_headers(),
    )
    return response, payload


async def accept_team_invitation(
    client,
    invitation: dict,
    *,
    password: str = "SenhaLocal#2026",
) -> dict:
    delivery = await client.get(
        f"/api/v1/auth/local-outbox/{invitation['invitationDeliveryId']}"
    )
    assert delivery.status_code == 200, delivery.text
    query = parse_qs(urlsplit(delivery.json()["link"]).query)
    token = query["token"][0]

    accepted = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json={"token": token, "password": password},
        headers=invitation_headers("accept"),
    )
    assert accepted.status_code == 200, accepted.text

    listed = await client.get("/api/v1/admin/team-members")
    assert listed.status_code == 200, listed.text
    return next(
        member
        for member in listed.json()
        if member["membershipId"] == invitation["membershipId"]
    )


async def invite_and_accept_team_member(
    client,
    *,
    password: str = "SenhaLocal#2026",
    **overrides,
):
    response, payload = await create_team_invitation(client, **overrides)
    assert response.status_code == 202, response.text
    invitation = response.json()
    assert invitation["status"] == "invited"
    member = await accept_team_invitation(client, invitation, password=password)
    assert member["status"] == "active"
    return member, {**payload, "password": password}
