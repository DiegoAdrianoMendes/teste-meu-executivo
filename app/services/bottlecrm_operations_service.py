"""Read-only operational views over the local BottleCRM projection."""

from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.matter import Matter
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.services.bottlecrm_client import BottleCRMClient

_DETAIL_CONTRACTS = {
    "contact": ("contacts", "contact_obj"),
    "lead": ("leads", "lead_obj"),
    "opportunity": ("opportunities", "opportunity_obj"),
    "case": ("cases", "cases_obj"),
    "task": ("tasks", "task_obj"),
}


def _dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _name(row: dict[str, Any], entity_type: str, include_sensitive: bool) -> str:
    if not include_sensitive:
        return {
            "lead": "Lead restrito",
            "contact": "Contato restrito",
            "case": "Caso restrito",
            "opportunity": "Oportunidade restrita",
            "task": "Tarefa restrita",
            "invoice": "Fatura restrita",
            "estimate": "Proposta restrita",
        }.get(entity_type, "Registro restrito")
    if entity_type == "contact":
        full_name = " ".join(
            part for part in (_text(row.get("first_name")), _text(row.get("last_name"))) if part
        )
        if full_name:
            return full_name
    candidates = {
        "lead": ("full_name", "company_name", "title"),
        "opportunity": ("name",),
        "case": ("name",),
        "task": ("title", "name"),
        "invoice": ("invoice_title", "title", "invoice_number"),
        "estimate": ("title", "estimate_number"),
        "product": ("name", "sku"),
    }.get(entity_type, ("name", "title"))
    return next((_text(row.get(key)) for key in candidates if _text(row.get(key))), None) or (
        entity_type.replace("_", " ").title()
    )


def _link_index(links: list[ExternalEntityLink]) -> dict[str, ExternalEntityLink]:
    return {
        link.external_entity_id: link
        for link in links
        if link.external_entity_type != "organization"
        and link.sync_state == ExternalEntitySyncState.synced
    }


def _is_current_matter_link(
    link: ExternalEntityLink,
    *,
    matter_id: UUID,
    client_id: UUID,
    task_ids: set[UUID],
) -> bool:
    if link.sync_state != ExternalEntitySyncState.synced:
        return False
    return (
        (link.klm_entity_type == "matter" and link.klm_entity_id == matter_id)
        or (link.klm_entity_type == "client" and link.klm_entity_id == client_id)
        or (link.klm_entity_type == "task" and link.klm_entity_id in task_ids)
    )


def _normalize_item(
    row: dict[str, Any],
    *,
    entity_type: str,
    links: dict[str, ExternalEntityLink],
    include_sensitive: bool,
) -> dict[str, Any]:
    external_id = _text(row.get("id")) or ""
    link = links.get(external_id)
    return {
        "id": external_id,
        "entityType": entity_type,
        "klmEntityId": str(link.klm_entity_id) if link else None,
        "klmEntityType": link.klm_entity_type if link else None,
        "name": _name(row, entity_type, include_sensitive),
        "stage": _text(row.get("stage")),
        "status": _text(row.get("status")),
        "priority": _text(row.get("priority")),
        "amount": _number(
            row.get("amount")
            if row.get("amount") not in (None, "")
            else row.get("opportunity_amount")
        ),
        "currency": _text(row.get("currency")) or "BRL",
        "probability": _integer(row.get("probability"))
        if row.get("probability") is not None
        else None,
        "daysInStage": _integer(row.get("days_in_stage"))
        if row.get("days_in_stage") is not None
        else None,
        "agingStatus": _text(row.get("aging_status")),
        "dueDate": _text(row.get("due_date")),
        "nextFollowUp": _text(row.get("next_follow_up")),
        "overdue": bool(row.get("is_follow_up_overdue")),
        "slaBreached": bool(
            row.get("is_sla_breached")
            or row.get("is_sla_first_response_breached")
            or row.get("is_sla_resolution_breached")
        ),
        "escalationCount": _integer(row.get("escalation_count")),
    }


def _normalize_columns(
    payload: Any,
    *,
    entity_type: str,
    row_key: str,
    count_key: str,
    links: dict[str, ExternalEntityLink],
    include_sensitive: bool,
) -> list[dict[str, Any]]:
    columns = _rows(_dictionary(payload).get("columns"))
    return [
        {
            "id": _text(column.get("id")) or f"{entity_type}-{index}",
            "name": _text(column.get("name")) or "Etapa",
            "order": _integer(column.get("order"), index),
            "color": _text(column.get("color")),
            "stageType": _text(column.get("stage_type")),
            "wipLimit": _integer(column.get("wip_limit"))
            if column.get("wip_limit") is not None
            else None,
            "itemCount": _integer(column.get(count_key), len(_rows(column.get(row_key)))),
            "items": [
                _normalize_item(
                    row,
                    entity_type=entity_type,
                    links=links,
                    include_sensitive=include_sensitive,
                )
                for row in _rows(column.get(row_key))
            ],
        }
        for index, column in enumerate(columns)
    ]


def _normalize_goal(row: dict[str, Any]) -> dict[str, Any]:
    target = _number(row.get("target_value") or row.get("target")) or 0.0
    achieved = (
        _number(row.get("current_value") or row.get("achieved") or row.get("progress"))
        or 0.0
    )
    return {
        "id": _text(row.get("id")) or _text(row.get("goal_id")) or "",
        "name": _text(row.get("name")) or _text(row.get("goal_name")) or "Meta comercial",
        "periodType": _text(row.get("period_type")),
        "periodStart": _text(row.get("period_start")),
        "periodEnd": _text(row.get("period_end")),
        "target": target,
        "achieved": achieved,
        "percent": round((achieved / target) * 100, 2) if target else 0.0,
        "active": bool(row.get("is_active", True)),
    }


def _normalize_finance_row(
    row: dict[str, Any],
    *,
    entity_type: str,
    include_sensitive: bool,
) -> dict[str, Any]:
    amount = next(
        (
            _number(row.get(key))
            for key in ("total_amount", "amount_due", "total", "unit_price", "price")
            if _number(row.get(key)) is not None
        ),
        None,
    )
    return {
        "id": _text(row.get("id")) or "",
        "entityType": entity_type,
        "name": _name(row, entity_type, include_sensitive or entity_type == "product"),
        "number": _text(row.get("invoice_number")) or _text(row.get("estimate_number")),
        "status": _text(row.get("status")),
        "amount": amount,
        "amountDue": _number(row.get("amount_due")),
        "currency": _text(row.get("currency")) or "BRL",
        "issueDate": _text(row.get("issue_date")),
        "dueDate": _text(row.get("due_date")),
        "validUntil": _text(row.get("valid_until")) or _text(row.get("expiry_date")),
        "category": _text(row.get("category")),
        "active": bool(row.get("is_active", True)),
    }


class BottleCRMOperationsService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = BottleCRMClient()

    async def _projection(
        self,
        workspace_id: UUID,
    ) -> tuple[dict[str, Any], list[ExternalEntityLink]]:
        organization_id = await self.client.organization_id()
        links = list(
            (
                await self.db.execute(
                    select(ExternalEntityLink).where(
                        ExternalEntityLink.workspace_id == workspace_id,
                        ExternalEntityLink.provider == OSSBridgeProvider.bottlecrm,
                        ExternalEntityLink.external_account_id == organization_id,
                    )
                )
            ).scalars()
        )
        binding = next(
            (
                link
                for link in links
                if link.klm_entity_type == "workspace"
                and link.external_entity_type == "organization"
                and link.external_entity_id == organization_id
                and link.sync_state == ExternalEntitySyncState.synced
            ),
            None,
        )
        if binding is None:
            return {
                "linked": False,
                "organizationId": organization_id,
                "totalLinks": 0,
                "syncedLinks": 0,
                "failedLinks": 0,
                "staleLinks": 0,
                "byEntityType": {},
                "lastSyncedAt": None,
                "sourceOfTruth": "klm",
                "externalExecutionEnabled": False,
            }, []

        state_counts = Counter(str(link.sync_state.value) for link in links)
        type_counts = Counter(
            link.external_entity_type
            for link in links
            if link.external_entity_type != "organization"
        )
        synced_dates = [link.last_synced_at for link in links if link.last_synced_at]
        return {
            "linked": True,
            "organizationId": organization_id,
            "totalLinks": max(0, len(links) - 1),
            "syncedLinks": max(0, state_counts[ExternalEntitySyncState.synced.value] - 1),
            "failedLinks": state_counts[ExternalEntitySyncState.failed.value],
            "staleLinks": state_counts[ExternalEntitySyncState.stale.value],
            "byEntityType": dict(sorted(type_counts.items())),
            "lastSyncedAt": max(synced_dates) if synced_dates else None,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }, links

    async def funnel_context(
        self,
        workspace_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        projection, links = await self._projection(workspace_id)
        empty = {
            "projection": projection,
            "counts": {},
            "urgentCounts": {},
            "pipeline": [],
            "revenue": {},
            "opportunityColumns": [],
            "leadColumns": [],
            "caseColumns": [],
            "goals": [],
            "agingConfig": [],
            "notificationsUnread": 0,
            "sensitiveDataHidden": not include_sensitive,
        }
        if not projection["linked"]:
            return empty

        dashboard, opportunities, leads, cases, goals, aging, notifications = await asyncio.gather(
            self.client.read_payload("/api/dashboard/"),
            self.client.read_payload("/api/opportunities/kanban/"),
            self.client.read_payload("/api/leads/kanban/"),
            self.client.read_payload("/api/cases/kanban/"),
            self.client.read_payload(
                "/api/opportunities/goals/",
                params={"active": "true", "current": "true", "limit": 20},
            ),
            self.client.read_payload("/api/opportunities/aging-config/"),
            self.client.read_payload("/api/notifications/", params={"limit": 10}),
        )
        dashboard_object = _dictionary(dashboard)
        link_map = _link_index(links)
        pipeline_source = _dictionary(dashboard_object.get("pipeline_by_stage"))
        pipeline = [
            {
                "id": stage,
                "label": _text(_dictionary(values).get("label")) or stage.replace("_", " ").title(),
                "count": _integer(_dictionary(values).get("count")),
                "value": _number(_dictionary(values).get("value")) or 0.0,
            }
            for stage, values in pipeline_source.items()
        ]
        goal_rows = _rows(_dictionary(goals).get("goals"))
        aging_rows = _rows(aging)
        return {
            **empty,
            "counts": {
                "accounts": _integer(dashboard_object.get("accounts_count")),
                "contacts": _integer(dashboard_object.get("contacts_count")),
                "leads": _integer(dashboard_object.get("leads_count")),
                "opportunities": _integer(dashboard_object.get("opportunities_count")),
            },
            "urgentCounts": {
                key: _integer(value)
                for key, value in _dictionary(dashboard_object.get("urgent_counts")).items()
            },
            "pipeline": pipeline,
            "revenue": {
                key: (_number(value) if key != "currency" else _text(value))
                for key, value in _dictionary(dashboard_object.get("revenue_metrics")).items()
            },
            "opportunityColumns": _normalize_columns(
                opportunities,
                entity_type="opportunity",
                row_key="items",
                count_key="item_count",
                links=link_map,
                include_sensitive=include_sensitive,
            ),
            "leadColumns": _normalize_columns(
                leads,
                entity_type="lead",
                row_key="leads",
                count_key="lead_count",
                links=link_map,
                include_sensitive=include_sensitive,
            ),
            "caseColumns": _normalize_columns(
                cases,
                entity_type="case",
                row_key="cases",
                count_key="case_count",
                links=link_map,
                include_sensitive=include_sensitive,
            ),
            "goals": [_normalize_goal(row) for row in goal_rows],
            "agingConfig": [
                {
                    "stage": _text(row.get("stage")) or "",
                    "expectedDays": _integer(row.get("expected_days"), 14),
                    "warningDays": _integer(row.get("warning_days"))
                    if row.get("warning_days") is not None
                    else None,
                }
                for row in aging_rows
            ],
            "notificationsUnread": _integer(
                _dictionary(notifications).get("unread_count")
            ),
        }

    async def matter_context(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        projection, links = await self._projection(workspace_id)
        matter = await self.db.scalar(
            select(Matter)
            .options(selectinload(Matter.client), selectinload(Matter.tasks))
            .where(
                Matter.workspace_id == workspace_id,
                Matter.id == matter_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise LookupError("Caso KLM não encontrado no workspace atual.")

        task_ids = {task.id for task in matter.tasks}
        selected_links = [
            link
            for link in links
            if _is_current_matter_link(
                link,
                matter_id=matter.id,
                client_id=matter.client_id,
                task_ids=task_ids,
            )
        ]
        readable_links = [
            link for link in selected_links if link.external_entity_type in _DETAIL_CONTRACTS
        ]

        async def read_record(link: ExternalEntityLink) -> dict[str, Any]:
            collection, object_key = _DETAIL_CONTRACTS[link.external_entity_type]
            payload = _dictionary(
                await self.client.read_payload(
                    f"/api/{collection}/{link.external_entity_id}/"
                )
            )
            record = _dictionary(payload.get(object_key))
            normalized = _normalize_item(
                record,
                entity_type=link.external_entity_type,
                links={link.external_entity_id: link},
                include_sensitive=include_sensitive,
            )
            return {
                **normalized,
                "syncState": link.sync_state.value,
                "lastSyncedAt": link.last_synced_at,
                "firstResponseDeadline": _text(record.get("first_response_sla_deadline")),
                "resolutionDeadline": _text(record.get("resolution_sla_deadline")),
            }

        records = await asyncio.gather(*(read_record(link) for link in readable_links))
        return {
            "projection": projection,
            "matterId": matter_id,
            "records": list(records),
            "sensitiveDataHidden": not include_sensitive,
        }

    async def work_context(
        self,
        workspace_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        projection, links = await self._projection(workspace_id)
        empty = {
            "projection": projection,
            "taskColumns": [],
            "tasks": [],
            "boards": [],
            "notificationsUnread": 0,
            "sensitiveDataHidden": not include_sensitive,
        }
        if not projection["linked"]:
            return empty
        task_kanban, task_list, boards, notifications = await asyncio.gather(
            self.client.read_payload("/api/tasks/kanban/"),
            self.client.read_payload("/api/tasks/", params={"limit": 50}),
            self.client.read_payload("/api/boards/", params={"limit": 20}),
            self.client.read_payload("/api/notifications/", params={"limit": 10}),
        )
        link_map = _link_index(links)
        return {
            **empty,
            "taskColumns": _normalize_columns(
                task_kanban,
                entity_type="task",
                row_key="tasks",
                count_key="task_count",
                links=link_map,
                include_sensitive=include_sensitive,
            ),
            "tasks": [
                _normalize_item(
                    row,
                    entity_type="task",
                    links=link_map,
                    include_sensitive=include_sensitive,
                )
                for row in _rows(_dictionary(task_list).get("tasks"))
            ],
            "boards": [
                {
                    "id": _text(row.get("id")) or "",
                    "name": _name(row, "board", include_sensitive),
                    "description": _text(row.get("description")) if include_sensitive else None,
                    "columnCount": len(_rows(row.get("columns"))),
                }
                for row in _rows(_dictionary(boards).get("results"))
            ],
            "notificationsUnread": _integer(
                _dictionary(notifications).get("unread_count")
            ),
        }

    async def finance_context(
        self,
        workspace_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        projection, _ = await self._projection(workspace_id)
        empty = {
            "projection": projection,
            "summary": {},
            "statusCounts": {},
            "overdue": {},
            "recentActivity": {},
            "estimateSummary": {},
            "invoices": [],
            "estimates": [],
            "products": [],
            "aging": [],
            "revenueSeries": [],
            "notificationsUnread": 0,
            "sensitiveDataHidden": not include_sensitive,
        }
        if not projection["linked"]:
            return empty

        (
            dashboard,
            invoices,
            estimates,
            products,
            aging,
            revenue,
            notifications,
        ) = await asyncio.gather(
            self.client.read_payload("/api/invoices/reports/dashboard/"),
            self.client.read_payload("/api/invoices/", params={"limit": 20}),
            self.client.read_payload("/api/invoices/estimates/", params={"limit": 20}),
            self.client.read_payload(
                "/api/invoices/products/", params={"limit": 20, "is_active": "true"}
            ),
            self.client.read_payload("/api/invoices/reports/aging/"),
            self.client.read_payload(
                "/api/invoices/reports/revenue/", params={"group_by": "month"}
            ),
            self.client.read_payload("/api/notifications/", params={"limit": 10}),
        )
        dashboard_object = _dictionary(dashboard)
        aging_object = _dictionary(aging)
        revenue_object = _dictionary(revenue)
        aging_keys = ("current", "1_30_days", "31_60_days", "61_90_days", "over_90_days")
        return {
            **empty,
            "summary": {
                key: _number(value) or 0.0
                for key, value in _dictionary(dashboard_object.get("summary")).items()
            },
            "statusCounts": {
                str(key): _integer(value)
                for key, value in _dictionary(dashboard_object.get("status_counts")).items()
            },
            "overdue": {
                "count": _integer(_dictionary(dashboard_object.get("overdue")).get("count")),
                "amount": _number(_dictionary(dashboard_object.get("overdue")).get("amount"))
                or 0.0,
            },
            "recentActivity": {
                key: (_number(value) or 0.0)
                if key in {"revenue_30d", "invoiced_30d"}
                else _integer(value)
                for key, value in _dictionary(
                    dashboard_object.get("recent_activity")
                ).items()
            },
            "estimateSummary": {
                key: _integer(value)
                for key, value in _dictionary(dashboard_object.get("estimates")).items()
            },
            "invoices": [
                _normalize_finance_row(
                    row, entity_type="invoice", include_sensitive=include_sensitive
                )
                for row in _rows(_dictionary(invoices).get("results"))
            ],
            "estimates": [
                _normalize_finance_row(
                    row, entity_type="estimate", include_sensitive=include_sensitive
                )
                for row in _rows(_dictionary(estimates).get("results"))
            ],
            "products": [
                _normalize_finance_row(
                    row, entity_type="product", include_sensitive=True
                )
                for row in _rows(_dictionary(products).get("results"))
            ],
            "aging": [
                {
                    "bucket": key,
                    "count": _integer(_dictionary(aging_object.get(key)).get("count")),
                    "amount": _number(_dictionary(aging_object.get(key)).get("amount"))
                    or 0.0,
                }
                for key in aging_keys
            ],
            "revenueSeries": [
                {
                    "period": _text(row.get("period")),
                    "revenue": _number(row.get("revenue")) or 0.0,
                    "count": _integer(row.get("count")),
                }
                for row in _rows(revenue_object.get("data"))
            ],
            "notificationsUnread": _integer(
                _dictionary(notifications).get("unread_count")
            ),
        }
