"""Project KLM legal CRM records into the local BottleCRM runtime."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.client import Client
from app.models.matter import KanbanColumnId, Matter
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.task import Task, TaskPriority, TaskStatus
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.bottlecrm_client import (
    BottleCRMClient,
    BottleCRMNotFoundError,
)
from app.services.oss_bridge_service import OSSBridgeService

SyncAction = Literal["created", "updated", "adopted", "recreated", "unchanged"]

_ROOT = Path(__file__).resolve().parents[2]
_PROCESS_SYNC_LOCK = asyncio.Lock()


@asynccontextmanager
async def _exclusive_bottlecrm_sync():
    """Serialize the single local BottleCRM organization across processes."""
    async with _PROCESS_SYNC_LOCK:
        lock_dir = _ROOT / ".local" / "locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(lock_dir, 0o700)
        lock_path = lock_dir / "bottlecrm-sync.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_EX)
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _marker(entity_id: UUID) -> str:
    return f"KLM-ID:{entity_id}"


def _bounded_label(label: str, entity_id: UUID, maximum: int) -> str:
    suffix = f" · KLM {str(entity_id)[:8]}"
    clean = " ".join(label.split()) or "Registro jurídico"
    return f"{clean[: maximum - len(suffix)].rstrip()}{suffix}"


def _description(marker: str, *parts: str | None) -> str:
    clean = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join([marker, *clean])


def _name_parts(name: str) -> tuple[str, str]:
    parts = " ".join(name.split()).split(" ", 1)
    return parts[0][:255], (parts[1][:255] if len(parts) > 1 else "")


def _date(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()[:10]
    return candidate if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate) else None


def _money(value: Any) -> float | None:
    if value is None:
        return None
    return round(min(max(float(value), 0.0), 9_999_999_999.99), 2)


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _source_digest(link: Any) -> str:
    metadata = getattr(link, "link_metadata", None)
    if not isinstance(metadata, dict):
        metadata = getattr(link, "metadata", None)
    return str(metadata.get("sourceDigest") or "") if isinstance(metadata, dict) else ""


def _lead_source(raw: str | None) -> str:
    source = (raw or "").lower()
    if "email" in source:
        return "email"
    if any(token in source for token in ("indica", "partner", "parceir", "refer")):
        return "partner"
    if "existing" in source or "cliente" in source:
        return "existing customer"
    if any(
        token in source
        for token in ("ads", "campaign", "campanha", "facebook", "google", "meta", "tiktok")
    ):
        return "compaign"
    if any(token in source for token in ("call", "telefone", "whatsapp")):
        return "call"
    if any(token in source for token in ("imprensa", "public relations")):
        return "public relations"
    return "other"


def _opportunity_source(raw: str | None) -> str:
    source = _lead_source(raw)
    return {
        "call": "CALL",
        "email": "EMAIL",
        "existing customer": "EXISTING CUSTOMER",
        "partner": "PARTNER",
        "public relations": "PUBLIC RELATIONS",
        "compaign": "CAMPAIGN",
    }.get(source, "OTHER")


_WON_COLUMNS = {
    KanbanColumnId.ganho.value,
    KanbanColumnId.assinado.value,
    KanbanColumnId.coleta_dados.value,
    KanbanColumnId.triagem_juridica.value,
    KanbanColumnId.pesquisa_validacao.value,
    KanbanColumnId.redacao_peca.value,
    KanbanColumnId.aprovacao_pendente.value,
    KanbanColumnId.pronto_protocolo.value,
    KanbanColumnId.migrar_sistema.value,
}
_PROPOSAL_COLUMNS = {KanbanColumnId.proposta.value, KanbanColumnId.proposta_fechamento.value}
_NEGOTIATION_COLUMNS = {
    KanbanColumnId.contrato.value,
    KanbanColumnId.pagamento.value,
}
_OPPORTUNITY_STAGE_PROBABILITIES = {
    "PROSPECTING": 10,
    "QUALIFICATION": 25,
    "PROPOSAL": 50,
    "NEGOTIATION": 75,
    "CLOSED_WON": 100,
    "CLOSED_LOST": 0,
}


def _opportunity_stage(matter: Matter) -> str:
    column = _value(matter.column_id)
    if column == KanbanColumnId.perdido.value:
        return "CLOSED_LOST"
    if column in _WON_COLUMNS:
        return "CLOSED_WON"
    if column in _NEGOTIATION_COLUMNS:
        return "NEGOTIATION"
    if column in _PROPOSAL_COLUMNS:
        return "PROPOSAL"
    if column in {
        KanbanColumnId.triagem_sdr.value,
        KanbanColumnId.documentos.value,
        KanbanColumnId.reuniao.value,
        KanbanColumnId.qualificacao.value,
    }:
        return "QUALIFICATION"
    return "PROSPECTING"


def _opportunity_probability(matter: Matter, stage: str) -> int:
    probability = min(max(matter.lead_score, 0), 100)
    if probability == 0:
        return _OPPORTUNITY_STAGE_PROBABILITIES.get(stage, 0)
    return probability


def _case_status(matter: Matter) -> str:
    column = _value(matter.column_id)
    if column == KanbanColumnId.perdido.value:
        return "Rejected"
    if column in _WON_COLUMNS:
        return "Assigned"
    if column in _PROPOSAL_COLUMNS | _NEGOTIATION_COLUMNS:
        return "Pending"
    return "New"


def _case_priority(matter: Matter) -> str:
    if _value(matter.urgency) == "urgente":
        return "Urgent"
    return {"alta": "High", "baixa": "Low"}.get(_value(matter.priority), "Normal")


def _task_status(task: Task) -> str:
    return {
        TaskStatus.done.value: "Completed",
        TaskStatus.in_progress.value: "In Progress",
        TaskStatus.blocked.value: "In Progress",
    }.get(_value(task.status), "New")


def _task_priority(task: Task) -> str:
    return {
        TaskPriority.alta.value: "High",
        TaskPriority.baixa.value: "Low",
    }.get(_value(task.priority), "Medium")


def _expected_fee(matter: Matter) -> float | None:
    estimate = matter.honorarium_estimate
    if estimate:
        value = estimate.total_estimated_value
        if value is None:
            value = estimate.fixed_fee
        if value is not None:
            return _money(value)
    projection = matter.revenue_projection
    return _money(projection.projected_revenue) if projection else None


class BottleCRMSyncService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = BottleCRMClient()
        self.links = OSSBridgeService(db)
        self.organization_id = ""

    async def _ensure_workspace_binding(self, workspace_id: UUID) -> None:
        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.bottlecrm,
                klm_entity_type="workspace",
                klm_entity_id=workspace_id,
                external_account_id=self.organization_id,
                external_entity_type="organization",
                external_entity_id=self.organization_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={
                    "managedBy": "klm-local-bridge",
                    "upstreamCommit": "3ab79d75",
                },
            ),
        )

    async def _sync_external(
        self,
        *,
        workspace_id: UUID,
        klm_entity_type: str,
        klm_entity_id: UUID,
        external_entity_type: str,
        payload: dict[str, Any],
        find: Callable[[], Awaitable[dict[str, Any] | None]],
        create: Callable[[dict[str, Any]], Awaitable[str]],
        update: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> tuple[str, SyncAction]:
        link = await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.bottlecrm,
            self.organization_id,
            klm_entity_type,
            klm_entity_id,
            external_entity_type,
        )
        external_id = link.external_entity_id if link else ""
        payload_digest = _digest(payload)
        action: SyncAction

        async def adopt(candidate: dict[str, Any]) -> tuple[str, SyncAction]:
            candidate_id = str(candidate.get("id") or "")
            if not candidate_id:
                return await create(payload), "recreated" if link else "created"
            try:
                await update(candidate_id, payload)
            except BottleCRMNotFoundError:
                return await create(payload), "recreated" if link else "created"
            return candidate_id, "adopted"

        digest_is_current = bool(
            link
            and _value(getattr(link, "sync_state", "")) == ExternalEntitySyncState.synced.value
            and _source_digest(link) == payload_digest
        )
        if digest_is_current and await self.client.entity_matches(
            external_entity_type,
            external_id,
            payload,
        ):
            return external_id, "unchanged"
        if link:
            try:
                await update(external_id, payload)
                action = "updated"
            except BottleCRMNotFoundError:
                existing = await find()
                if existing and existing.get("id"):
                    external_id, action = await adopt(existing)
                else:
                    external_id = await create(payload)
                    action = "recreated"
        else:
            existing = await find()
            if existing and existing.get("id"):
                external_id, action = await adopt(existing)
            else:
                external_id = await create(payload)
                action = "created"

        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.bottlecrm,
                klm_entity_type=klm_entity_type,
                klm_entity_id=klm_entity_id,
                external_account_id=self.organization_id,
                external_entity_type=external_entity_type,
                external_entity_id=external_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={
                    "managedBy": "klm-local-bridge",
                    "sourceDigest": payload_digest,
                    "upstreamCommit": "3ab79d75",
                },
            ),
        )
        return external_id, action

    @staticmethod
    def _record(counts: dict[str, int], resource: str, action: SyncAction) -> None:
        counts[resource] += 1
        counts[action] += 1

    async def _reconcile_stale_links(
        self,
        workspace_id: UUID,
        expected: set[tuple[str, UUID, str]],
    ) -> int:
        """Tombstone managed projections that disappeared from canonical KLM data."""

        links = list(
            (
                await self.db.execute(
                    select(ExternalEntityLink).where(
                        ExternalEntityLink.workspace_id == workspace_id,
                        ExternalEntityLink.provider == OSSBridgeProvider.bottlecrm,
                        ExternalEntityLink.external_account_id == self.organization_id,
                        ExternalEntityLink.external_entity_type != "organization",
                    )
                )
            ).scalars()
        )
        stale_at = datetime.now(UTC)
        changed = 0
        for link in links:
            metadata = link.link_metadata or {}
            key = (
                link.klm_entity_type,
                link.klm_entity_id,
                link.external_entity_type,
            )
            if metadata.get("managedBy") != "klm-local-bridge" or key in expected:
                continue
            if link.sync_state != ExternalEntitySyncState.stale:
                changed += 1
            link.sync_state = ExternalEntitySyncState.stale
            link.sync_error = None
            link.link_metadata = {
                **metadata,
                "staleReason": "canonical_entity_absent",
                "staleAt": stale_at.isoformat(),
                "upstreamPreserved": True,
            }
        return changed

    async def sync_workspace(self, workspace_id: UUID) -> dict[str, Any]:
        async with _exclusive_bottlecrm_sync():
            return await self._sync_workspace_unlocked(workspace_id)

    async def _sync_workspace_unlocked(self, workspace_id: UUID) -> dict[str, Any]:
        self.organization_id = await self.client.organization_id()
        await self._ensure_workspace_binding(workspace_id)

        clients = list(
            (
                await self.db.execute(
                    select(Client)
                    .where(Client.workspace_id == workspace_id, Client.deleted_at.is_(None))
                    .order_by(Client.created_at.asc())
                )
            ).scalars()
        )
        matters = list(
            (
                await self.db.execute(
                    select(Matter)
                    .options(
                        selectinload(Matter.client),
                        selectinload(Matter.tasks),
                        selectinload(Matter.honorarium_estimate),
                        selectinload(Matter.revenue_projection),
                    )
                    .where(Matter.workspace_id == workspace_id, Matter.deleted_at.is_(None))
                    .order_by(Matter.created_at.asc())
                )
            ).scalars()
        )
        active_client_ids = {client.id for client in clients}
        invalid_matter = next(
            (matter for matter in matters if matter.client_id not in active_client_ids),
            None,
        )
        if invalid_matter:
            raise LookupError(
                "Demanda KLM sem cliente ativo íntegro para sincronizar no BottleCRM."
            )
        matters_by_client: dict[UUID, list[Matter]] = defaultdict(list)
        for matter in matters:
            matters_by_client[matter.client_id].append(matter)

        insurer_ids = {
            uuid5(workspace_id, f"health-insurer:{' '.join(insurer.lower().split())}")
            for insurer in {
                matter.health_insurer.strip()
                for matter in matters
                if matter.health_insurer
            }
        }
        expected_links: set[tuple[str, UUID, str]] = {
            *(('health_insurer', insurer_id, 'account') for insurer_id in insurer_ids),
            *(('client', client.id, 'contact') for client in clients),
            *(('client', client.id, 'lead') for client in clients),
            *(('matter', matter.id, 'opportunity') for matter in matters),
            *(('matter', matter.id, 'case') for matter in matters),
            *(
                ('task', task.id, 'task')
                for matter in matters
                for task in matter.tasks
            ),
        }

        counts = {
            "accounts": 0,
            "contacts": 0,
            "leads": 0,
            "opportunities": 0,
            "cases": 0,
            "tasks": 0,
            "created": 0,
            "updated": 0,
            "adopted": 0,
            "recreated": 0,
            "unchanged": 0,
            "stale": 0,
        }
        account_ids: dict[str, str] = {}
        for insurer in sorted(
            {matter.health_insurer.strip() for matter in matters if matter.health_insurer}
        ):
            normalized = " ".join(insurer.lower().split())
            synthetic_id = uuid5(workspace_id, f"health-insurer:{normalized}")
            payload = {
                "name": insurer[:255],
                "industry": "INSURANCE",
                "country": "BR",
                "currency": "BRL",
                "description": _description(
                    _marker(synthetic_id),
                    "Operadora de saúde relacionada a demandas do Meu Executivo.",
                ),
                "is_active": True,
            }
            external_id, action = await self._sync_external(
                workspace_id=workspace_id,
                klm_entity_type="health_insurer",
                klm_entity_id=synthetic_id,
                external_entity_type="account",
                payload=payload,
                find=lambda name=payload["name"]: self.client.find_account(name),
                create=self.client.create_account,
                update=self.client.update_account,
            )
            account_ids[normalized] = external_id
            self._record(counts, "accounts", action)

        contact_ids: dict[UUID, str] = {}
        for client in clients:
            first_name, last_name = _name_parts(client.name)
            client_matters = matters_by_client.get(client.id, [])
            insurers = {
                " ".join(matter.health_insurer.lower().split())
                for matter in client_matters
                if matter.health_insurer
            }
            account_id = account_ids.get(next(iter(insurers))) if len(insurers) == 1 else None
            marker = _marker(client.id)
            payload = _compact(
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": client.email,
                    "phone": client.phone,
                    "organization": client.employer,
                    "title": client.position,
                    "country": "BR",
                    "description": _description(marker, client.notes),
                    "account": account_id,
                    "is_active": True,
                }
            )
            query = client.email or first_name
            external_id, action = await self._sync_external(
                workspace_id=workspace_id,
                klm_entity_type="client",
                klm_entity_id=client.id,
                external_entity_type="contact",
                payload=payload,
                find=lambda marker=marker, query=query: self.client.find_contact(marker, query),
                create=self.client.create_contact,
                update=self.client.update_contact,
            )
            contact_ids[client.id] = external_id
            self._record(counts, "contacts", action)

            amounts = [value for value in (_expected_fee(m) for m in client_matters) if value]
            score = max((matter.lead_score for matter in client_matters), default=0)
            all_lost = bool(client_matters) and all(
                _value(matter.column_id) == KanbanColumnId.perdido.value
                for matter in client_matters
            )
            lead_marker = _marker(client.id)
            lead_payload = _compact(
                {
                    "title": _bounded_label("Atendimento jurídico", client.id, 255),
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": client.email,
                    "phone": client.phone,
                    "job_title": client.position,
                    "status": "closed" if all_lost else "in process",
                    "source": _lead_source(
                        client_matters[0].lead_source if client_matters else None
                    ),
                    "rating": "HOT" if score >= 75 else "WARM" if score >= 45 else "COLD",
                    "opportunity_amount": _money(sum(amounts)) if amounts else None,
                    "currency": "BRL",
                    "probability": min(max(score, 0), 100),
                    "country": "BR",
                    "company_name": f"Meu Executivo | {lead_marker}",
                    "description": _description(
                        lead_marker,
                        "\n".join(f"• {matter.demand_type}" for matter in client_matters)
                        or "Atendimento sem demanda vinculada.",
                    ),
                    "contacts": [external_id],
                    "is_active": True,
                }
            )
            _, action = await self._sync_external(
                workspace_id=workspace_id,
                klm_entity_type="client",
                klm_entity_id=client.id,
                external_entity_type="lead",
                payload=lead_payload,
                find=lambda marker=lead_marker: self.client.find_lead(marker),
                create=self.client.create_lead,
                update=self.client.update_lead,
            )
            self._record(counts, "leads", action)

        case_ids: dict[UUID, str] = {}
        for matter in matters:
            contact_id = contact_ids[matter.client_id]
            insurer_key = (
                " ".join(matter.health_insurer.lower().split()) if matter.health_insurer else ""
            )
            account_id = account_ids.get(insurer_key)
            expected_fee = _expected_fee(matter)
            display = _bounded_label(f"{matter.client.name} · {matter.demand_type}", matter.id, 255)
            case_display = _bounded_label(
                f"{matter.client.name} · {matter.demand_type}", matter.id, 64
            )
            description = _description(
                _marker(matter.id),
                matter.executive_summary,
                f"Subtema: {_value(matter.health_subtheme)}" if matter.health_subtheme else None,
                f"Objeto: {matter.health_object_name}" if matter.health_object_name else None,
                f"Motivo da negativa: {matter.health_denial_reason}"
                if matter.health_denial_reason
                else None,
            )
            opportunity_stage = _opportunity_stage(matter)
            opportunity_payload = _compact(
                {
                    "name": display,
                    "account": account_id,
                    "stage": opportunity_stage,
                    "opportunity_type": "NEW_BUSINESS",
                    "currency": "BRL",
                    "amount": expected_fee,
                    "probability": _opportunity_probability(matter, opportunity_stage),
                    "lead_source": _opportunity_source(matter.lead_source),
                    "description": description,
                    "contacts": [contact_id],
                    "is_active": True,
                }
            )
            _, action = await self._sync_external(
                workspace_id=workspace_id,
                klm_entity_type="matter",
                klm_entity_id=matter.id,
                external_entity_type="opportunity",
                payload=opportunity_payload,
                find=lambda name=display: self.client.find_opportunity(name),
                create=self.client.create_opportunity,
                update=self.client.update_opportunity,
            )
            self._record(counts, "opportunities", action)

            case_payload = _compact(
                {
                    "name": case_display,
                    "status": _case_status(matter),
                    "priority": _case_priority(matter),
                    "case_type": "Incident",
                    "description": description,
                    "account": account_id,
                    "contacts": [contact_id],
                    "is_active": True,
                }
            )
            case_id, action = await self._sync_external(
                workspace_id=workspace_id,
                klm_entity_type="matter",
                klm_entity_id=matter.id,
                external_entity_type="case",
                payload=case_payload,
                find=lambda name=case_display: self.client.find_case(name),
                create=self.client.create_case,
                update=self.client.update_case,
            )
            case_ids[matter.id] = case_id
            self._record(counts, "cases", action)

            for task in matter.tasks:
                title = _bounded_label(task.title, task.id, 200)
                task_payload = _compact(
                    {
                        "title": title,
                        "status": _task_status(task),
                        "priority": _task_priority(task),
                        "due_date": _date(task.due_date),
                        "description": _description(_marker(task.id), task.description),
                        "case": case_id,
                        "contacts": [contact_id],
                    }
                )
                _, action = await self._sync_external(
                    workspace_id=workspace_id,
                    klm_entity_type="task",
                    klm_entity_id=task.id,
                    external_entity_type="task",
                    payload=task_payload,
                    find=lambda title=title: self.client.find_task(title),
                    create=self.client.create_task,
                    update=self.client.update_task,
                )
                self._record(counts, "tasks", action)

        counts["stale"] = await self._reconcile_stale_links(
            workspace_id,
            expected_links,
        )
        await self.db.commit()
        return {
            **counts,
            "organizationBound": True,
            "sourceOfTruth": "klm",
        }
