from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.klm_operations import KlmAuditEvent
from app.models.task_external_share import TaskExternalShare
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _unrelated_workspace_owner(
    db: AsyncSession, auth: AuthContext
) -> WorkspaceMembership:
    user = User(
        name="Gestor sem vínculo com a tarefa compartilhada",
        email=f"task-share-outsider-{uuid4().hex[:8]}@example.com",
        initials="SV",
        color="#475569",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    membership = WorkspaceMembership(
        workspace_id=auth.workspace_id,
        user_id=user.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
    )
    db.add(membership)
    await db.commit()
    return membership


@pytest.mark.asyncio
async def test_external_task_share_is_previewed_hash_only_minimal_and_revocable(
    client,
    unauthenticated_client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    created = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Prazo de contestação",
            "description": "SEGREDO INTERNO que não pode sair",
            "context": {"type": "workspace"},
            "origin": "administrative",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    expires_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()

    preview = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares/preview",
        json={"expiresAt": expires_at, "safeContext": "  Comparecer ao fórum.  "},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["title"] == "Prazo de contestação"
    assert preview_body["safeContext"] == "Comparecer ao fórum."
    assert "comentários" in preview_body["excludedContent"]
    assert "SEGREDO INTERNO" not in preview.text

    stale = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares",
        headers={"Idempotency-Key": "task-share-stale-preview"},
        json={
            "expiresAt": expires_at,
            "safeContext": "mudou",
            "previewFingerprint": preview_body["previewFingerprint"],
        },
    )
    assert stale.status_code == 409

    issued = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares",
        headers={"Idempotency-Key": "task-share-stable-retry"},
        json={
            "expiresAt": expires_at,
            "safeContext": "Comparecer ao fórum.",
            "previewFingerprint": preview_body["previewFingerprint"],
        },
    )
    assert issued.status_code == 201, issued.text
    issued_body = issued.json()
    assert "/task-share#token=" in issued_body["url"]

    persisted = await db_session.scalar(select(TaskExternalShare))
    assert persisted is not None
    assert len(persisted.token_hash) == 64
    assert persisted.token_hash not in issued_body["url"]
    assert not hasattr(persisted, "token")
    assert len(persisted.idempotency_key_hash) == 64

    replay = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares",
        headers={"Idempotency-Key": "task-share-stable-retry"},
        json={
            "expiresAt": expires_at,
            "safeContext": "Comparecer ao fórum.",
            "previewFingerprint": preview_body["previewFingerprint"],
        },
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "TASK_SHARE_IDEMPOTENT_REPLAY"
    assert "url" not in replay.json()["detail"]
    assert issued_body["url"].split("#token=", 1)[-1] not in replay.text
    assert len((await db_session.scalars(select(TaskExternalShare))).all()) == 1

    raw_token = issued_body["url"].split("#token=", 1)[-1]
    redeemed = await unauthenticated_client.post(
        "/api/v1/public/task-shares/redeem",
        headers={"X-Task-Share-Token": raw_token},
    )
    assert redeemed.status_code == 200, redeemed.text
    redeemed_body = redeemed.json()
    assert redeemed_body | {"expiresAt": None} == {
        "title": "Prazo de contestação",
        "status": "pending",
        "dueAt": None,
        "safeContext": "Comparecer ao fórum.",
        "expiresAt": None,
    }
    assert datetime.fromisoformat(
        redeemed_body["expiresAt"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(issued_body["expiresAt"].replace("Z", "+00:00"))
    for forbidden in ("description", "comments", "events", "participants", "history"):
        assert forbidden not in redeemed.json()
    assert "SEGREDO INTERNO" not in redeemed.text

    revoked = await client.delete(f"/api/v1/tasks/{task_id}/external-shares/{issued_body['id']}")
    assert revoked.status_code == 200, revoked.text
    denied = await unauthenticated_client.post(
        "/api/v1/public/task-shares/redeem",
        headers={"X-Task-Share-Token": raw_token},
    )
    assert denied.status_code == 404

    event_types = set((await db_session.scalars(select(KlmAuditEvent.event_type))).all())
    assert {
        "task_external_share_created",
        "task_external_share_redeemed",
        "task_external_share_revoked",
    }.issubset(event_types)


@pytest.mark.asyncio
async def test_external_task_share_rejects_expired_or_overlong_validity(
    client,
    auth_context: AuthContext,
) -> None:
    created = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Tarefa jurídica",
            "context": {"type": "workspace"},
            "origin": "administrative",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    task_id = created.json()["id"]
    for invalid in (
        datetime.now(UTC) - timedelta(minutes=1),
        datetime.now(UTC) + timedelta(days=31),
    ):
        response = await client.post(
            f"/api/v1/tasks/{task_id}/external-shares/preview",
            json={"expiresAt": invalid.isoformat()},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_task_editor_without_message_permissions_cannot_prepare_whatsapp_share(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    created = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Tarefa editável sem acesso a mensagens",
            "context": {"type": "workspace"},
            "origin": "administrative",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.permission_denials = [
        {"module": "mensagens", "action": "view", "scope": "workspace"},
        {"module": "mensagens", "action": "send_message", "scope": "workspace"},
    ]
    await db_session.flush()

    # Prova que tasks:edit continua ativo apesar da restrição de Mensagens.
    task_preview = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares/preview",
        json={"expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    )
    assert task_preview.status_code == 200, task_preview.text

    draft_payload = {
        "contactRef": "opaque-contact-reference-with-sufficient-length",
        "shareId": "opaque-share-reference",
        "shareUrl": "https://app.example/task-share#token=" + ("x" * 80),
        "messageBody": "Texto com https://app.example/task-share#token=" + ("x" * 80),
    }
    contacts = await client.get(
        f"/api/v1/tasks/{task_id}/external-shares/whatsapp/contacts"
    )
    preview = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares/whatsapp/preview",
        json=draft_payload,
    )
    for response in (contacts, preview):
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "AUTH-012"

    # Com leitura restaurada, o write continua negado especificamente por send_message.
    membership.permission_denials = [
        {"module": "mensagens", "action": "send_message", "scope": "workspace"}
    ]
    await db_session.flush()
    prepared = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares/whatsapp/draft",
        headers={"Idempotency-Key": "task-whatsapp-rbac-denied"},
        json={
            **draft_payload,
            "previewFingerprint": "a" * 64,
            "confirmed": True,
        },
    )
    assert prepared.status_code == 403, prepared.text
    assert prepared.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_known_idempotency_key_cannot_reveal_share_for_hidden_task(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    replay_actor = await _unrelated_workspace_owner(db_session, auth_context)
    created = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Tarefa sigilosa com link",
            "context": {"type": "workspace"},
            "origin": "administrative",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
                {"userId": str(replay_actor.user_id), "role": "observer"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    expires_at = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    preview = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares/preview",
        json={"expiresAt": expires_at, "safeContext": "Contexto autorizado."},
    )
    assert preview.status_code == 200, preview.text
    request_body = {
        "expiresAt": expires_at,
        "safeContext": "Contexto autorizado.",
        "previewFingerprint": preview.json()["previewFingerprint"],
    }
    key = "known-hidden-task-share-key"
    issued = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares",
        headers={"Idempotency-Key": key},
        json=request_body,
    )
    assert issued.status_code == 201, issued.text

    original_user_id = auth_context.user_id
    original_membership_id = auth_context.membership_id
    auth_context.user_id = replay_actor.user_id
    auth_context.membership_id = replay_actor.id
    try:
        actor_conflict = await client.post(
            f"/api/v1/tasks/{task_id}/external-shares",
            headers={"Idempotency-Key": key},
            json=request_body,
        )
    finally:
        auth_context.user_id = original_user_id
        auth_context.membership_id = original_membership_id
    assert actor_conflict.status_code == 409
    assert actor_conflict.json()["detail"]["code"] == "TASK_SHARE_IDEMPOTENCY_CONFLICT"
    assert "shareId" not in actor_conflict.text
    assert "expiresAt" not in actor_conflict.text

    outsider = await _unrelated_workspace_owner(db_session, auth_context)
    auth_context.user_id = outsider.user_id
    auth_context.membership_id = outsider.id
    try:
        denied = await client.post(
            f"/api/v1/tasks/{task_id}/external-shares",
            headers={"Idempotency-Key": key},
            json=request_body,
        )
    finally:
        auth_context.user_id = original_user_id
        auth_context.membership_id = original_membership_id

    assert denied.status_code == 404
    assert denied.json() == {"detail": "Tarefa não encontrada."}
    assert "shareId" not in denied.text
    assert "expiresAt" not in denied.text
    assert issued.json()["id"] not in denied.text

    changed_preview = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares/preview",
        json={"expiresAt": expires_at, "safeContext": "Outro contexto."},
    )
    conflict = await client.post(
        f"/api/v1/tasks/{task_id}/external-shares",
        headers={"Idempotency-Key": key},
        json={
            "expiresAt": expires_at,
            "safeContext": "Outro contexto.",
            "previewFingerprint": changed_preview.json()["previewFingerprint"],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "TASK_SHARE_IDEMPOTENCY_CONFLICT",
        "message": "Idempotency-Key já utilizada para outra solicitação.",
    }
