"""
Service do site público institucional.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.public_site_defaults import get_default_public_site_documents
from app.models.public_site_document import PublicSiteDocument
from app.schemas.public_site import (
    PlatformSiteDocumentEnvelopeRead,
    PublicFaqDocumentRead,
    PublicFaqDocumentUpdate,
    PublicHomeDocumentRead,
    PublicHomeDocumentUpdate,
    PublicHomePayloadRead,
    PublicLegalDocumentRead,
    PublicLegalDocumentUpdate,
    PublicMediaLibraryRead,
    PublicMediaLibraryUpdate,
    PublicSiteSettingsRead,
    PublicSiteSettingsUpdate,
)


class PublicSiteService:
    VALID_KEYS = {"settings", "home", "faq", "terms", "privacy", "media"}
    LEGAL_KEYS = {"terms", "privacy"}
    LEGACY_BRAND_REPLACEMENTS = (
        ("contato@desburocratize.com", "contato@klmadvogados.com"),
        ("desburocratize.com", "klmadvogados.com"),
        ("5562999999999", "5561993328108"),
        ("A Desburocratize", "O Meu Executivo"),
        ("a Desburocratize", "o Meu Executivo"),
        ("da Desburocratize", "do Meu Executivo"),
        ("Na Desburocratize", "No Meu Executivo"),
        ("Com a Desburocratize", "Com o Meu Executivo"),
        ("pela Desburocratize", "pelo Meu Executivo"),
        ("DESBUROCRATIZE", "MEU EXECUTIVO"),
        ("Desburocratize", "Meu Executivo"),
        ("desburocratize", "meu executivo"),
        ("A KLM Hub", "O Meu Executivo"),
        ("a KLM Hub", "o Meu Executivo"),
        ("da KLM Hub", "do Meu Executivo"),
        ("Na KLM Hub", "No Meu Executivo"),
        ("Com a KLM Hub", "Com o Meu Executivo"),
        ("pela KLM Hub", "pelo Meu Executivo"),
        ("KLM HUB", "MEU EXECUTIVO"),
        ("KLM Hub", "Meu Executivo"),
        ("klm hub", "meu executivo"),
        ("KLM Advogados", "Meu Executivo"),
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    async def ensure_defaults(self) -> None:
        defaults = get_default_public_site_documents()
        default_rows = [
            {
                "key": key,
                "title": payload["title"],
                "description": payload.get("description"),
                "draft_content": payload["content"],
                "published_content": payload["content"],
                "version": 1,
                "published_at": datetime.now(UTC),
            }
            for key, payload in defaults.items()
        ]
        dialect_name = self.db.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = postgresql_insert(PublicSiteDocument).values(default_rows)
            statement = statement.on_conflict_do_nothing(index_elements=[PublicSiteDocument.key])
        elif dialect_name == "sqlite":
            statement = sqlite_insert(PublicSiteDocument).values(default_rows)
            statement = statement.on_conflict_do_nothing(index_elements=[PublicSiteDocument.key])
        else:  # pragma: no cover - production and tests use PostgreSQL/SQLite.
            raise RuntimeError(
                f"Unsupported database dialect for public site defaults: {dialect_name}"
            )
        insert_result = await self.db.execute(statement)
        changed = bool(insert_result.rowcount)

        result = await self.db.execute(select(PublicSiteDocument))
        existing = {doc.key: doc for doc in result.scalars().all()}
        for doc in existing.values():
            normalized_draft = self._normalize_legacy_brand(doc.draft_content)
            normalized_published = self._normalize_legacy_brand(doc.published_content)
            if (
                normalized_draft == doc.draft_content
                and normalized_published == doc.published_content
            ):
                continue
            doc.draft_content = normalized_draft
            doc.published_content = normalized_published
            doc.version += 1
            changed = True

        # Never commit a caller's unrelated mutation.  The old implementation
        # only closed the transaction when defaults or brand normalization
        # changed; preserving that boundary keeps audit + mutation atomic.
        if changed:
            await self.db.commit()

    @classmethod
    def _normalize_legacy_brand(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._normalize_legacy_brand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._normalize_legacy_brand(item) for item in value]
        if not isinstance(value, str):
            return value
        normalized = value
        for old, new in cls.LEGACY_BRAND_REPLACEMENTS:
            normalized = normalized.replace(old, new)
        return normalized

    async def get_public_site_settings(self) -> PublicSiteSettingsRead:
        doc = await self._get_doc("settings")
        return PublicSiteSettingsRead.model_validate(doc.published_content)

    async def get_public_home(self) -> PublicHomePayloadRead:
        settings = await self.get_public_site_settings()
        home_doc = await self._get_doc("home")
        faq_doc = await self._get_doc("faq")
        return PublicHomePayloadRead(
            settings=settings,
            home=PublicHomeDocumentRead.model_validate(home_doc.published_content),
            faq=PublicFaqDocumentRead.model_validate(faq_doc.published_content),
        )

    async def get_public_legal(self, kind: str) -> PublicLegalDocumentRead:
        key = self.validate_legal_key(kind)
        doc = await self._get_doc(key)
        return PublicLegalDocumentRead.model_validate(doc.published_content)

    async def get_admin_document(self, key: str) -> PlatformSiteDocumentEnvelopeRead:
        doc = await self._get_doc(self.validate_key(key))
        return PlatformSiteDocumentEnvelopeRead(
            key=doc.key,
            title=doc.title,
            description=doc.description,
            version=doc.version,
            updated_at=doc.updated_at,
            published_at=doc.published_at,
            draft=self._serialize_document_content(doc.key, doc.draft_content),
            published=self._serialize_document_content(doc.key, doc.published_content),
        )

    async def update_settings(
        self,
        payload: PublicSiteSettingsUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformSiteDocumentEnvelopeRead:
        return await self._update_document(
            "settings", payload.model_dump(by_alias=False), user_id, commit=commit
        )

    async def update_home(
        self,
        payload: PublicHomeDocumentUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformSiteDocumentEnvelopeRead:
        return await self._update_document(
            "home", payload.model_dump(by_alias=False), user_id, commit=commit
        )

    async def update_faq(
        self,
        payload: PublicFaqDocumentUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformSiteDocumentEnvelopeRead:
        return await self._update_document(
            "faq", payload.model_dump(by_alias=False), user_id, commit=commit
        )

    async def update_media(
        self,
        payload: PublicMediaLibraryUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformSiteDocumentEnvelopeRead:
        return await self._update_document(
            "media", payload.model_dump(by_alias=False), user_id, commit=commit
        )

    async def update_legal(
        self,
        kind: str,
        payload: PublicLegalDocumentUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformSiteDocumentEnvelopeRead:
        return await self._update_document(
            self.validate_legal_key(kind),
            payload.model_dump(by_alias=False),
            user_id,
            commit=commit,
        )

    async def publish_document(
        self, key: str, user_id: UUID, *, commit: bool = True
    ) -> PlatformSiteDocumentEnvelopeRead:
        doc = await self._get_doc(self.validate_key(key))
        doc.published_content = doc.draft_content
        doc.published_at = datetime.now(UTC)
        doc.published_by_user_id = user_id
        doc.version += 1
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        await self.db.refresh(doc)
        return await self.get_admin_document(key)

    async def _update_document(
        self,
        key: str,
        content: dict,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformSiteDocumentEnvelopeRead:
        doc = await self._get_doc(key)
        doc.draft_content = content
        doc.updated_by_user_id = user_id
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        await self.db.refresh(doc)
        return await self.get_admin_document(key)

    async def _get_doc(self, key: str) -> PublicSiteDocument:
        await self.ensure_defaults()
        result = await self.db.execute(
            select(PublicSiteDocument).where(PublicSiteDocument.key == key)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento público não encontrado.",
            )
        return doc

    @classmethod
    def validate_key(cls, key: str) -> str:
        if key not in cls.VALID_KEYS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recurso público não encontrado.",
            )
        return key

    @classmethod
    def validate_legal_key(cls, kind: str) -> str:
        if kind not in cls.LEGAL_KEYS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento legal não encontrado.",
            )
        return kind

    def _serialize_document_content(self, key: str, content: dict) -> dict:
        if key == "settings":
            return PublicSiteSettingsRead.model_validate(content).model_dump(by_alias=True)
        if key == "home":
            return PublicHomeDocumentRead.model_validate(content).model_dump(by_alias=True)
        if key == "faq":
            return PublicFaqDocumentRead.model_validate(content).model_dump(by_alias=True)
        if key == "media":
            return PublicMediaLibraryRead.model_validate(content).model_dump(by_alias=True)
        if key in self.LEGAL_KEYS:
            return PublicLegalDocumentRead.model_validate(content).model_dump(by_alias=True)
        return content
