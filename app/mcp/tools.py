"""Compact, legal-CRM-specific MCP tools with tenant and mutation guardrails."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.errors import McpToolError
from app.mcp.identity import IdentityGuard, McpActor
from app.mcp.locks import WorkspaceMutationLock
from app.mcp.schemas import (
    CreateCaseNoteInput,
    CreateTaskInput,
    GetClientInput,
    GetMatterInput,
    ListClientsInput,
    ListMattersInput,
    QualifyMatterInput,
    RequestApprovalInput,
    RequestHandoffInput,
    ToolInput,
)
from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import KanbanColumnId, Matter
from app.models.task import Task
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.approval import ApprovalCreate
from app.schemas.task import TaskCreate
from app.services.approval_service import ApprovalService
from app.services.klm_operations_service import KlmOperationsService
from app.services.matter_service import MatterService
from app.services.task_service import TaskService


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (UUID, datetime, Decimal)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_model: type[ToolInput]
    capability: str
    handler_name: str
    read_only: bool

    def as_mcp_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_model.model_json_schema(),
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            "execution": {"taskSupport": "forbidden"},
        }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "klm.crm.list_clients",
        "Listar clientes",
        (
            "Lista clientes do workspace fixado no grant. Busca por identidade exige "
            "acesso sensível explícito; limite máximo 50."
        ),
        ListClientsInput,
        "crm.read",
        "list_clients",
        True,
    ),
    ToolSpec(
        "klm.crm.get_client",
        "Consultar cliente",
        (
            "Obtém um cliente por UUID, sempre validando que pertence ao workspace "
            "do grant e mascarando dados quando necessário."
        ),
        GetClientInput,
        "crm.read",
        "get_client",
        True,
    ),
    ToolSpec(
        "klm.crm.list_matters",
        "Listar casos jurídicos",
        (
            "Busca casos jurídicos do workspace fixado no grant com paginação "
            "compacta e filtros seguros."
        ),
        ListMattersInput,
        "crm.read",
        "list_matters",
        True,
    ),
    ToolSpec(
        "klm.crm.get_matter",
        "Consultar caso jurídico",
        "Obtém o dossiê CRM compacto de um caso; não retorna documentos nem conteúdo RAG em massa.",
        GetMatterInput,
        "crm.read",
        "get_matter",
        True,
    ),
    ToolSpec(
        "klm.crm.qualify_and_tag_matter",
        "Qualificar e etiquetar caso",
        (
            "Atualiza somente campos de qualificação permitidos. Requer confirm=true, "
            "idempotency_key e permissão de editar dados do lead."
        ),
        QualifyMatterInput,
        "crm.write",
        "qualify_and_tag_matter",
        False,
    ),
    ToolSpec(
        "klm.crm.create_case_note",
        "Criar nota interna",
        (
            "Cria nota interna na timeline do caso. Nunca envia conteúdo ao cliente "
            "ou a uma API externa."
        ),
        CreateCaseNoteInput,
        "crm.write",
        "create_case_note",
        False,
    ),
    ToolSpec(
        "klm.crm.create_task",
        "Criar tarefa jurídica",
        (
            "Cria tarefa vinculada ao caso. Um assignee informado é aceito apenas "
            "se for membro ativo do mesmo workspace."
        ),
        CreateTaskInput,
        "crm.write",
        "create_task",
        False,
    ),
    ToolSpec(
        "klm.crm.request_human_handoff",
        "Pedir handoff humano",
        (
            "Registra um pedido local de handoff na fila de aprovação humana, "
            "sem acionar canais externos."
        ),
        RequestHandoffInput,
        "handoff.request",
        "request_human_handoff",
        False,
    ),
    ToolSpec(
        "klm.crm.request_approval",
        "Pedir aprovação humana",
        (
            "Registra uma solicitação local na fila de aprovação do caso; não aprova "
            "nem executa a ação solicitada."
        ),
        RequestApprovalInput,
        "approval.request",
        "request_approval",
        False,
    ),
)


@dataclass(frozen=True)
class MutationOutcome:
    entity_type: str
    entity_id: UUID
    summary: str
    result: dict[str, Any]


class MutationCoordinator:
    """Serialize writes and use the audit trail as the atomic idempotency receipt."""

    def __init__(self, lock: WorkspaceMutationLock) -> None:
        self.lock = lock

    @staticmethod
    def correlation_id(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"mcp:{digest}"

    async def execute(
        self,
        db: AsyncSession,
        actor: McpActor,
        *,
        tool_name: str,
        idempotency_key: str,
        mutation: Callable[[], Awaitable[MutationOutcome]],
    ) -> dict[str, Any]:
        correlation_id = self.correlation_id(idempotency_key)
        event_type = f"mcp_{tool_name.replace('.', '_')}"
        with self.lock.acquire(actor.workspace_id):
            try:
                workspace = await db.scalar(
                    select(Workspace)
                    .where(Workspace.id == actor.workspace_id)
                    .with_for_update()
                )
                if workspace is None:
                    raise McpToolError(
                        "MCP_IDENTITY_REVOKED",
                        "The configured workspace is no longer available.",
                    )

                receipt = await db.scalar(
                    select(KlmAuditEvent)
                    .where(
                        KlmAuditEvent.workspace_id == actor.workspace_id,
                        KlmAuditEvent.correlation_id == correlation_id,
                    )
                    .order_by(KlmAuditEvent.occurred_at.desc())
                    .limit(1)
                )
                if receipt is not None:
                    if receipt.event_type != event_type:
                        raise McpToolError(
                            "MCP_IDEMPOTENCY_CONFLICT",
                            "This idempotency key was already used for a different operation.",
                        )
                    stored = (receipt.after_data or {}).get("result")
                    if not isinstance(stored, dict):
                        raise McpToolError(
                            "MCP_IDEMPOTENCY_RECEIPT_INVALID",
                            "The existing mutation receipt cannot be replayed safely.",
                        )
                    await db.commit()
                    return {**stored, "ok": True, "deduplicated": True}

                outcome = await mutation()
                result = _json_safe(outcome.result)
                await KlmOperationsService(db).record_audit(
                    workspace_id=actor.workspace_id,
                    actor_user_id=actor.user_id,
                    actor_type="mcp",
                    actor_label=f"MCP via {actor.label}",
                    event_type=event_type,
                    entity_type=outcome.entity_type,
                    entity_id=outcome.entity_id,
                    summary=outcome.summary,
                    correlation_id=correlation_id,
                    after_data={"tool": tool_name, "result": result},
                )
                await db.commit()
                return {**result, "ok": True, "deduplicated": False}
            except Exception:
                await db.rollback()
                raise


class KlmCrmTools:
    def __init__(self, identity: IdentityGuard, *, state_dir: str) -> None:
        self.identity = identity
        self.coordinator = MutationCoordinator(WorkspaceMutationLock(state_dir))
        self._specs = {spec.name: spec for spec in TOOL_SPECS}

    def definitions(self) -> list[dict[str, Any]]:
        capabilities = set(self.identity.claims.capabilities)
        return [
            spec.as_mcp_definition()
            for spec in TOOL_SPECS
            if spec.capability in capabilities
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None or spec.capability not in self.identity.claims.capabilities:
            raise McpToolError("MCP_TOOL_NOT_FOUND", "The requested MCP tool is not exposed.")
        try:
            payload = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            errors = [
                {
                    "field": ".".join(str(item) for item in error["loc"]),
                    "message": error["msg"],
                }
                for error in exc.errors(include_url=False, include_input=False)
            ]
            raise McpToolError(
                "MCP_INVALID_ARGUMENT",
                "The tool arguments failed validation.",
                details={"validationErrors": errors},
            ) from exc
        handler = getattr(self, spec.handler_name)
        return await handler(payload)

    @staticmethod
    def _client_payload(client: Client, *, sensitive: bool) -> dict[str, Any]:
        token = str(client.id).replace("-", "")[-6:].upper()
        base: dict[str, Any] = {
            "id": str(client.id),
            "label": client.name if sensitive else f"Lead {token}",
            "sensitiveDataHidden": not sensitive,
            "createdAt": client.created_at.isoformat() if client.created_at else None,
        }
        if sensitive:
            base.update(
                {
                    "name": client.name,
                    "cpf": client.cpf,
                    "email": client.email,
                    "phone": client.phone,
                    "employer": client.employer,
                    "position": client.position,
                    "notes": client.notes,
                }
            )
        return base

    @staticmethod
    def _matter_payload(matter: Matter, client: Client, *, sensitive: bool) -> dict[str, Any]:
        qualification = dict(matter.sdr_qualification or {})
        payload: dict[str, Any] = {
            "id": str(matter.id),
            "client": KlmCrmTools._client_payload(client, sensitive=sensitive),
            "practiceArea": matter.practice_area.value,
            "demandType": matter.demand_type,
            "columnId": matter.column_id.value,
            "microStatus": matter.micro_status.value,
            "priority": matter.priority.value,
            "urgency": matter.urgency.value,
            "classification": matter.classification.value,
            "leadScore": matter.lead_score,
            "docScore": matter.doc_score,
            "productionScore": matter.production_score,
            "openTasks": matter.open_tasks,
            "needsApproval": matter.needs_approval,
            "isBlocked": matter.is_blocked,
            "sensitiveDataHidden": not sensitive,
            "updatedAt": matter.updated_at.isoformat() if matter.updated_at else None,
        }
        if sensitive:
            payload.update(
                {
                    "caseNumber": matter.case_number,
                    "leadSource": matter.lead_source,
                    "claimValue": (
                        str(matter.claim_value) if matter.claim_value is not None else None
                    ),
                    "executiveSummary": matter.executive_summary,
                    "healthSubtheme": (
                        matter.health_subtheme.value if matter.health_subtheme else None
                    ),
                    "healthObjectName": matter.health_object_name,
                    "healthInsurer": matter.health_insurer,
                    "healthDenialReason": matter.health_denial_reason,
                    "medicalUrgencyHours": matter.medical_urgency_hours,
                    "nextAction": matter.next_action,
                    "qualification": qualification or None,
                    "tags": qualification.get("tags", []),
                }
            )
        return payload

    async def list_clients(self, payload: ListClientsInput) -> dict[str, Any]:
        async with self.identity.session("crm.read") as (db, actor):
            if payload.query and not actor.sensitive_data_allowed:
                raise McpToolError(
                    "MCP_SENSITIVE_SEARCH_DENIED",
                    "Client identity search requires an explicit sensitive-data grant.",
                )
            query = select(Client).where(
                Client.workspace_id == actor.workspace_id,
                Client.deleted_at.is_(None),
            )
            if payload.query:
                term = f"%{payload.query}%"
                query = query.where(
                    or_(
                        Client.name.ilike(term),
                        Client.cpf.ilike(term),
                        Client.email.ilike(term),
                        Client.phone.ilike(term),
                    )
                )
            rows = list(
                (
                    await db.scalars(
                        query.order_by(Client.created_at.desc())
                        .offset(payload.offset)
                        .limit(payload.limit + 1)
                    )
                ).all()
            )
            has_more = len(rows) > payload.limit
            rows = rows[: payload.limit]
            return {
                "ok": True,
                "items": [
                    self._client_payload(item, sensitive=actor.sensitive_data_allowed)
                    for item in rows
                ],
                "nextOffset": payload.offset + payload.limit if has_more else None,
            }

    async def get_client(self, payload: GetClientInput) -> dict[str, Any]:
        async with self.identity.session("crm.read") as (db, actor):
            client = await db.scalar(
                select(Client).where(
                    Client.id == payload.client_id,
                    Client.workspace_id == actor.workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            if client is None:
                raise McpToolError("MCP_NOT_FOUND", "Client not found in the selected workspace.")
            return {
                "ok": True,
                "client": self._client_payload(client, sensitive=actor.sensitive_data_allowed),
            }

    async def list_matters(self, payload: ListMattersInput) -> dict[str, Any]:
        async with self.identity.session("crm.read") as (db, actor):
            query = (
                select(Matter, Client)
                .join(Client, Client.id == Matter.client_id)
                .where(
                    Matter.workspace_id == actor.workspace_id,
                    Client.workspace_id == actor.workspace_id,
                    Matter.deleted_at.is_(None),
                    Client.deleted_at.is_(None),
                )
            )
            if payload.client_id:
                query = query.where(Matter.client_id == payload.client_id)
            if payload.column_id:
                try:
                    column = KanbanColumnId(payload.column_id)
                except ValueError as exc:
                    raise McpToolError("MCP_INVALID_ARGUMENT", "Unknown Kanban column_id.") from exc
                query = query.where(Matter.column_id == column)
            if payload.classification:
                query = query.where(Matter.classification == payload.classification)
            if payload.query:
                term = f"%{payload.query}%"
                conditions = [Matter.demand_type.ilike(term), Matter.case_number.ilike(term)]
                if actor.sensitive_data_allowed:
                    conditions.extend(
                        [Client.name.ilike(term), Matter.health_object_name.ilike(term)]
                    )
                query = query.where(or_(*conditions))
            rows = list(
                (
                    await db.execute(
                        query.order_by(Matter.updated_at.desc())
                        .offset(payload.offset)
                        .limit(payload.limit + 1)
                    )
                ).all()
            )
            has_more = len(rows) > payload.limit
            rows = rows[: payload.limit]
            return {
                "ok": True,
                "items": [
                    self._matter_payload(matter, client, sensitive=actor.sensitive_data_allowed)
                    for matter, client in rows
                ],
                "nextOffset": payload.offset + payload.limit if has_more else None,
            }

    async def get_matter(self, payload: GetMatterInput) -> dict[str, Any]:
        async with self.identity.session("crm.read") as (db, actor):
            row = (
                await db.execute(
                    select(Matter, Client)
                    .join(Client, Client.id == Matter.client_id)
                    .where(
                        Matter.id == payload.matter_id,
                        Matter.workspace_id == actor.workspace_id,
                        Client.workspace_id == actor.workspace_id,
                        Matter.deleted_at.is_(None),
                        Client.deleted_at.is_(None),
                    )
                )
            ).one_or_none()
            if row is None:
                raise McpToolError("MCP_NOT_FOUND", "Matter not found in the selected workspace.")
            matter, client = row
            tasks = list(
                (
                    await db.scalars(
                        select(Task)
                        .where(Task.matter_id == matter.id)
                        .order_by(Task.created_at.desc())
                        .limit(25)
                    )
                ).all()
            )
            task_rows = [
                {
                    "id": str(task.id),
                    "status": task.status.value,
                    "priority": task.priority.value if task.priority else None,
                    "dueDate": task.due_date,
                    **(
                        {"title": task.title, "description": task.description}
                        if actor.sensitive_data_allowed
                        else {}
                    ),
                }
                for task in tasks
            ]
            return {
                "ok": True,
                "matter": {
                    **self._matter_payload(
                        matter,
                        client,
                        sensitive=actor.sensitive_data_allowed,
                    ),
                    "recentTasks": task_rows,
                },
            }

    async def _matter_for_write(
        self,
        db: AsyncSession,
        actor: McpActor,
        matter_id: UUID,
    ) -> Matter:
        matter = await db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == actor.workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise McpToolError("MCP_NOT_FOUND", "Matter not found in the selected workspace.")
        return matter

    async def qualify_and_tag_matter(self, payload: QualifyMatterInput) -> dict[str, Any]:
        async with self.identity.session(
            "crm.write",
            extra_permissions=frozenset({("lead_data", "edit")}),
        ) as (db, actor):

            async def mutation() -> MutationOutcome:
                matter = await self._matter_for_write(db, actor, payload.matter_id)
                changed_fields: list[str] = []
                for field in ("lead_score", "classification", "priority", "urgency", "next_action"):
                    value = getattr(payload, field)
                    if value is not None:
                        setattr(matter, field, value)
                        changed_fields.append(field)

                qualification = dict(matter.sdr_qualification or {})
                if payload.tags is not None:
                    qualification["tags"] = payload.tags
                    changed_fields.append("tags")
                if payload.qualification_summary is not None:
                    qualification["summary"] = payload.qualification_summary
                    changed_fields.append("qualification_summary")
                qualification.update(
                    {
                        "source": "mcp",
                        "qualifiedAt": datetime.now(UTC).isoformat(),
                        "qualifiedByUserId": str(actor.user_id),
                    }
                )
                matter.sdr_qualification = qualification
                db.add(
                    TimelineEvent(
                        matter_id=matter.id,
                        agent_id=actor.user_id,
                        event_type=TimelineEventType.agent_action,
                        title="Qualificação CRM atualizada via MCP",
                        event_data={
                            "source": "mcp",
                            "changed_fields": changed_fields,
                            "tag_count": len(qualification.get("tags", [])),
                        },
                    )
                )
                await db.flush()
                return MutationOutcome(
                    entity_type="matter",
                    entity_id=matter.id,
                    summary="Qualificação e etiquetas do caso atualizadas via MCP.",
                    result={
                        "matterId": str(matter.id),
                        "changedFields": changed_fields,
                        "tagCount": len(qualification.get("tags", [])),
                    },
                )

            return await self.coordinator.execute(
                db,
                actor,
                tool_name="klm.crm.qualify_and_tag_matter",
                idempotency_key=payload.idempotency_key,
                mutation=mutation,
            )

    async def create_case_note(self, payload: CreateCaseNoteInput) -> dict[str, Any]:
        async with self.identity.session("crm.write") as (db, actor):

            async def mutation() -> MutationOutcome:
                await self._matter_for_write(db, actor, payload.matter_id)
                event = await MatterService(db).add_note(
                    payload.matter_id,
                    payload.note,
                    agent_id=actor.user_id,
                    workspace_id=actor.workspace_id,
                )
                if event is None:
                    raise McpToolError(
                        "MCP_NOT_FOUND",
                        "Matter not found in the selected workspace.",
                    )
                return MutationOutcome(
                    entity_type="timeline_event",
                    entity_id=event.id,
                    summary="Nota interna criada no caso via MCP.",
                    result={
                        "matterId": str(payload.matter_id),
                        "eventId": str(event.id),
                        "visibility": "internal",
                    },
                )

            return await self.coordinator.execute(
                db,
                actor,
                tool_name="klm.crm.create_case_note",
                idempotency_key=payload.idempotency_key,
                mutation=mutation,
            )

    async def _validate_assignee(
        self,
        db: AsyncSession,
        actor: McpActor,
        assigned_to: UUID,
    ) -> None:
        membership = await db.scalar(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == actor.workspace_id,
                WorkspaceMembership.user_id == assigned_to,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if membership is None:
            raise McpToolError(
                "MCP_INVALID_ASSIGNEE",
                "The requested assignee is not an active member of the selected workspace.",
            )

    async def create_task(self, payload: CreateTaskInput) -> dict[str, Any]:
        required_task_permissions = {("tasks", "create")}
        if payload.assigned_to is not None:
            required_task_permissions.add(("tasks", "assign"))
        async with self.identity.session(
            "crm.write",
            extra_permissions=frozenset(required_task_permissions),
        ) as (db, actor):

            async def mutation() -> MutationOutcome:
                await self._matter_for_write(db, actor, payload.matter_id)
                if payload.assigned_to:
                    await self._validate_assignee(db, actor, payload.assigned_to)
                task = await TaskService(db).create(
                    payload.matter_id,
                    TaskCreate(
                        title=payload.title,
                        description=payload.description,
                        assigned_to=payload.assigned_to,
                        due_date=payload.due_date,
                        priority=payload.priority,
                        is_ai_generated=True,
                    ),
                    workspace_id=actor.workspace_id,
                    created_by=actor.user_id,
                )
                return MutationOutcome(
                    entity_type="task",
                    entity_id=task.id,
                    summary="Tarefa jurídica criada no caso via MCP.",
                    result={
                        "matterId": str(payload.matter_id),
                        "taskId": str(task.id),
                        "status": task.status.value,
                        "assignedTo": str(task.assigned_to) if task.assigned_to else None,
                    },
                )

            return await self.coordinator.execute(
                db,
                actor,
                tool_name="klm.crm.create_task",
                idempotency_key=payload.idempotency_key,
                mutation=mutation,
            )

    async def request_human_handoff(self, payload: RequestHandoffInput) -> dict[str, Any]:
        async with self.identity.session("handoff.request") as (db, actor):

            async def mutation() -> MutationOutcome:
                await self._matter_for_write(db, actor, payload.matter_id)
                approval = await ApprovalService(db, actor.workspace_id).create(
                    ApprovalCreate(
                        matter_id=payload.matter_id,
                        approval_type="agent_handoff",
                        title="Handoff humano solicitado via MCP",
                        description=payload.reason,
                        priority=payload.priority,
                        requested_by=actor.user_id,
                    )
                )
                return MutationOutcome(
                    entity_type="approval_queue_item",
                    entity_id=approval.id,
                    summary=(
                        "Handoff humano solicitado via MCP; nenhuma ação externa "
                        "foi executada."
                    ),
                    result={
                        "matterId": str(payload.matter_id),
                        "approvalId": str(approval.id),
                        "status": approval.status.value,
                        "externalActionExecuted": False,
                    },
                )

            return await self.coordinator.execute(
                db,
                actor,
                tool_name="klm.crm.request_human_handoff",
                idempotency_key=payload.idempotency_key,
                mutation=mutation,
            )

    async def request_approval(self, payload: RequestApprovalInput) -> dict[str, Any]:
        async with self.identity.session("approval.request") as (db, actor):

            async def mutation() -> MutationOutcome:
                await self._matter_for_write(db, actor, payload.matter_id)
                approval = await ApprovalService(db, actor.workspace_id).create(
                    ApprovalCreate(
                        matter_id=payload.matter_id,
                        approval_type=payload.approval_type,
                        title=payload.title,
                        description=payload.description,
                        priority=payload.priority,
                        requested_by=actor.user_id,
                        document_version_url=payload.document_version_url,
                    )
                )
                return MutationOutcome(
                    entity_type="approval_queue_item",
                    entity_id=approval.id,
                    summary=(
                        "Aprovação humana solicitada via MCP; nenhuma ação foi "
                        "aprovada automaticamente."
                    ),
                    result={
                        "matterId": str(payload.matter_id),
                        "approvalId": str(approval.id),
                        "status": approval.status.value,
                        "autoApproved": False,
                    },
                )

            return await self.coordinator.execute(
                db,
                actor,
                tool_name="klm.crm.request_approval",
                idempotency_key=payload.idempotency_key,
                mutation=mutation,
            )
