from unittest.mock import AsyncMock

import pytest

from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.klm_operations_service import KlmOperationsService


@pytest.mark.asyncio
async def test_legal_artifact_revision_uses_advisory_lock_without_update_privilege(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    client = Client(workspace_id=auth_context.workspace_id, name="Cliente lock jurídico")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Caso lock jurídico",
    )
    db_session.add(matter)
    await db_session.flush()

    lock = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.services.klm_operations_service.acquire_transaction_advisory_lock",
        lock,
    )

    artifact = await KlmOperationsService(db_session).create_legal_artifact_version(
        auth_context.workspace_id,
        auth_context.user_id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind="proposal",
            title="Proposta concorrente",
            content={"sections": []},
        ),
    )

    lock.assert_awaited_once_with(
        db_session,
        namespace="legal-artifact-version",
        value=f"{auth_context.workspace_id}:{artifact.artifact_key}",
    )
    assert "FOR UPDATE" not in str(
        KlmOperationsService._latest_legal_artifact_query(
            workspace_id=auth_context.workspace_id,
            artifact_key=artifact.artifact_key,
        )
    )
