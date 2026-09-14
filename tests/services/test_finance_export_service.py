import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.finance_dimensional import FinanceExportJob
from app.models.klm_operations import KlmAuditEvent
from app.models.platform_audit_event import PlatformAuditEvent
from app.services.finance_export_service import (
    FinanceExportService,
    _docx_bytes,
    _pdf_bytes,
    _strip_internal_ids,
    _xlsx_bytes,
    finance_export_audit_snapshot,
    record_finance_export_event,
    record_finance_export_platform_lifecycle,
)
from app.services.platform_export_service import PlatformExportService
from app.workers.celery_app import celery_app


def test_pdf_artifact_has_valid_structure_and_text():
    payload = _pdf_bytes(["Meu Executivo", "Receita: 100"])
    assert payload.startswith(b"%PDF-1.4")
    assert payload.rstrip().endswith(b"%%EOF")
    assert b"xref" in payload
    assert b"Meu Executivo" in payload


def test_pdf_artifact_paginates_without_silently_truncating_rows():
    payload = _pdf_bytes([f"Linha financeira {index}" for index in range(125)])
    assert b"Linha financeira 0" in payload
    assert b"Linha financeira 124" in payload
    assert b"/Count 3" in payload


def test_xlsx_artifact_is_openxml_and_contains_report_rows():
    payload = _xlsx_bytes([("receita", "100"), ("cliente", "Acme")])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "xl/workbook.xml" in archive.namelist()
        sheet = archive.read("xl/worksheets/sheet1.xml")
    assert b"receita" in sheet
    assert b"Acme" in sheet


def test_xlsx_neutralizes_formula_injection():
    payload = _xlsx_bytes([("cliente", '=HYPERLINK("https://evil.invalid")')])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml")
    assert b"<f>" not in sheet
    assert b"'=HYPERLINK" in sheet


def test_xlsx_keeps_all_hierarchy_rows_over_500():
    payload = _xlsx_bytes([(f"drilldown.items.{index}", str(index)) for index in range(650)])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        hierarchy = b"".join(
            archive.read(name) for name in archive.namelist() if name.startswith("xl/worksheets/")
        )
    assert b"drilldown.items.649" in hierarchy


def test_docx_artifact_is_openxml_and_contains_narrative():
    payload = _docx_bytes(["Relatório financeiro", "Resultado positivo"])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "word/document.xml" in archive.namelist()
        document = archive.read("word/document.xml")
    assert "Relatório financeiro".encode() in document
    assert b"Resultado positivo" in document


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("export_format", "signature"),
    [("pdf", b"%PDF-1.4"), ("xlsx", b"PK"), ("docx", b"PK")],
)
async def test_finance_export_run_completes_every_format_in_private_local_storage(
    db_session,
    auth_context,
    monkeypatch,
    tmp_path,
    export_format,
    signature,
):
    """Exercise the worker lifecycle, format dispatch and private storage together."""
    monkeypatch.setattr(
        "app.services.platform_export_service.settings.PLATFORM_EXPORT_STORAGE_DIR",
        str(tmp_path / "private-exports"),
    )

    async def authorized(_service, _job):
        return True

    async def snapshot(_service, _job):
        return {
            "dataQuality": "source_records",
            "kpis": {"receita": "100.00", "despesas": "25.00"},
            "dre": {"result": "75.00"},
            "drilldown": {"items": [{"clientLabel": "Cliente teste", "amount": "100.00"}]},
            "sources": ["ledger"],
        }

    monkeypatch.setattr(FinanceExportService, "_actor_still_authorized", authorized)
    monkeypatch.setattr(FinanceExportService, "_snapshot", snapshot)
    job = FinanceExportJob(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        format=export_format,
        status="queued",
        drill_context={},
    )
    db_session.add(job)
    await db_session.commit()

    await FinanceExportService(db_session).run(job)
    await db_session.refresh(job)

    assert job.status == "completed"
    assert job.progress == 100
    assert job.storage_path is not None
    assert not job.storage_path.startswith("/")
    assert job.storage_path.startswith("platform-exports/finance/")
    artifact = PlatformExportService.local_artifact_path(job.storage_path, must_exist=True)
    assert artifact.read_bytes().startswith(signature)
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert job.checksum_sha256 is not None and len(job.checksum_sha256) == 64


def test_finance_export_audit_never_exposes_private_storage_key():
    job = FinanceExportJob(
        id=uuid4(),
        workspace_id=uuid4(),
        actor_user_id=uuid4(),
        format="pdf",
        status="completed",
        drill_context={},
        storage_path="platform-exports/private/secret-object.pdf",
        progress=100,
        row_count=42,
        checksum_sha256="a" * 64,
    )
    db = MagicMock()
    record_finance_export_event(db, job, "finance_export_completed", before_status="running")
    event = db.add.call_args.args[0]
    assert isinstance(event, KlmAuditEvent)
    assert event.before_data == {"status": "running"}
    assert event.after_data["status"] == "completed"
    assert event.after_data["checksumPresent"] is True
    assert "storage" not in str(event.after_data).lower()
    assert "secret-object" not in str(event.after_data)


def test_export_artifact_strips_internal_identifiers_but_keeps_labels():
    safe = _strip_internal_ids(
        {
            "workspaceId": str(uuid4()),
            "clientLabel": "Cliente A",
            "clientId": str(uuid4()),
            "amount": "100.00",
            "source": "ledger",
        }
    )
    assert safe == {"clientLabel": "Cliente A", "amount": "100.00", "source": "ledger"}


def test_finance_export_tasks_are_registered_for_worker_and_expiry_schedule():
    assert "finance_export_tasks.run" in celery_app.tasks
    assert "finance_export_tasks.expire" in celery_app.tasks
    assert "finance_export_tasks.recover" in celery_app.tasks
    assert (
        celery_app.conf.beat_schedule["expire-finance-exports-hourly"]["task"]
        == "finance_export_tasks.expire"
    )
    assert (
        celery_app.conf.beat_schedule["recover-finance-exports-every-minute"]["task"]
        == "finance_export_tasks.recover"
    )


@pytest.mark.asyncio
async def test_platform_finance_export_worker_revalidates_internal_permission():
    """A Blackbox operator does not need tenant membership to download a requested export."""
    actor = SimpleNamespace(
        is_active=True,
        deleted_at=None,
        is_internal_staff=True,
        platform_access_active=True,
        is_platform_admin=False,
        platform_role_package="finance_billing",
        platform_permission_denials=[],
        platform_permission_grants=[],
    )
    db = SimpleNamespace(get=AsyncMock(return_value=actor), scalar=AsyncMock())
    job = SimpleNamespace(actor_user_id=uuid4(), workspace_id=uuid4())

    assert await FinanceExportService(db)._actor_still_authorized(job) is True
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_lifecycle_writes_sanitized_canonical_finance_audit_event(
    db_session, auth_context
):
    job = FinanceExportJob(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        format="xlsx",
        status="queued",
        drill_context={"accessToken": "must-not-appear"},
        storage_path="platform-exports/private/must-not-appear.xlsx",
    )
    db_session.add(job)
    await db_session.flush()
    before = finance_export_audit_snapshot(job)
    job.status = "running"
    job.progress = 10
    await record_finance_export_platform_lifecycle(db_session, job, action="started", before=before)
    await db_session.commit()

    event_id = await db_session.scalar(
        select(PlatformAuditEvent.id).where(
            PlatformAuditEvent.resource_id == str(job.id),
            PlatformAuditEvent.action == "export.started",
        )
    )
    event = await db_session.get(PlatformAuditEvent, event_id)
    assert event is not None
    assert event.before["status"] == "queued"
    assert event.after["status"] == "running"
    assert "storage" not in str(event.after).lower()
    assert "must-not-appear" not in str(event.before)
