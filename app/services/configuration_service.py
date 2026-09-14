"""Hierarchical, versioned configuration with deterministic resolution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import (
    ConfigDocument,
    ConfigDocumentVersion,
    ConfigScope,
    PlatformConfigDocument,
    PlatformConfigDocumentVersion,
)
from app.models.oss_native import assert_secret_free_json
from app.models.workspace_membership import WorkspaceMembership

_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,159}$")


class ConfigConflictError(RuntimeError):
    """Raised when an optimistic-concurrency precondition is stale."""


class ConfigValidationError(ValueError):
    """Raised when a scope or payload violates the configuration contract."""


@dataclass(frozen=True)
class ConfigLayer:
    scope: str
    scope_key: str
    version: int
    etag: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ResolvedConfig:
    namespace: str
    effective: dict[str, Any]
    etag: str
    layers: tuple[ConfigLayer, ...]


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    return normalized.strip('"')


class ConfigurationService:
    """Persist configuration layers and resolve platform-to-user inheritance."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def scope_key(
        scope: ConfigScope,
        *,
        namespace: str,
        role_key: str | None = None,
        user_id: UUID | None = None,
    ) -> str:
        if not _NAMESPACE_PATTERN.fullmatch(namespace):
            raise ConfigValidationError("Invalid configuration namespace.")
        if scope is ConfigScope.workspace:
            if role_key is not None or user_id is not None:
                raise ConfigValidationError("Workspace scope cannot target a role or user.")
            return f"workspace:{namespace}"
        if scope is ConfigScope.role:
            if not role_key or user_id is not None:
                raise ConfigValidationError("Role scope requires only role_key.")
            return f"role:{role_key}:{namespace}"
        if scope is ConfigScope.user:
            if user_id is None or role_key is not None:
                raise ConfigValidationError("User scope requires only user_id.")
            return f"user:{user_id}:{namespace}"
        raise ConfigValidationError("Platform configuration uses the platform writer.")

    @staticmethod
    def validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return assert_secret_free_json(
                payload,
                field_name="configuration payload",
                max_bytes=128 * 1024,
            )
        except (TypeError, ValueError) as exc:
            raise ConfigValidationError(str(exc)) from exc

    async def get_tenant_document(
        self,
        *,
        workspace_id: UUID,
        scope: ConfigScope,
        namespace: str,
        role_key: str | None = None,
        user_id: UUID | None = None,
    ) -> ConfigDocument | None:
        key = self.scope_key(
            scope,
            namespace=namespace,
            role_key=role_key,
            user_id=user_id,
        )
        return await self.db.scalar(
            select(ConfigDocument).where(
                ConfigDocument.workspace_id == workspace_id,
                ConfigDocument.scope_key == key,
            )
        )

    async def write_tenant(
        self,
        *,
        workspace_id: UUID,
        scope: ConfigScope,
        namespace: str,
        payload: Mapping[str, Any],
        actor_user_id: UUID | None,
        role_key: str | None = None,
        user_id: UUID | None = None,
        schema_version: str = "1.0",
        expected_etag: str | None = None,
        reason: str | None = None,
    ) -> ConfigDocument:
        clean_payload = self.validate_payload(payload)
        key = self.scope_key(
            scope,
            namespace=namespace,
            role_key=role_key,
            user_id=user_id,
        )
        if scope is ConfigScope.user:
            membership_id = await self.db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user_id,
                )
            )
            if membership_id is None:
                raise ConfigValidationError(
                    "User-scoped configuration requires workspace membership."
                )
        document = await self.db.scalar(
            select(ConfigDocument)
            .where(
                ConfigDocument.workspace_id == workspace_id,
                ConfigDocument.scope_key == key,
            )
            .with_for_update()
        )
        normalized_etag = _normalize_etag(expected_etag)
        if document is None:
            if normalized_etag is not None:
                raise ConfigConflictError("Configuration was created or removed concurrently.")
            version = 1
            etag = _payload_hash(clean_payload)
            document = ConfigDocument(
                workspace_id=workspace_id,
                namespace=namespace,
                scope=scope,
                scope_key=key,
                role_key=role_key,
                user_id=user_id,
                schema_version=schema_version,
                current_version=version,
                etag=etag,
                payload=clean_payload,
                updated_by_user_id=actor_user_id,
            )
            self.db.add(document)
            await self.db.flush()
        else:
            if normalized_etag is not None and normalized_etag != document.etag:
                raise ConfigConflictError("Configuration changed since it was loaded.")
            etag = _payload_hash(clean_payload)
            if etag == document.etag and schema_version == document.schema_version:
                return document
            version = document.current_version + 1
            document.payload = clean_payload
            document.etag = etag
            document.schema_version = schema_version
            document.current_version = version
            document.updated_by_user_id = actor_user_id

        self.db.add(
            ConfigDocumentVersion(
                document_id=document.id,
                version=version,
                schema_version=schema_version,
                payload=clean_payload,
                payload_hash=etag,
                reason=(reason or "Configuration updated")[:240],
                created_by_user_id=actor_user_id,
            )
        )
        await self.db.flush()
        await self.db.refresh(document)
        return document

    async def write_platform(
        self,
        *,
        namespace: str,
        payload: Mapping[str, Any],
        actor_user_id: UUID | None,
        schema_version: str = "1.0",
        expected_etag: str | None = None,
        reason: str | None = None,
    ) -> PlatformConfigDocument:
        if not _NAMESPACE_PATTERN.fullmatch(namespace):
            raise ConfigValidationError("Invalid configuration namespace.")
        clean_payload = self.validate_payload(payload)
        document = await self.db.scalar(
            select(PlatformConfigDocument)
            .where(PlatformConfigDocument.namespace == namespace)
            .with_for_update()
        )
        normalized_etag = _normalize_etag(expected_etag)
        if document is None:
            if normalized_etag is not None:
                raise ConfigConflictError("Configuration was created or removed concurrently.")
            version = 1
            etag = _payload_hash(clean_payload)
            document = PlatformConfigDocument(
                namespace=namespace,
                schema_version=schema_version,
                current_version=version,
                etag=etag,
                payload=clean_payload,
                updated_by_user_id=actor_user_id,
            )
            self.db.add(document)
            await self.db.flush()
        else:
            if normalized_etag is not None and normalized_etag != document.etag:
                raise ConfigConflictError("Configuration changed since it was loaded.")
            etag = _payload_hash(clean_payload)
            if etag == document.etag and schema_version == document.schema_version:
                return document
            version = document.current_version + 1
            document.payload = clean_payload
            document.etag = etag
            document.schema_version = schema_version
            document.current_version = version
            document.updated_by_user_id = actor_user_id

        self.db.add(
            PlatformConfigDocumentVersion(
                document_id=document.id,
                version=version,
                schema_version=schema_version,
                payload=clean_payload,
                payload_hash=etag,
                reason=(reason or "Platform configuration updated")[:240],
                created_by_user_id=actor_user_id,
            )
        )
        await self.db.flush()
        await self.db.refresh(document)
        return document

    async def resolve(
        self,
        *,
        namespace: str,
        workspace_id: UUID,
        role_key: str | None = None,
        user_id: UUID | None = None,
        contract_defaults: Mapping[str, Any] | None = None,
    ) -> ResolvedConfig:
        if not _NAMESPACE_PATTERN.fullmatch(namespace):
            raise ConfigValidationError("Invalid configuration namespace.")
        layers: list[ConfigLayer] = []
        effective: dict[str, Any] = {}
        if contract_defaults:
            defaults = self.validate_payload(contract_defaults)
            default_etag = _payload_hash(defaults)
            layers.append(ConfigLayer("contract", "contract", 1, default_etag, defaults))
            effective = _deep_merge(effective, defaults)

        platform = await self.db.scalar(
            select(PlatformConfigDocument).where(PlatformConfigDocument.namespace == namespace)
        )
        if platform:
            layer = ConfigLayer(
                ConfigScope.platform.value,
                f"platform:{namespace}",
                platform.current_version,
                platform.etag,
                deepcopy(platform.payload),
            )
            layers.append(layer)
            effective = _deep_merge(effective, layer.payload)

        candidates = [
            self.scope_key(ConfigScope.workspace, namespace=namespace),
        ]
        if role_key:
            candidates.append(
                self.scope_key(ConfigScope.role, namespace=namespace, role_key=role_key)
            )
        if user_id:
            candidates.append(
                self.scope_key(ConfigScope.user, namespace=namespace, user_id=user_id)
            )
        rows = (
            await self.db.scalars(
                select(ConfigDocument).where(
                    ConfigDocument.workspace_id == workspace_id,
                    ConfigDocument.scope_key.in_(candidates),
                )
            )
        ).all()
        by_key = {row.scope_key: row for row in rows}
        for key in candidates:
            row = by_key.get(key)
            if row is None:
                continue
            layer = ConfigLayer(
                row.scope.value,
                row.scope_key,
                row.current_version,
                row.etag,
                deepcopy(row.payload),
            )
            layers.append(layer)
            effective = _deep_merge(effective, layer.payload)

        return ResolvedConfig(
            namespace=namespace,
            effective=effective,
            etag=_payload_hash(effective),
            layers=tuple(layers),
        )

    async def history(self, document_id: UUID) -> list[ConfigDocumentVersion]:
        return list(
            (
                await self.db.scalars(
                    select(ConfigDocumentVersion)
                    .where(ConfigDocumentVersion.document_id == document_id)
                    .order_by(ConfigDocumentVersion.version.desc())
                )
            ).all()
        )

    async def platform_history(self, document_id: UUID) -> list[PlatformConfigDocumentVersion]:
        return list(
            (
                await self.db.scalars(
                    select(PlatformConfigDocumentVersion)
                    .where(PlatformConfigDocumentVersion.document_id == document_id)
                    .order_by(PlatformConfigDocumentVersion.version.desc())
                )
            ).all()
        )

    async def page_platform_history(
        self,
        document_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[PlatformConfigDocumentVersion], int]:
        total = int(
            await self.db.scalar(
                select(func.count(PlatformConfigDocumentVersion.id)).where(
                    PlatformConfigDocumentVersion.document_id == document_id
                )
            )
            or 0
        )
        items = list(
            (
                await self.db.scalars(
                    select(PlatformConfigDocumentVersion)
                    .where(PlatformConfigDocumentVersion.document_id == document_id)
                    .order_by(
                        PlatformConfigDocumentVersion.version.desc(),
                        PlatformConfigDocumentVersion.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return items, total

    async def rollback_platform(
        self,
        *,
        document: PlatformConfigDocument,
        target_version: int,
        actor_user_id: UUID | None,
        expected_etag: str | None,
    ) -> PlatformConfigDocument:
        target = await self.db.scalar(
            select(PlatformConfigDocumentVersion).where(
                PlatformConfigDocumentVersion.document_id == document.id,
                PlatformConfigDocumentVersion.version == target_version,
            )
        )
        if target is None:
            raise ConfigValidationError("Configuration version does not exist.")
        return await self.write_platform(
            namespace=document.namespace,
            payload=target.payload,
            actor_user_id=actor_user_id,
            schema_version=target.schema_version,
            expected_etag=expected_etag,
            reason=f"Rollback to version {target_version}",
        )

    async def rollback_tenant(
        self,
        *,
        document: ConfigDocument,
        target_version: int,
        actor_user_id: UUID | None,
        expected_etag: str | None,
    ) -> ConfigDocument:
        target = await self.db.scalar(
            select(ConfigDocumentVersion).where(
                ConfigDocumentVersion.document_id == document.id,
                ConfigDocumentVersion.version == target_version,
            )
        )
        if target is None:
            raise ConfigValidationError("Configuration version does not exist.")
        return await self.write_tenant(
            workspace_id=document.workspace_id,
            scope=document.scope,
            namespace=document.namespace,
            payload=target.payload,
            actor_user_id=actor_user_id,
            role_key=document.role_key,
            user_id=document.user_id,
            schema_version=target.schema_version,
            expected_etag=expected_etag,
            reason=f"Rollback to version {target_version}",
        )
