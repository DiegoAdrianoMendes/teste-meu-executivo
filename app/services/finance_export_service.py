"""Generate private, reproducible financial exports without optional runtime deps."""
# Long XML/PDF literals below are intentionally kept contiguous for archive validity.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import io
import logging
import tempfile
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy import select

from app.core.config import settings
from app.core.logging_privacy import safe_error_type
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.finance_dimensional import (
    FinanceBudgetLine,
    FinanceExportJob,
    FinanceForecast,
    FinanceTaxProfile,
    FinanceValuation,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.finance_dimensional_service import FinanceDimensionalService
from app.services.finance_executive_service import FinanceExecutiveService
from app.services.finance_reports_service import FinanceReportsService
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_export_service import EXPORT_MEDIA_TYPES, PlatformExportService

PRIVATE_EXPORT_ROOT = Path("/tmp/meu-executivo-finance-exports")
logger = logging.getLogger(__name__)
_FINANCE_ROLES = {
    WorkspaceRole.workspace_owner,
    WorkspaceRole.workspace_admin,
    WorkspaceRole.finance_manager,
}


def finance_export_audit_snapshot(job: FinanceExportJob) -> dict[str, Any]:
    """Return the operator-safe lifecycle state; never include object storage paths."""
    return {
        "workspaceId": str(job.workspace_id),
        "format": str(getattr(job.format, "value", job.format)),
        "status": str(getattr(job.status, "value", job.status)),
        "progress": job.progress,
        "rowCount": job.row_count,
        "checksumPresent": bool(job.checksum_sha256),
        "expiresAt": job.expires_at,
        "cancelRequestedAt": job.cancel_requested_at,
        "errorCode": job.error_detail,
    }


async def record_finance_export_platform_lifecycle(
    db,
    job: FinanceExportJob,
    *,
    action: str,
    before: dict[str, Any] | None,
    result: str = "success",
    error_code: str | None = None,
) -> None:
    """Append a canonical audit event for durable worker-side transitions.

    The request audit covers creation/cancellation. Worker transitions do not
    have a request context, so they must write their own canonical event in
    the same transaction as the export state change.
    """
    actor = await db.get(User, job.actor_user_id)
    await PlatformAuditEventService(db).record(
        PlatformAuditEventCreate(
            actor_id=job.actor_user_id if actor is not None else None,
            actor_role_snapshot=(
                str(getattr(actor, "platform_role_package", "") or "system")
            ),
            is_platform_root=is_platform_root(actor),
            workspace_id=job.workspace_id,
            session_type="system",
            module="finance",
            action=f"export.{action}",
            resource_type="finance_export_job",
            resource_id=str(job.id),
            resource_label_snapshot=f"Exportação financeira {str(job.format).upper()}",
            result=result,
            before=before,
            after=finance_export_audit_snapshot(job),
            reason=f"Lifecycle de exportação financeira: {action}.",
            reason_source="system",
            correlation_id=f"finance-export:{job.id}",
            operation_id=f"finance-export:{job.id}",
            error_code=error_code,
            route="worker:finance_export",
            method="WORKER",
        )
    )


def record_finance_export_event(
    db,
    job: FinanceExportJob,
    event_type: str,
    *,
    before_status: str | None = None,
    error_code: str | None = None,
) -> None:
    """Append an export lifecycle event without exposing its private object key."""
    db.add(
        KlmAuditEvent(
            workspace_id=job.workspace_id,
            actor_user_id=job.actor_user_id,
            actor_type="human"
            if event_type
            in {"finance_export_requested", "finance_export_cancelled", "finance_export_downloaded"}
            else "system",
            actor_label="Finance export",
            event_type=event_type,
            entity_type="finance_export_job",
            entity_id=job.id,
            summary=f"Exportação financeira: {event_type.removeprefix('finance_export_')}.",
            correlation_id=f"finance-export:{job.id}",
            before_data={"status": before_status} if before_status is not None else None,
            after_data={
                "status": str(getattr(job.status, "value", job.status)),
                "format": str(getattr(job.format, "value", job.format)),
                "progress": job.progress,
                "rowCount": job.row_count,
                "checksumPresent": bool(job.checksum_sha256),
                "expiresAt": job.expires_at.isoformat() if job.expires_at else None,
                "errorCode": error_code,
            },
        )
    )


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return value


def _strip_internal_ids(value: Any) -> Any:
    """Remove internal UUIDs from operator-facing export artifacts."""
    if isinstance(value, dict):
        return {
            key: _strip_internal_ids(item)
            for key, item in value.items()
            if str(key).lower() not in {"id", "workspaceid", "actoruserid"}
            and not str(key).lower().endswith("id")
        }
    if isinstance(value, list):
        return [_strip_internal_ids(item) for item in value]
    return value


def _flatten(prefix: str, value: Any, rows: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, rows)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(f"{prefix}[{index}]", item, rows)
    else:
        rows.append((prefix, "" if value is None else str(value)))


def _safe_spreadsheet_text(value: str) -> str:
    """Prevent exported text from becoming a spreadsheet formula."""
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _pdf_bytes(lines: list[str]) -> bytes:
    """Build a complete paginated PDF with embedded text and a valid xref."""
    safe_lines: list[str] = []
    for line in lines:
        encoded = line.encode("latin-1", "replace").decode("latin-1")
        safe_lines.append(encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))
    chunks = [safe_lines[index : index + 50] for index in range(0, len(safe_lines), 50)] or [[]]
    font_id = 3 + (len(chunks) * 2)
    page_ids = [3 + index * 2 for index in range(len(chunks))]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{value} 0 R' for value in page_ids)}] /Count {len(chunks)} >>".encode(),
    ]
    for index, chunk in enumerate(chunks):
        page_id = page_ids[index]
        content_id = page_id + 1
        commands = ["BT", "/F1 9 Tf", "36 806 Td", "14 TL"]
        for line_index, line in enumerate(chunk):
            if line_index:
                commands.append("T*")
            commands.append(f"({line}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
        objects.extend(
            [
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(),
                f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
            ]
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def _xlsx_bytes(rows: list[tuple[str, str]]) -> bytes:
    try:
        from openpyxl import Workbook

        wb = Workbook()
        wb.remove(wb.active)
        grouped = {
            "Resumo": rows[:20],
            # Keep every drill-down row.  The snapshot is already paged to
            # completion; truncating here silently produced incomplete exports.
            "Hierarquia": [(k, v) for k, v in rows if "drilldown" in k],
            "Registros": rows,
            "Premissas": [(k, v) for k, v in rows if "assumption" in k.lower()],
            "Fontes": [(k, v) for k, v in rows if "source" in k.lower()],
        }
        for name, values in grouped.items():
            ws = wb.create_sheet(name)
            ws.append(["Campo", "Valor"])
            for key, value in values or [("status", "ausente")]:
                ws.append([_safe_spreadsheet_text(key), _safe_spreadsheet_text(value)])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    except ImportError:
        pass
    sheet_rows = []
    for row_number, (key, value) in enumerate([("Campo", "Valor"), *rows], start=1):
        key = _safe_spreadsheet_text(key)
        value = _safe_spreadsheet_text(value)
        sheet_rows.append(
            f'<row r="{row_number}"><c r="A{row_number}" t="inlineStr"><is><t>{escape(key)}</t></is></c>'
            f'<c r="B{row_number}" t="inlineStr"><is><t>{escape(value)}</t></is></c></row>'
        )
    content_types = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
    root_rels = b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    workbook = b'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Relatorio" sheetId="1" r:id="rId1"/></sheets></workbook>'
    workbook_rels = b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    sheet = (
        '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _docx_bytes(lines: list[str]) -> bytes:
    paragraphs = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>' for line in lines
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + paragraphs
        + "<w:sectPr/></w:body></w:document>"
    ).encode()
    content_types = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    root_rels = b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _chart_lines(snapshot: dict[str, Any]) -> list[str]:
    """Render a deterministic, data-backed chart legend for PDF/DOCX.

    The lightweight exporter intentionally has no provider dependency.  These
    bars are derived from the same KPI snapshot as the tables and make the
    visual report useful even when optional chart packages are unavailable.
    """
    kpis = snapshot.get("kpis") if isinstance(snapshot.get("kpis"), dict) else {}
    if not any(key in kpis for key in ("receita", "despesas", "resultado", "mrr")):
        dre = snapshot.get("dre") if isinstance(snapshot.get("dre"), dict) else {}
        kpis = {"resultado": dre.get("result")}
        lines = dre.get("lines")
        if isinstance(lines, list):
            for line in lines:
                if not isinstance(line, dict):
                    continue
                key = str(line.get("key", line.get("label", ""))).lower()
                if "receita" in key:
                    kpis["receita"] = line.get("amount", line.get("value"))
                elif "despesa" in key:
                    kpis["despesas"] = line.get("amount", line.get("value"))
    candidates = []
    for key in ("receita", "despesas", "resultado", "mrr"):
        value = kpis.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        try:
            candidates.append((key, float(value)))
        except (TypeError, ValueError):
            continue
    if not candidates:
        return ["Gráfico: dados insuficientes para visualização."]
    maximum = max(abs(value) for _, value in candidates) or 1.0
    lines = ["Gráfico — principais indicadores (escala relativa)"]
    for key, value in candidates:
        width = min(40, max(1, round(abs(value) / maximum * 40)))
        lines.append(f"{key}: {'█' * width} {value:,.2f}")
    return lines


class FinanceExportService:
    def __init__(self, db):
        self.db = db

    async def _actor_still_authorized(self, job: FinanceExportJob) -> bool:
        actor = await self.db.get(User, job.actor_user_id)
        if actor is None or not actor.is_active:
            return False
        if actor.platform_role_package == "platform_owner":
            return True
        # Platform exports are initiated by internal staff through the
        # Blackbox endpoint, where tenant membership is neither required nor
        # desirable. Re-check the same capability here because a permission
        # may have been revoked between request and worker execution.
        if getattr(actor, "is_internal_staff", False) and has_platform_permission(
            actor, "finance", "export"
        ):
            return True
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == job.workspace_id,
                WorkspaceMembership.user_id == job.actor_user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                WorkspaceMembership.role.in_(_FINANCE_ROLES),
            )
        )
        return membership is not None

    async def _snapshot(self, job: FinanceExportJob) -> dict[str, Any]:
        now = datetime.now(UTC)
        context = dict(job.drill_context or {})
        horizon = min(365, max(1, int(context.get("horizonDays") or 90)))
        start = (
            date.fromisoformat(str(context.get("periodStart")))
            if context.get("periodStart")
            else date(now.year, now.month, 1)
        )
        end = (
            date.fromisoformat(str(context.get("periodEnd")))
            if context.get("periodEnd")
            else now.date()
        )
        if end < start:
            raise ValueError("finance export period is invalid")
        reports = FinanceReportsService(self.db)
        dimensional = FinanceDimensionalService(self.db)
        # camelCase is the public persisted contract. Snake_case remains a
        # defensive compatibility input for pre-release callers only.
        context_ids = {
            "costCenterId": context.get("costCenterId") or context.get("cost_center_id"),
            "clientId": context.get("clientId") or context.get("client_id"),
            "contractId": context.get("contractId") or context.get("contract_id"),
            "matterId": context.get("matterId") or context.get("matter_id"),
        }
        drill_args = {
            "cost_center_id": context_ids["costCenterId"],
            "client_id": context_ids["clientId"],
            "contract_id": context_ids["contractId"],
            "matter_id": context_ids["matterId"],
            "limit": 200,
            "offset": 0,
        }
        for key in ("cost_center_id", "client_id", "contract_id", "matter_id"):
            value = drill_args[key]
            if isinstance(value, str) and value:
                from uuid import UUID

                drill_args[key] = UUID(value)
            elif not value:
                drill_args[key] = None
        drill = await dimensional.drilldown(job.workspace_id, **drill_args)
        # Export the complete drill-down, paging through the service rather than
        # silently truncating at the UI page size.
        all_items = list(drill.get("items", []))
        while drill.get("hasMore"):
            drill_args["offset"] += drill_args["limit"]
            drill = await dimensional.drilldown(job.workspace_id, **drill_args)
            all_items.extend(drill.get("items", []))
        drill["items"] = all_items
        drill["hasMore"] = False
        drill["totalCount"] = len(all_items)
        contextual = any(
            drill_args[key] is not None
            for key in ("cost_center_id", "client_id", "contract_id", "matter_id")
        )
        safe_filter_context = {
            "scope": "dimensional" if contextual else "workspace_consolidated",
            "dimensions": [
                name
                for name, key in (
                    ("cost_center", "costCenterId"),
                    ("client", "clientId"),
                    ("contract", "contractId"),
                    ("matter", "matterId"),
                )
                if context_ids.get(key)
            ],
        }
        scoped_dre = await dimensional.dre(
            job.workspace_id,
            mode=str(context.get("dreMode") or "accrual"),
            period_start=start,
            period_end=end,
            cost_center_id=drill_args["cost_center_id"],
            client_id=drill_args["client_id"],
            contract_id=drill_args["contract_id"],
            matter_id=drill_args["matter_id"],
        )
        executive = (
            await FinanceExecutiveService(self.db).analytics(job.workspace_id, as_of=now.date())
            if not contextual
            else {
                "status": "not_applicable_to_dimensional_scope",
                "reason": "Métricas executivas consolidadas foram omitidas do recorte filtrado.",
            }
        )
        forecasts = [
            {
                "id": item.id,
                "scenario": item.scenario,
                "horizon": item.horizon,
                "periodStart": item.period_start,
                "periodEnd": item.period_end,
                "revenue": item.revenue,
                "expense": item.expense,
                "assumptions": item.assumptions,
                "version": item.version,
            }
            for item in (
                await self.db.scalars(
                    select(FinanceForecast)
                    .where(FinanceForecast.workspace_id == job.workspace_id)
                    .order_by(FinanceForecast.created_at.desc(), FinanceForecast.id)
                )
            ).all()
        ]
        valuations = [
            {
                "id": item.id,
                "method": item.method,
                "valuationDate": item.valuation_date,
                "enterpriseValue": item.enterprise_value,
                "assumptions": item.assumptions,
                "premiseQuality": item.premise_quality,
                "version": item.version,
            }
            for item in (
                await self.db.scalars(
                    select(FinanceValuation)
                    .where(FinanceValuation.workspace_id == job.workspace_id)
                    .order_by(FinanceValuation.valuation_date.desc(), FinanceValuation.id)
                )
            ).all()
        ]
        budget_lines = [
            {
                "id": item.id,
                "periodStart": item.period_start,
                "periodEnd": item.period_end,
                "costCenterId": item.cost_center_id,
                "category": item.category,
                "entryType": item.entry_type,
                "amount": item.amount,
                "assumptions": item.assumptions,
                "status": item.status,
                "version": item.version,
            }
            for item in (
                await self.db.scalars(
                    select(FinanceBudgetLine)
                    .where(
                        FinanceBudgetLine.workspace_id == job.workspace_id,
                        FinanceBudgetLine.status == "active",
                        FinanceBudgetLine.period_end >= start,
                        FinanceBudgetLine.period_start <= end,
                        *(
                            [FinanceBudgetLine.cost_center_id == drill_args["cost_center_id"]]
                            if drill_args["cost_center_id"] is not None
                            else []
                        ),
                    )
                    .order_by(
                        FinanceBudgetLine.period_start,
                        FinanceBudgetLine.entry_type,
                        FinanceBudgetLine.id,
                    )
                )
            ).all()
        ]
        tax_profile = await self.db.scalar(
            select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == job.workspace_id)
        )
        tax_simulation = (
            await dimensional.simulate_tax_reform(
                job.workspace_id, as_of=now.date(), horizon_years=8
            )
            if not contextual
            else {
                "status": "not_applicable_to_dimensional_scope",
                "reason": "Simulação tributária consolidada omitida do recorte filtrado.",
            }
        )
        insights = []
        if (
            not contextual
            and executive.concentration.value is not None
            and Decimal(str(executive.concentration.value)) >= Decimal("30")
        ):
            insights.append(
                {
                    "code": "client_concentration",
                    "severity": "high",
                    "message": "Concentração relevante de receita em um único cliente.",
                    "humanReviewRequired": True,
                }
            )
        if (
            not contextual
            and executive.runway.value is not None
            and Decimal(str(executive.runway.value)) < 6
        ):
            insights.append(
                {
                    "code": "short_runway",
                    "severity": "critical",
                    "message": "Runway estimado inferior a seis meses.",
                    "humanReviewRequired": True,
                }
            )
        return _strip_internal_ids(_json_safe(
            {
                "generatedAt": now,
                "workspaceId": job.workspace_id,
                "filters": safe_filter_context,
                "dataQuality": "source_records",
                "scope": "dimensional" if contextual else "workspace_consolidated",
                "kpis": (
                    {
                        "status": "derived_from_scoped_dre",
                        "periodStart": start,
                        "periodEnd": end,
                        "result": scoped_dre["result"],
                        "lines": scoped_dre["lines"],
                    }
                    if contextual
                    else await reports.kpis(
                        job.workspace_id, start_date=start, end_date=end, as_of=end
                    )
                ),
                "dre": scoped_dre,
                "cashFlow": (
                    {
                        "status": "not_applicable_to_dimensional_scope",
                        "reason": "Fluxo de caixa consolidado omitido do recorte filtrado.",
                    }
                    if contextual
                    else await reports.cash_flow_projection(job.workspace_id, dias=horizon)
                ),
                "drilldown": drill,
                "executive": executive,
                "forecasts": (
                    forecasts
                    if not contextual
                    else {
                        "status": "not_applicable_to_dimensional_scope",
                        "reason": "Forecasts consolidados omitidos do recorte filtrado.",
                    }
                ),
                "valuations": (
                    valuations
                    if not contextual
                    else {
                        "status": "not_applicable_to_dimensional_scope",
                        "reason": "Valuations consolidados omitidos do recorte filtrado.",
                    }
                ),
                "budgetLines": budget_lines,
                "tax": {
                    "profile": (
                        {
                            "regime": tax_profile.regime,
                            "taxRate": tax_profile.tax_rate,
                            "effectiveFrom": tax_profile.effective_from,
                            "municipality": tax_profile.municipality,
                            "sources": tax_profile.sources,
                            "reformCalendar": tax_profile.reform_calendar,
                            "settings": tax_profile.settings,
                            "version": tax_profile.version,
                        }
                        if tax_profile and not contextual
                        else None
                    ),
                    "reformSimulation": tax_simulation,
                },
                "insights": insights,
            }
        ))

    async def run(self, job: FinanceExportJob):
        # Celery delivery is at-least-once. A duplicate wakeup must never
        # regenerate a terminal export or overwrite its audited result.
        if job.status not in {"queued", "running"}:
            return
        if job.status == "running":
            return
        if job.cancel_requested_at:
            before_snapshot = finance_export_audit_snapshot(job)
            before = str(getattr(job.status, "value", job.status))
            job.status = "cancelled"
            record_finance_export_event(
                self.db, job, "finance_export_cancelled", before_status=before
            )
            await record_finance_export_platform_lifecycle(
                self.db, job, action="cancelled", before=before_snapshot
            )
            await self.db.commit()
            return
        if not await self._actor_still_authorized(job):
            before_snapshot = finance_export_audit_snapshot(job)
            before = str(getattr(job.status, "value", job.status))
            job.status = "failed"
            job.error_detail = "FINANCE_EXPORT_PERMISSION_REVOKED"
            record_finance_export_event(
                self.db,
                job,
                "finance_export_failed",
                before_status=before,
                error_code=job.error_detail,
            )
            await record_finance_export_platform_lifecycle(
                self.db,
                job,
                action="failed",
                before=before_snapshot,
                result="failed",
                error_code=job.error_detail,
            )
            await self.db.commit()
            return
        before_snapshot = finance_export_audit_snapshot(job)
        before = str(getattr(job.status, "value", job.status))
        job.status = "running"
        job.progress = 10
        record_finance_export_event(self.db, job, "finance_export_started", before_status=before)
        await record_finance_export_platform_lifecycle(
            self.db, job, action="started", before=before_snapshot
        )
        await self.db.commit()
        try:
            snapshot = await self._snapshot(job)
            await self.db.refresh(job)
            if job.cancel_requested_at or job.status == "cancelled":
                before_snapshot = finance_export_audit_snapshot(job)
                before = str(getattr(job.status, "value", job.status))
                job.status = "cancelled"
                record_finance_export_event(
                    self.db, job, "finance_export_cancelled", before_status=before
                )
                await record_finance_export_platform_lifecycle(
                    self.db, job, action="cancelled", before=before_snapshot
                )
                await self.db.commit()
                return
            rows: list[tuple[str, str]] = []
            _flatten("", snapshot, rows)
            section_titles = {
                "kpis": "KPIs executivos",
                "dre": "DRE e orçamento",
                "cashFlow": "Fluxo de caixa e projeções",
                "drilldown": "Hierarquia financeira e registros",
                "forecasts": "Cenários e premissas",
                "valuations": "Valuation e premissas",
                "tax": "Tributação e reforma",
                "insights": "Insights e alertas",
            }
            lines = [
                "Meu Executivo — Relatório Financeiro",
                "",
                "Escopo, fontes e premissas",
                "",
                *_chart_lines(snapshot),
            ]
            for key, value in rows:
                section = next((title for prefix, title in section_titles.items() if key.startswith(prefix)), None)
                if section and (not lines or lines[-1] != section):
                    lines.extend(["", section])
                lines.append(f"{key}: {value}")
            if job.format == "xlsx":
                content = _xlsx_bytes(rows)
            elif job.format == "docx":
                content = _docx_bytes(lines)
            elif job.format == "pdf":
                content = _pdf_bytes(lines)
            else:
                raise ValueError("unsupported export format")
            checksum = hashlib.sha256(content).hexdigest()
            storage_key = (
                f"{settings.PLATFORM_EXPORT_STORAGE_PREFIX}/finance/{job.id}/{job.id}.{job.format}"
            )
            with tempfile.TemporaryDirectory(prefix="me-finance-export-") as temp_dir:
                await self.db.refresh(job)
                if job.cancel_requested_at or job.status == "cancelled":
                    before_snapshot = finance_export_audit_snapshot(job)
                    before = str(getattr(job.status, "value", job.status))
                    job.status = "cancelled"
                    record_finance_export_event(
                        self.db, job, "finance_export_cancelled", before_status=before
                    )
                    await record_finance_export_platform_lifecycle(
                        self.db, job, action="cancelled", before=before_snapshot
                    )
                    await self.db.commit()
                    return
                path = Path(temp_dir) / f"{job.id}.{job.format}"
                path.write_bytes(content)
                await PlatformExportService(self.db)._persist_artifact(
                    source=path,
                    storage_key=storage_key,
                    content_type={
                        "pdf": "application/pdf",
                        "xlsx": EXPORT_MEDIA_TYPES["xlsx"],
                        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }[job.format],
                    checksum_sha256=checksum,
                    dataset="finance",
                    format=job.format,
                )
            await self.db.refresh(job)
            if job.cancel_requested_at or job.status == "cancelled":
                await PlatformExportService(self.db).delete_artifact(storage_key)
                before_snapshot = finance_export_audit_snapshot(job)
                before = str(getattr(job.status, "value", job.status))
                job.status = "cancelled"
                job.storage_path = None
                record_finance_export_event(
                    self.db, job, "finance_export_cancelled", before_status=before
                )
                await record_finance_export_platform_lifecycle(
                    self.db, job, action="cancelled", before=before_snapshot
                )
                await self.db.commit()
                return
            before_snapshot = finance_export_audit_snapshot(job)
            job.storage_path = storage_key
            job.status = "completed"
            job.progress = 100
            job.row_count = len(rows)
            job.snapshot_at = datetime.now(UTC)
            job.checksum_sha256 = checksum
            job.error_detail = None
            record_finance_export_event(
                self.db, job, "finance_export_completed", before_status="running"
            )
            await record_finance_export_platform_lifecycle(
                self.db, job, action="completed", before=before_snapshot
            )
        except Exception as error:
            logger.error(
                "finance_export.generation_failed",
                extra={
                    "finance_export_job_id": str(job.id),
                    "error_type": safe_error_type(error),
                },
            )
            before_snapshot = finance_export_audit_snapshot(job)
            before = str(getattr(job.status, "value", job.status))
            job.status = "failed"
            job.error_detail = "FINANCE_EXPORT_GENERATION_FAILED"
            record_finance_export_event(
                self.db,
                job,
                "finance_export_failed",
                before_status=before,
                error_code=job.error_detail,
            )
            await record_finance_export_platform_lifecycle(
                self.db,
                job,
                action="failed",
                before=before_snapshot,
                result="failed",
                error_code=job.error_detail,
            )
        await self.db.commit()
