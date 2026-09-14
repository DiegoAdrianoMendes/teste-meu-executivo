from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.append_only import ImmutableRecordError
from app.db.base import Base
from app.models.client import Client
from app.models.klm_operations import (
    KlmAuditEvent,
    LegalArtifactState,
    LegalArtifactVersion,
    legal_artifact_evidence_sha256,
)
from app.models.matter import Matter
from app.models.oss_function_unit import (
    OSSFunctionUnitEvent,
    OSSFunctionUnitEventStatus,
)
from app.models.oss_native import (
    OSSNativeAuditAction,
    OSSNativeAuditEvent,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite:///:memory:")

    @sa.event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _seed_evidence(session: Session) -> dict[type, object]:
    workspace = Workspace(
        name="Evidence Workspace",
        slug=f"evidence-{uuid4().hex[:12]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    user = User(
        name="Evidence User",
        email=f"evidence-{uuid4().hex[:12]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EU",
        color="#111111",
        is_active=True,
        is_internal_staff=False,
    )
    session.add_all((workspace, user))
    session.flush()
    client = Client(workspace_id=workspace.id, name="Evidence Client")
    session.add(client)
    session.flush()
    matter = Matter(
        workspace_id=workspace.id,
        client_id=client.id,
        demand_type="Evidence integrity",
    )
    session.add(matter)
    session.flush()

    klm_event = KlmAuditEvent(
        workspace_id=workspace.id,
        actor_type="system",
        actor_label="Evidence test",
        event_type="evidence.created",
        entity_type="matter",
        entity_id=matter.id,
        summary="KLM evidence",
    )
    oss_event = OSSNativeAuditEvent(
        workspace_id=workspace.id,
        provider="chatwoot",
        capability="audit-log",
        action=OSSNativeAuditAction.command_succeeded,
        summary="OSS evidence",
        audit_metadata={},
    )
    function_event = OSSFunctionUnitEvent(
        workspace_id=workspace.id,
        unit_id="chatwoot:audit-log:record_event",
        contract_id="c" * 64,
        provider="chatwoot",
        capability="audit-log",
        semantic_profile="audit_event",
        operation="record_event",
        idempotency_key="append-only-evidence",
        resource_id="matter-evidence",
        payload={},
        result={},
        status=OSSFunctionUnitEventStatus.succeeded,
        external_network_executed=False,
    )
    artifact = LegalArtifactVersion(
        workspace_id=workspace.id,
        matter_id=matter.id,
        artifact_key=uuid4(),
        version=1,
        kind="proposal",
        title="Canonical evidence",
        content={"sections": [{"body": "immutable"}]},
        source_snapshot={"matterId": str(matter.id)},
        created_by_user_id=user.id,
        evidence_schema_version=1,
        evidence_sha256="",
        state=LegalArtifactState(
            workspace_id=workspace.id,
            matter_id=matter.id,
            status="draft",
        ),
    )
    session.add_all((klm_event, oss_event, function_event, artifact))
    session.commit()
    return {
        KlmAuditEvent: klm_event.id,
        OSSNativeAuditEvent: oss_event.id,
        OSSFunctionUnitEvent: function_event.id,
        LegalArtifactVersion: artifact.id,
    }


@pytest.mark.parametrize(
    ("model", "attribute", "replacement"),
    (
        (KlmAuditEvent, "summary", "tampered KLM evidence"),
        (OSSNativeAuditEvent, "summary", "tampered OSS evidence"),
        (OSSFunctionUnitEvent, "operation", "tampered_operation"),
        (LegalArtifactVersion, "title", "Tampered legal evidence"),
    ),
)
def test_orm_and_sqlite_reject_update_and_delete(
    model,
    attribute: str,
    replacement: str,
) -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as session:
        identifiers = _seed_evidence(session)
        record = session.get(model, identifiers[model])
        setattr(record, attribute, replacement)
        with pytest.raises(ImmutableRecordError, match="immutable"):
            session.flush()
        session.rollback()

    with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text(
                    f'UPDATE "{model.__tablename__}" SET id = id WHERE id = :record_id'
                ),
                {"record_id": str(identifiers[model])},
            )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text(f'DELETE FROM "{model.__tablename__}" WHERE id = :record_id'),
                {"record_id": str(identifiers[model])},
            )
    engine.dispose()


def test_legal_artifact_digest_and_mutable_state_are_separate() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as session:
        identifiers = _seed_evidence(session)
        artifact = session.get(LegalArtifactVersion, identifiers[LegalArtifactVersion])
        assert artifact.evidence_sha256 == legal_artifact_evidence_sha256(
            **artifact.evidence_values()
        )
        original_digest = artifact.evidence_sha256
        artifact.state.status = "review_pending"
        session.commit()
        assert artifact.status == "review_pending"
        assert artifact.evidence_sha256 == original_digest
    engine.dispose()


def test_native_approval_consumption_is_unique_only_for_consumed_event() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as session:
        identifiers = _seed_evidence(session)
        seed = session.get(KlmAuditEvent, identifiers[KlmAuditEvent])
        common = {
            "workspace_id": seed.workspace_id,
            "actor_type": "system",
            "actor_label": "Native approval runtime",
            "entity_type": "native_capability_approval",
            "entity_id": uuid4(),
            "summary": "Approval consumed",
        }
        first = KlmAuditEvent(
            event_type="native_capability_human_approval_consumed",
            **common,
        )
        session.add(first)
        session.commit()

        session.add(
            KlmAuditEvent(
                event_type="native_capability_human_approval_consumed",
                **common,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            KlmAuditEvent(
                event_type="native_capability_human_approval_requested",
                **common,
            )
        )
        session.commit()

        session.add(
            KlmAuditEvent(
                event_type="native_capability_human_approval_consumed",
                workspace_id=seed.workspace_id,
                actor_type="system",
                actor_label="Native approval runtime",
                entity_type="native_capability_approval",
                entity_id=None,
                summary="Invalid approval consumption",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()
