"""Estúdio assistido que publica nos motores nativos de agentes, flows e UX."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import ConfigDocument, ConfigDocumentVersion, ConfigScope
from app.models.flow import Flow
from app.models.workspace import Workspace
from app.schemas.flow import FlowCreate
from app.schemas.platform_configuration_studio import (
    StudioAssistRequest,
    StudioDraftCreate,
    StudioDraftPageRead,
    StudioDraftRead,
    StudioDraftUpdate,
    StudioProposalRead,
    StudioPublishResult,
    StudioVersionPageRead,
    StudioVersionRead,
)
from app.services.configuration_service import ConfigurationService, ConfigValidationError
from app.services.flow_service import FlowService
from app.services.klm_agent_service import KNOWN_AGENT_TOOL_KEYS

STUDIO_NAMESPACE_PREFIX = "studio."
CUSTOM_AGENTS_NAMESPACE = "agent.custom_definitions"
EXPERIENCE_NAMESPACE = "workspace.experience"
SAFE_PARENT_KEYS = frozenset(
    {"orquestrador", "juridico", "financeiro", "marketing", "secretaria"}
)
SAFE_AGENT_TOOLS = frozenset(
    {
        "conversation_rag",
        "document_rag",
        "manual_revision_check",
        "canonical_identity",
        "crm_read",
        "nota_interna",
        "tarefas",
        "templates",
        "checklist",
        "kpis",
        "agenda",
        "flow_progress",
    }
) & KNOWN_AGENT_TOOL_KEYS


def _slug(value: str, *, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return (cleaned or fallback)[:72]


class PlatformConfigurationStudioService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.configurations = ConfigurationService(db)

    async def _require_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
        )
        if workspace is None:
            raise LookupError("Conta contratante não encontrada.")
        return workspace

    @staticmethod
    def _namespace(kind: str, key: str) -> str:
        return f"{STUDIO_NAMESPACE_PREFIX}{kind}.{_slug(key, fallback=kind)}"

    @staticmethod
    def _agent_tools(prompt: str) -> list[str]:
        requested = {"conversation_rag", "manual_revision_check"}
        lowered = prompt.lower()
        keyword_tools = {
            "document": "document_rag",
            "crm": "crm_read",
            "agenda": "agenda",
            "tarefa": "tarefas",
            "indicador": "kpis",
            "checklist": "checklist",
            "fluxo": "flow_progress",
            "nota": "nota_interna",
        }
        requested.update(
            tool for keyword, tool in keyword_tools.items() if keyword in lowered
        )
        return sorted(requested & SAFE_AGENT_TOOLS)

    async def assist(
        self,
        *,
        workspace_id: UUID,
        request: StudioAssistRequest,
    ) -> StudioProposalRead:
        await self._require_workspace(workspace_id)
        title = (request.title or request.prompt.splitlines()[0]).strip()[:160]
        key = _slug(title, fallback=request.kind)
        warnings: list[str] = []
        suggestions = [
            "Revise a proposta antes de publicar.",
            "Teste em modo local e confirme os gates humanos.",
        ]
        if request.attachment_names:
            suggestions.append(
                f"{len(request.attachment_names)} anexo(s) foram referenciados; o conteúdo não foi executado."
            )

        if request.kind == "agent":
            payload = {
                "key": key,
                "name": title,
                "role": "Agente personalizado",
                "group": "Personalizados",
                "description": request.prompt.strip(),
                "parentAgentKey": "orquestrador",
                "tools": self._agent_tools(request.prompt),
                "gate": "Ações externas, jurídicas, financeiras e irreversíveis exigem aprovação humana.",
                "baseState": "paused",
                "taskClass": "medium",
                "canRequestHuman": False,
                "canWriteCrm": False,
                "canCreateDraft": True,
            }
            summary = "Agente subordinado ao Orquestrador, publicado pausado e com ferramentas mínimas."
        elif request.kind == "agent_package":
            package_tools = self._agent_tools(request.prompt)
            specialist_key = _slug(f"{key}_especialista", fallback="especialista")
            reviewer_key = _slug(f"{key}_revisor", fallback="revisor")
            payload = {
                "key": key,
                "name": title,
                "description": request.prompt.strip(),
                "agents": [
                    {
                        "key": specialist_key,
                        "name": f"{title} · Especialista",
                        "role": "Especialista do pacote",
                        "group": title,
                        "description": (
                            f"Executa a análise especializada deste pacote: {request.prompt.strip()}"
                        ),
                        "parentAgentKey": "orquestrador",
                        "tools": package_tools,
                        "gate": "Ações externas, jurídicas, financeiras e irreversíveis exigem aprovação humana.",
                        "baseState": "paused",
                        "taskClass": "medium",
                        "canRequestHuman": False,
                        "canWriteCrm": False,
                        "canCreateDraft": True,
                    },
                    {
                        "key": reviewer_key,
                        "name": f"{title} · Revisor",
                        "role": "Revisor do pacote",
                        "group": title,
                        "description": (
                            "Confere a saída do especialista, identifica lacunas e devolve ao "
                            "Orquestrador para decisão."
                        ),
                        "parentAgentKey": "orquestrador",
                        "tools": sorted(
                            {"conversation_rag", "manual_revision_check"} & SAFE_AGENT_TOOLS
                        ),
                        "gate": "Não publica, envia ou chama humanos diretamente.",
                        "baseState": "paused",
                        "taskClass": "medium",
                        "canRequestHuman": False,
                        "canWriteCrm": False,
                        "canCreateDraft": True,
                    },
                ],
                "governance": {
                    "orchestratorOnly": True,
                    "humanApprovalRequired": True,
                    "publishState": "paused",
                },
            }
            summary = (
                "Pacote nativo com especialista e revisor, publicado de forma atômica e pausado."
            )
        elif request.kind == "flow":
            payload = {
                "name": title,
                "description": request.prompt.strip(),
                "practice_area": "universal",
                "trigger": "manual",
                "trigger_config": {"source": "blackbox_studio"},
                "nodes": [
                    {
                        "id": "studio_start",
                        "type": "trigger",
                        "position": {"x": 120, "y": 120},
                        "config": {"event": "manual", "label": "Início aprovado"},
                    },
                    {
                        "id": "studio_review",
                        "type": "human_pause",
                        "position": {"x": 120, "y": 300},
                        "config": {
                            "reason": "Revisar a execução proposta pelo Estúdio Blackbox",
                            "label": "Aprovação humana",
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "studio_start_review",
                        "source": "studio_start",
                        "target": "studio_review",
                    }
                ],
                "is_active": False,
            }
            summary = "Flow nativo criado inativo, com gatilho manual e aprovação humana inicial."
        else:
            payload = {
                "name": title,
                "brief": request.prompt.strip(),
                "welcomeMessage": "",
                "supportLabel": "Ajuda e suporte",
                "emptyStates": {},
                "navigationLabels": {},
                "featureVisibility": {},
            }
            summary = "Personalização aditiva da experiência, sem alterar contratos visuais globais."

        if request.current_payload:
            payload = {**payload, **request.current_payload}
        return StudioProposalRead(
            kind=request.kind,
            title=title,
            key=key,
            payload=payload,
            summary=summary,
            suggestions=suggestions,
            warnings=warnings,
        )

    async def list_drafts(
        self,
        *,
        workspace_id: UUID,
        kind: str | None = None,
    ) -> list[StudioDraftRead]:
        await self._require_workspace(workspace_id)
        query = select(ConfigDocument).where(
            ConfigDocument.workspace_id == workspace_id,
            ConfigDocument.namespace.like(f"{STUDIO_NAMESPACE_PREFIX}%"),
        )
        if kind:
            query = query.where(
                ConfigDocument.namespace.like(f"{STUDIO_NAMESPACE_PREFIX}{kind}.%")
            )
        rows = (
            await self.db.scalars(
                query.order_by(ConfigDocument.updated_at.desc(), ConfigDocument.id.desc())
            )
        ).all()
        return [self._read(row) for row in rows]

    async def page_drafts(
        self,
        *,
        workspace_id: UUID,
        kind: str | None = None,
        limit: int,
        offset: int,
    ) -> StudioDraftPageRead:
        await self._require_workspace(workspace_id)
        filters = (
            ConfigDocument.workspace_id == workspace_id,
            ConfigDocument.namespace.like(f"{STUDIO_NAMESPACE_PREFIX}%"),
        )
        query = select(ConfigDocument).where(*filters)
        count_query = select(func.count(ConfigDocument.id)).where(*filters)
        if kind:
            kind_filter = ConfigDocument.namespace.like(
                f"{STUDIO_NAMESPACE_PREFIX}{kind}.%"
            )
            query = query.where(kind_filter)
            count_query = count_query.where(kind_filter)
        total = int((await self.db.scalar(count_query)) or 0)
        rows = (
            await self.db.scalars(
                query.order_by(ConfigDocument.updated_at.desc(), ConfigDocument.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        items = [self._read(row) for row in rows]
        return StudioDraftPageRead(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    async def create_draft(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        data: StudioDraftCreate,
    ) -> StudioDraftRead:
        await self._require_workspace(workspace_id)
        key = _slug(data.key, fallback=data.kind)
        self._validate_payload(data.kind, data.payload)
        document = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=self._namespace(data.kind, key),
            payload={
                "kind": data.kind,
                "key": key,
                "title": data.title.strip(),
                "status": "draft",
                "definition": data.payload,
                "publication": None,
            },
            actor_user_id=actor_user_id,
            reason=data.reason or "Rascunho criado no Estúdio Blackbox",
        )
        await self.db.refresh(document)
        return self._read(document)

    async def update_draft(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        actor_user_id: UUID,
        data: StudioDraftUpdate,
    ) -> StudioDraftRead:
        document = await self._get_draft(workspace_id, document_id)
        kind = str(document.payload.get("kind") or "")
        self._validate_payload(kind, data.payload)
        updated = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=document.namespace,
            payload={
                **document.payload,
                "title": (data.title or str(document.payload.get("title") or "")).strip(),
                "status": "draft",
                "definition": data.payload,
                "publication": None,
            },
            actor_user_id=actor_user_id,
            expected_etag=data.expected_etag,
            reason=data.reason or "Rascunho atualizado no Estúdio Blackbox",
        )
        await self.db.refresh(updated)
        return self._read(updated)

    async def list_versions(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
    ) -> list[StudioVersionRead]:
        document = await self._get_draft(workspace_id, document_id)
        rows = (
            await self.db.scalars(
                select(ConfigDocumentVersion)
                .where(ConfigDocumentVersion.document_id == document.id)
                .order_by(ConfigDocumentVersion.version.desc())
            )
        ).all()
        return [
            StudioVersionRead(
                version=row.version,
                payload=dict(row.payload or {}),
                reason=row.reason,
                created_by_user_id=row.created_by_user_id,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def page_versions(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        limit: int,
        offset: int,
    ) -> StudioVersionPageRead:
        document = await self._get_draft(workspace_id, document_id)
        total = int(
            (
                await self.db.scalar(
                    select(func.count(ConfigDocumentVersion.id)).where(
                        ConfigDocumentVersion.document_id == document.id
                    )
                )
            )
            or 0
        )
        rows = (
            await self.db.scalars(
                select(ConfigDocumentVersion)
                .where(ConfigDocumentVersion.document_id == document.id)
                .order_by(
                    ConfigDocumentVersion.version.desc(),
                    ConfigDocumentVersion.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        items = [
            StudioVersionRead(
                version=row.version,
                payload=dict(row.payload or {}),
                reason=row.reason,
                created_by_user_id=row.created_by_user_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
        return StudioVersionPageRead(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    async def restore_draft(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        actor_user_id: UUID,
        target_version: int,
        expected_etag: str | None,
        reason: str,
    ) -> StudioDraftRead:
        document = await self._get_draft(workspace_id, document_id)
        restored = await self.configurations.rollback_tenant(
            document=document,
            target_version=target_version,
            actor_user_id=actor_user_id,
            expected_etag=expected_etag,
        )
        restored.payload = {
            **dict(restored.payload or {}),
            "status": "draft",
            "publication": None,
        }
        # Persiste o estado restaurado como uma nova versão auditável, em vez de
        # reescrever silenciosamente o histórico.
        restored = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=restored.namespace,
            payload=restored.payload,
            actor_user_id=actor_user_id,
            expected_etag=restored.etag,
            reason=reason,
        )
        await self.db.refresh(restored)
        return self._read(restored)

    async def publish(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        actor_user_id: UUID,
        expected_etag: str | None,
        reason: str | None,
    ) -> StudioPublishResult:
        document = await self._get_draft(workspace_id, document_id)
        payload = dict(document.payload)
        publication = payload.get("publication")
        if payload.get("status") == "published" and isinstance(publication, dict):
            return StudioPublishResult(
                draft=self._read(document),
                target_type=str(publication.get("targetType") or payload["kind"]),
                target_id=str(publication.get("targetId") or ""),
                target_version=int(publication.get("targetVersion") or 1),
                already_published=True,
            )

        kind = str(payload.get("kind") or "")
        definition = dict(payload.get("definition") or {})
        self._validate_payload(kind, definition)
        now = datetime.now(timezone.utc).isoformat()
        if kind == "agent":
            target_id, target_version = await self._publish_agent(
                workspace_id, actor_user_id, definition
            )
        elif kind == "agent_package":
            target_id, target_version = await self._publish_agent_package(
                workspace_id, actor_user_id, definition
            )
        elif kind == "flow":
            target_id, target_version = await self._publish_flow(
                workspace_id, actor_user_id, document.id, definition
            )
        elif kind == "experience":
            target_id, target_version = await self._publish_experience(
                workspace_id, actor_user_id, definition
            )
        else:
            raise ConfigValidationError("Tipo de rascunho desconhecido.")

        publication = {
            "targetType": kind,
            "targetId": target_id,
            "targetVersion": target_version,
            "publishedAt": now,
            "publishedByUserId": str(actor_user_id),
        }
        updated = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=document.namespace,
            payload={**payload, "status": "published", "publication": publication},
            actor_user_id=actor_user_id,
            expected_etag=expected_etag,
            reason=reason or "Configuração publicada pelo Estúdio Blackbox",
        )
        await self.db.refresh(updated)
        return StudioPublishResult(
            draft=self._read(updated),
            target_type=kind,
            target_id=target_id,
            target_version=target_version,
        )

    async def _publish_agent(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        definition: dict[str, Any],
    ) -> tuple[str, int]:
        current = await self.configurations.get_tenant_document(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=CUSTOM_AGENTS_NAMESPACE,
        )
        agents = dict((current.payload if current else {}).get("agents") or {})
        key = str(definition["key"])
        agents[key] = definition
        target = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=CUSTOM_AGENTS_NAMESPACE,
            payload={"agents": agents},
            actor_user_id=actor_user_id,
            expected_etag=current.etag if current else None,
            reason=f"Agente personalizado {key} publicado pelo Blackbox",
        )
        return key, target.current_version

    async def _publish_agent_package(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        definition: dict[str, Any],
    ) -> tuple[str, int]:
        current = await self.configurations.get_tenant_document(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=CUSTOM_AGENTS_NAMESPACE,
        )
        agents = dict((current.payload if current else {}).get("agents") or {})
        for agent in definition["agents"]:
            agents[str(agent["key"])] = dict(agent)
        target = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=CUSTOM_AGENTS_NAMESPACE,
            payload={"agents": agents},
            actor_user_id=actor_user_id,
            expected_etag=current.etag if current else None,
            reason=f"Pacote de agentes {definition['key']} publicado pelo Blackbox",
        )
        return str(definition["key"]), target.current_version

    async def _publish_flow(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        draft_id: UUID,
        definition: dict[str, Any],
    ) -> tuple[str, int]:
        flows = (
            await self.db.scalars(
                select(Flow).where(
                    Flow.workspace_id == workspace_id,
                    Flow.is_template.is_(False),
                )
            )
        ).all()
        for flow in flows:
            if str((flow.trigger_config or {}).get("studioDraftId") or "") == str(draft_id):
                return str(flow.id), int(flow.current_revision or 1)
        trigger_config = dict(definition.get("trigger_config") or {})
        trigger_config["studioDraftId"] = str(draft_id)
        flow = await FlowService(self.db).create_flow(
            workspace_id,
            FlowCreate(
                name=str(definition["name"]),
                description=definition.get("description"),
                practice_area=str(definition.get("practice_area") or "universal"),
                trigger=str(definition.get("trigger") or "manual"),
                trigger_config=trigger_config,
                nodes=list(definition.get("nodes") or []),
                edges=list(definition.get("edges") or []),
                is_active=bool(definition.get("is_active", False)),
            ),
            actor_user_id=actor_user_id,
            commit=False,
        )
        return str(flow.id), int(flow.current_revision or 1)

    async def _publish_experience(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        definition: dict[str, Any],
    ) -> tuple[str, int]:
        current = await self.configurations.get_tenant_document(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=EXPERIENCE_NAMESPACE,
        )
        target = await self.configurations.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=EXPERIENCE_NAMESPACE,
            payload=definition,
            actor_user_id=actor_user_id,
            expected_etag=current.etag if current else None,
            reason="Experiência da conta publicada pelo Blackbox",
        )
        return str(target.id), target.current_version

    async def _get_draft(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> ConfigDocument:
        document = await self.db.scalar(
            select(ConfigDocument).where(
                ConfigDocument.id == document_id,
                ConfigDocument.workspace_id == workspace_id,
                ConfigDocument.namespace.like(f"{STUDIO_NAMESPACE_PREFIX}%"),
            )
        )
        if document is None:
            raise LookupError("Rascunho não encontrado para esta conta.")
        return document

    @staticmethod
    def _validate_payload(kind: str, payload: dict[str, Any]) -> None:
        if kind == "agent":
            key = _slug(str(payload.get("key") or ""), fallback="")
            if not key or key in {"orquestrador", "hermes", "auditor", "ti", "qualidade"}:
                raise ConfigValidationError("A chave do agente é inválida ou reservada.")
            parent = str(payload.get("parentAgentKey") or "orquestrador")
            if parent not in SAFE_PARENT_KEYS:
                raise ConfigValidationError("O agente deve usar uma hierarquia segura.")
            tools = [str(item) for item in payload.get("tools") or []]
            unknown = sorted(set(tools) - KNOWN_AGENT_TOOL_KEYS)
            if unknown:
                raise ConfigValidationError(
                    f"Ferramentas fora do catálogo nativo: {', '.join(unknown)}"
                )
            if payload.get("canRequestHuman"):
                raise ConfigValidationError(
                    "Agentes personalizados não chamam o humano diretamente; use o Orquestrador."
                )
            payload["key"] = key
            payload["parentAgentKey"] = parent
            payload["baseState"] = str(payload.get("baseState") or "paused")
        elif kind == "agent_package":
            package_key = _slug(str(payload.get("key") or ""), fallback="")
            agents = payload.get("agents")
            if not package_key:
                raise ConfigValidationError("A chave do pacote de agentes é inválida.")
            if not isinstance(agents, list) or not 1 <= len(agents) <= 20:
                raise ConfigValidationError("O pacote deve conter entre 1 e 20 agentes.")
            keys: list[str] = []
            for agent in agents:
                if not isinstance(agent, dict):
                    raise ConfigValidationError("Cada agente do pacote deve ter uma definição.")
                PlatformConfigurationStudioService._validate_payload("agent", agent)
                keys.append(str(agent["key"]))
            if len(keys) != len(set(keys)):
                raise ConfigValidationError("O pacote contém chaves de agentes duplicadas.")
            payload["key"] = package_key
            payload["governance"] = {
                **dict(payload.get("governance") or {}),
                "orchestratorOnly": True,
                "humanApprovalRequired": True,
                "publishState": "paused",
            }
        elif kind == "flow":
            FlowCreate(**payload)
        elif kind == "experience":
            ConfigurationService.validate_payload(payload)
        else:
            raise ConfigValidationError("Tipo de configuração desconhecido.")

    @staticmethod
    def _read(document: ConfigDocument) -> StudioDraftRead:
        payload = dict(document.payload or {})
        return StudioDraftRead(
            id=document.id,
            workspace_id=document.workspace_id,
            kind=payload.get("kind"),
            key=str(payload.get("key") or ""),
            title=str(payload.get("title") or ""),
            status=str(payload.get("status") or "draft"),
            payload=dict(payload.get("definition") or {}),
            current_version=document.current_version,
            etag=document.etag,
            updated_at=document.updated_at,
            publication=payload.get("publication"),
        )
