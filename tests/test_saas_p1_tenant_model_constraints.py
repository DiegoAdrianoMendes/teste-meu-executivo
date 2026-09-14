from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.base import Base
from app.models.client import Client
from app.models.matter import Matter
from app.models.storage_object import StorageObject
from app.models.workspace import Workspace


@pytest.fixture
def constrained_session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _workspace(slug: str) -> Workspace:
    return Workspace(name=f"Workspace {slug}", slug=slug)


def _storage_object(*, workspace_id, matter_id, client_id, object_id=None) -> StorageObject:
    object_id = object_id or uuid4()
    return StorageObject(
        workspace_id=workspace_id,
        object_id=object_id,
        version=1,
        matter_id=matter_id,
        client_id=client_id,
        original_filename="tenant-bound.pdf",
        declared_content_type="application/pdf",
        storage_key=f"tenants/{workspace_id}/objects/{object_id}/1/payload",
    )


def test_matter_client_reference_is_rejected_across_workspaces(
    constrained_session: Session,
) -> None:
    workspace_a = _workspace("tenant-model-a")
    workspace_b = _workspace("tenant-model-b")
    constrained_session.add_all((workspace_a, workspace_b))
    constrained_session.flush()
    client_a = Client(workspace_id=workspace_a.id, name="Cliente A")
    client_b = Client(workspace_id=workspace_b.id, name="Cliente B")
    constrained_session.add_all((client_a, client_b))
    constrained_session.commit()

    constrained_session.add(
        Matter(
            workspace_id=workspace_a.id,
            client_id=client_b.id,
            demand_type="Referência cruzada",
        )
    )
    with pytest.raises(IntegrityError):
        constrained_session.flush()


def test_storage_references_are_rejected_across_workspaces(
    constrained_session: Session,
) -> None:
    workspace_a = _workspace("storage-model-a")
    workspace_b = _workspace("storage-model-b")
    constrained_session.add_all((workspace_a, workspace_b))
    constrained_session.flush()
    client_a = Client(workspace_id=workspace_a.id, name="Cliente Storage A")
    client_b = Client(workspace_id=workspace_b.id, name="Cliente Storage B")
    constrained_session.add_all((client_a, client_b))
    constrained_session.flush()
    matter_a = Matter(
        workspace_id=workspace_a.id,
        client_id=client_a.id,
        demand_type="Caso Storage A",
    )
    matter_b = Matter(
        workspace_id=workspace_b.id,
        client_id=client_b.id,
        demand_type="Caso Storage B",
    )
    constrained_session.add_all((matter_a, matter_b))
    constrained_session.commit()

    constrained_session.add(
        _storage_object(
            workspace_id=workspace_a.id,
            matter_id=matter_b.id,
            client_id=client_b.id,
        )
    )
    with pytest.raises(IntegrityError):
        constrained_session.flush()

    constrained_session.rollback()
    constrained_session.add(
        _storage_object(
            workspace_id=workspace_a.id,
            matter_id=matter_a.id,
            client_id=client_b.id,
        )
    )
    with pytest.raises(IntegrityError):
        constrained_session.flush()


def test_storage_object_version_is_globally_unique_across_workspaces(
    constrained_session: Session,
) -> None:
    workspace_a = _workspace("storage-version-a")
    workspace_b = _workspace("storage-version-b")
    constrained_session.add_all((workspace_a, workspace_b))
    constrained_session.flush()
    shared_object_id = uuid4()
    constrained_session.add(
        _storage_object(
            workspace_id=workspace_a.id,
            matter_id=None,
            client_id=None,
            object_id=shared_object_id,
        )
    )
    constrained_session.commit()

    constrained_session.add(
        _storage_object(
            workspace_id=workspace_b.id,
            matter_id=None,
            client_id=None,
            object_id=shared_object_id,
        )
    )
    with pytest.raises(IntegrityError):
        constrained_session.flush()
