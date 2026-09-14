"""
Service: KnowledgeService
Indexação leve e retrieval seletivo de contexto por card/task.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Select, bindparam, desc, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import EvidenceRequiredError
from app.models.document import Document
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import Matter

HASH_EMBEDDING_DIMENSIONS = 384
HASH_EMBEDDING_VERSION = "klm-hash-embedding-v1"
HYBRID_RETRIEVAL_VERSION = "klm-hybrid-rag-v1"
MAX_RETRIEVAL_CANDIDATES = 512
_TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")
_POSTGRES_VECTOR_CAPABILITY: dict[int, bool] = {}


@dataclass
class RetrievedChunk:
    chunk: KnowledgeChunk
    score: float
    source_priority: int = 0
    lexical_score: float = 0.0
    vector_score: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalReceipt:
    version: str
    workspace_id: str
    matter_id: str
    document_filter: tuple[str, ...] | None
    query_sha256: str
    candidate_count: int
    selected_count: int
    evidence_required: bool
    evidence_satisfied: bool
    selected_sources: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class RetrievalBundle:
    items: tuple[RetrievedChunk, ...]
    receipt: RetrievalReceipt

    @property
    def chunks(self) -> list[KnowledgeChunk]:
        return [item.chunk for item in self.items]


DOCUMENT_EVIDENCE_SOURCES = frozenset({"document", "email_attachment", "local_document"})
PRIMARY_EVIDENCE_SOURCES = DOCUMENT_EVIDENCE_SOURCES | frozenset(
    {"whatsapp_message", "whatsapp_media"}
)
PUBLIC_CONVERSATION_SOURCES = frozenset({"conversation", "conversation_message"})
DOCUMENT_TEXT_ATTRIBUTES = (
    "extracted_text",
    "extracted_content",
    "content_text",
    "text_content",
    "ocr_text",
)
DOCUMENT_METADATA_ATTRIBUTES = (
    "document_metadata",
    "file_metadata",
    "extraction_metadata",
    "source_metadata",
    "metadata",
)
DOCUMENT_METADATA_TEXT_KEYS = (
    "extractedText",
    "extracted_text",
    "extractedContent",
    "extracted_content",
    "contentText",
    "content_text",
    "textContent",
    "text_content",
    "ocrText",
    "ocr_text",
)


class KnowledgeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_chunk(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID | None,
        source_type: str,
        source_ref: str | None,
        content_text: str,
        title: str | None = None,
        summary: str | None = None,
        task_id: UUID | None = None,
        tags: Iterable[str] | None = None,
        metadata: dict[str, Any] | None = None,
        rank_hint: int = 0,
        created_by_user_id: UUID | None = None,
    ) -> KnowledgeChunk:
        normalized_tags = [tag for tag in (tags or []) if tag]
        normalized_metadata = dict(metadata or {})
        content_hash = self._hash_content(content_text, summary, normalized_metadata)
        embedding_data = self._hashed_embedding(
            " ".join(
                part for part in (title, summary, content_text, " ".join(normalized_tags)) if part
            )
        )
        normalized_metadata["embeddingVersion"] = HASH_EMBEDDING_VERSION

        existing_query: Select[tuple[KnowledgeChunk]] = select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.matter_id == matter_id,
            KnowledgeChunk.source_type == source_type,
        )
        if source_ref:
            existing_query = existing_query.where(KnowledgeChunk.source_ref == source_ref)
        else:
            existing_query = existing_query.where(KnowledgeChunk.content_hash == content_hash)

        def apply_values(chunk: KnowledgeChunk) -> None:
            chunk.task_id = task_id
            chunk.created_by_user_id = created_by_user_id or chunk.created_by_user_id
            chunk.title = title
            chunk.content_text = content_text
            chunk.summary = summary
            chunk.content_hash = content_hash
            chunk.rank_hint = rank_hint
            chunk.tags = normalized_tags
            chunk.chunk_metadata = normalized_metadata
            chunk.embedding_data = embedding_data

        result = await self.db.execute(existing_query.limit(1))
        chunk = result.scalar_one_or_none()
        if chunk is None:
            candidate = KnowledgeChunk(
                workspace_id=workspace_id,
                matter_id=matter_id,
                task_id=task_id,
                created_by_user_id=created_by_user_id,
                source_type=source_type,
                source_ref=source_ref,
                title=title,
                content_text=content_text,
                summary=summary,
                content_hash=content_hash,
                rank_hint=rank_hint,
                tags=normalized_tags,
                chunk_metadata=normalized_metadata,
                embedding_data=embedding_data,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(candidate)
                    await self.db.flush()
            except IntegrityError:
                result = await self.db.execute(existing_query.limit(1))
                chunk = result.scalar_one_or_none()
                if chunk is None:
                    raise
                apply_values(chunk)
                await self.db.flush()
            else:
                chunk = candidate
        else:
            apply_values(chunk)
            await self.db.flush()
        return chunk

    async def index_document(
        self,
        *,
        workspace_id: UUID,
        document: Document,
        title: str | None = None,
        source_type: str = "document",
        rank_hint: int = 3,
    ) -> KnowledgeChunk:
        doc_summary = (
            f"Documento '{document.name}' do tipo '{document.doc_type.value}' "
            f"com status '{document.status.value}'."
        )
        if document.mime_type:
            doc_summary += f" MIME type: {document.mime_type}."
        if document.s3_key:
            doc_summary += f" Arquivo armazenado em '{document.s3_key}'."

        metadata = {
            "documentId": str(document.id),
            "mimeType": document.mime_type,
            "status": document.status.value,
            "docType": document.doc_type.value,
            "s3Key": document.s3_key,
        }
        extracted_text, text_source = self._document_text(document)
        content_text = extracted_text or doc_summary
        summary = f"{document.name} disponível no card."
        if extracted_text:
            summary = extracted_text[:240] + ("..." if len(extracted_text) > 240 else "")
            metadata.update(
                {
                    "hasExtractedText": True,
                    "textSource": text_source,
                }
            )
        return await self.upsert_chunk(
            workspace_id=workspace_id,
            matter_id=document.matter_id,
            source_type=source_type,
            source_ref=str(document.id),
            title=title or document.name,
            content_text=content_text,
            summary=summary,
            tags=["documento", document.doc_type.value, document.status.value],
            metadata=metadata,
            rank_hint=rank_hint,
            created_by_user_id=document.created_by,
        )

    async def index_whatsapp_message(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        source_ref: str,
        body: str,
        phone: str,
        contact_name: str | None = None,
        media_type: str | None = None,
        media_url: str | None = None,
        task_id: UUID | None = None,
    ) -> KnowledgeChunk:
        speaker = contact_name or phone
        content = f"Mensagem recebida de {speaker}: {body or '[sem texto]'}"
        if media_type:
            content += f" Arquivo multimodal associado do tipo '{media_type}'."
        metadata = {
            "phone": phone,
            "contactName": contact_name,
            "mediaType": media_type,
            "mediaUrl": media_url,
        }
        tags = ["whatsapp", "cliente"]
        if media_type:
            tags.extend(["multimodal", media_type])
        return await self.upsert_chunk(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task_id,
            source_type="whatsapp_message",
            source_ref=source_ref,
            title=f"WhatsApp de {speaker}",
            content_text=content,
            summary=(body or f"Mensagem {media_type or 'recebida'}")[:240],
            tags=tags,
            metadata=metadata,
            rank_hint=4 if media_type else 2,
        )

    async def index_omnichannel_message(
        self,
        *,
        message: OmnichannelMessage,
        matter_id: UUID | None = None,
    ) -> KnowledgeChunk:
        """Persist one canonical message as tenant-scoped, traceable RAG context."""

        scoped_matter_id = matter_id or message.matter_id
        if scoped_matter_id is None:
            raise ValueError("A mensagem precisa estar vinculada a um caso para entrar no RAG.")

        internal = (
            message.direction is MessageDirection.internal
            or message.channel is OmnichannelChannel.internal
            or message.audience == "internal"
        )
        source_type = "internal_conversation" if internal else "conversation_message"
        direction_label = {
            MessageDirection.inbound: "recebida",
            MessageDirection.outbound: "enviada",
            MessageDirection.internal: "interna",
        }[message.direction]
        body = str(message.body or "").strip() or "[mensagem sem texto]"
        supplemental, attachment_refs = self._conversation_supplemental_text(
            message.message_metadata or {}
        )
        content_parts = [
            (
                f"Mensagem {direction_label} em {message.channel.value} por "
                f"{message.author_label}: {body}"
            )
        ]
        if supplemental:
            content_parts.append(supplemental)
        content_text = " ".join(content_parts)[:12_000]
        tags = [
            "conversa",
            message.channel.value,
            message.direction.value,
            message.author_type.value,
            message.content_type,
        ]
        if internal:
            tags.append("interno_restrito")
        if attachment_refs:
            tags.append("anexo")

        rank_hint = 0 if internal else 4 if message.direction is MessageDirection.inbound else 2
        return await self.upsert_chunk(
            workspace_id=message.workspace_id,
            matter_id=scoped_matter_id,
            source_type=source_type,
            source_ref=str(message.id),
            title=(
                "Nota interna da conversa"
                if internal
                else f"{message.channel.value.title()} · {message.author_label}"
            ),
            content_text=content_text,
            summary=body[:240] + ("..." if len(body) > 240 else ""),
            tags=tags,
            metadata={
                "threadId": str(message.thread_id),
                "messageId": str(message.id),
                "channel": message.channel.value,
                "direction": message.direction.value,
                "authorType": message.author_type.value,
                "audience": message.audience,
                "contentType": message.content_type,
                "language": message.language,
                "occurredAt": message.occurred_at.isoformat(),
                "deliveryStatus": message.delivery_status.value,
                "attachmentRefs": attachment_refs,
                "isInternal": internal,
                "provenanceVersion": "klm-canonical-conversation-v1",
            },
            rank_hint=rank_hint,
            created_by_user_id=(
                message.author_user_id
                if message.author_type in {MessageAuthorType.human, MessageAuthorType.agent}
                else None
            ),
        )

    async def index_thread_conversation(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        matter_id: UUID,
    ) -> list[KnowledgeChunk]:
        """Idempotently backfill the complete canonical thread into the case RAG."""

        messages = list(
            (
                await self.db.scalars(
                    select(OmnichannelMessage)
                    .where(
                        OmnichannelMessage.workspace_id == workspace_id,
                        OmnichannelMessage.thread_id == thread_id,
                    )
                    .order_by(OmnichannelMessage.occurred_at, OmnichannelMessage.id)
                )
            ).all()
        )
        chunks: list[KnowledgeChunk] = []
        for message in messages:
            if message.matter_id is None:
                message.matter_id = matter_id
            elif message.matter_id != matter_id:
                raise ValueError("A conversa contém mensagem vinculada a outro caso.")
            chunks.append(
                await self.index_omnichannel_message(message=message, matter_id=matter_id)
            )
        return chunks

    async def index_agent_run(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        matter_id: UUID | None,
        agent_name: str,
        trigger_source: str,
        output_payload: dict[str, Any] | None = None,
        task_id: UUID | None = None,
    ) -> KnowledgeChunk:
        summary = self._extract_output_summary(output_payload)
        content_lines = [
            f"Saída do agente {agent_name} acionado por {trigger_source}.",
        ]
        if summary:
            content_lines.append(summary)
        if output_payload:
            serialized = json.dumps(output_payload, ensure_ascii=False, default=str)
            if len(serialized) > 1800:
                serialized = serialized[:1800] + "..."
            content_lines.append(f"Payload resumido: {serialized}")
        return await self.upsert_chunk(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task_id,
            source_type="agent_run",
            source_ref=str(run_id),
            title=f"Execução {agent_name}",
            content_text=" ".join(content_lines),
            summary=summary or f"Execução {agent_name} registrada no card.",
            tags=["agent_run", agent_name, trigger_source],
            metadata={"agentName": agent_name, "triggerSource": trigger_source},
            rank_hint=5,
        )

    async def index_internal_conversation(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        conversation_id: UUID,
        message: str,
        task_id: UUID | None = None,
    ) -> KnowledgeChunk:
        return await self.upsert_chunk(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task_id,
            source_type="internal_conversation",
            source_ref=str(conversation_id),
            title="Mensagem interna do card",
            content_text=message,
            summary=message[:220],
            tags=["conversa", "interna"],
            metadata={"isInternal": True},
            rank_hint=2,
        )

    async def index_human_override(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        override_id: UUID,
        agent_name: str,
        decision: str,
        notes: str | None = None,
        corrected_payload: dict[str, Any] | None = None,
        created_by_user_id: UUID | None = None,
    ) -> KnowledgeChunk:
        content = f"Override humano sobre {agent_name}. Decisão: {decision}."
        if notes:
            content += f" Observação: {notes}"
        if corrected_payload:
            serialized = json.dumps(corrected_payload, ensure_ascii=False, default=str)
            if len(serialized) > 1200:
                serialized = serialized[:1200] + "..."
            content += f" Payload corrigido: {serialized}"
        return await self.upsert_chunk(
            workspace_id=workspace_id,
            matter_id=matter_id,
            source_type="human_override",
            source_ref=str(override_id),
            title=f"Override humano em {agent_name}",
            content_text=content,
            summary=notes or f"Decisão humana: {decision}",
            tags=["override", "humano", agent_name, decision],
            metadata={"decision": decision, "agentName": agent_name},
            rank_hint=6,
            created_by_user_id=created_by_user_id,
        )

    async def retrieve_relevant_chunks(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        query_text: str | None,
        context_mode: str = "balanced",
        task_id: UUID | None = None,
        use_rag: bool = True,
        multimodal_enabled: bool = True,
        limit: int | None = None,
        document_ids: Iterable[UUID | str] | None = None,
        require_evidence: bool = False,
        include_internal: bool = False,
    ) -> list[KnowledgeChunk]:
        bundle = await self.retrieve_hybrid(
            workspace_id=workspace_id,
            matter_id=matter_id,
            query_text=query_text,
            context_mode=context_mode,
            task_id=task_id,
            use_rag=use_rag,
            multimodal_enabled=multimodal_enabled,
            limit=limit,
            document_ids=document_ids,
            require_evidence=require_evidence,
            include_internal=include_internal,
        )
        return bundle.chunks

    async def retrieve_hybrid(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        query_text: str | None,
        context_mode: str = "balanced",
        task_id: UUID | None = None,
        use_rag: bool = True,
        multimodal_enabled: bool = True,
        limit: int | None = None,
        document_ids: Iterable[UUID | str] | None = None,
        require_evidence: bool = False,
        include_internal: bool = False,
    ) -> RetrievalBundle:
        normalized_documents = (
            tuple(sorted({str(value).strip() for value in document_ids if str(value).strip()}))
            if document_ids is not None
            else None
        )
        query = str(query_text or "").strip()
        query_digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        if not use_rag:
            if require_evidence:
                raise EvidenceRequiredError("RAG is disabled while evidence is required.")
            return RetrievalBundle(
                items=(),
                receipt=RetrievalReceipt(
                    version=HYBRID_RETRIEVAL_VERSION,
                    workspace_id=str(workspace_id),
                    matter_id=str(matter_id),
                    document_filter=normalized_documents,
                    query_sha256=query_digest,
                    candidate_count=0,
                    selected_count=0,
                    evidence_required=False,
                    evidence_satisfied=False,
                    selected_sources=(),
                ),
            )

        allowed_sources = self._allowed_sources(
            context_mode=context_mode,
            multimodal_enabled=multimodal_enabled,
            include_internal=include_internal,
        )
        base_query = (
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.workspace_id == workspace_id,
                KnowledgeChunk.matter_id == matter_id,
                KnowledgeChunk.source_type.in_(sorted(allowed_sources)),
            )
            .order_by(desc(KnowledgeChunk.updated_at))
        )

        if task_id:
            base_query = base_query.where(
                or_(KnowledgeChunk.task_id.is_(None), KnowledgeChunk.task_id == task_id)
            )

        if normalized_documents is not None:
            base_query = base_query.where(
                or_(
                    KnowledgeChunk.source_type.not_in(sorted(DOCUMENT_EVIDENCE_SOURCES)),
                    KnowledgeChunk.source_ref.in_(normalized_documents),
                )
            )

        final_limit = (
            limit
            if limit is not None
            else (8 if context_mode == "full" else 6 if context_mode == "balanced" else 4)
        )
        if final_limit <= 0:
            if require_evidence:
                raise EvidenceRequiredError("A positive retrieval limit is required for evidence.")
            final_limit = 0
        candidate_limit = min(
            MAX_RETRIEVAL_CANDIDATES,
            max(64, final_limit * 16),
        )
        query_embedding = self._hashed_embedding(query) if query else []
        postgres_scores: dict[str, tuple[float, float]] = {}
        if query and self._dialect_name() == "postgresql":
            chunks, postgres_scores = await self._postgres_hybrid_candidates(
                workspace_id=workspace_id,
                matter_id=matter_id,
                query_text=query,
                query_embedding=query_embedding,
                allowed_sources=allowed_sources,
                document_ids=normalized_documents,
                task_id=task_id,
                candidate_limit=candidate_limit,
            )
        else:
            result = await self.db.execute(base_query.limit(candidate_limit))
            chunks = list(result.scalars().all())

        retrieved: list[RetrievedChunk] = []
        for chunk in chunks:
            database_scores = postgres_scores.get(str(chunk.id))
            lexical_score = (
                database_scores[0]
                if database_scores is not None
                else self._lexical_score(chunk, query_text=query)
            )
            vector_score = (
                database_scores[1]
                if database_scores is not None
                else self._vector_similarity(
                    self._chunk_embedding(chunk),
                    query_embedding,
                )
            )
            source_priority = self._source_priority(chunk.source_type)
            score = self._score_chunk(
                chunk,
                query_text=query,
                task_id=task_id,
                lexical_score=lexical_score,
                vector_score=vector_score,
                source_priority=source_priority,
            )
            if score <= 0:
                continue
            retrieved.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=score,
                    source_priority=source_priority,
                    lexical_score=lexical_score,
                    vector_score=vector_score,
                    provenance=self._chunk_provenance(chunk),
                )
            )

        retrieved.sort(
            key=lambda item: (item.source_priority, item.score),
            reverse=True,
        )
        selected = tuple(retrieved[:final_limit])
        evidence_satisfied = any(
            item.chunk.source_type in DOCUMENT_EVIDENCE_SOURCES
            and (
                normalized_documents is None
                or str(item.chunk.source_ref or "") in normalized_documents
            )
            for item in selected
        )
        if require_evidence and not evidence_satisfied:
            raise EvidenceRequiredError(
                "No scoped document evidence matched the requested workspace, matter, and filter."
            )
        receipt = RetrievalReceipt(
            version=HYBRID_RETRIEVAL_VERSION,
            workspace_id=str(workspace_id),
            matter_id=str(matter_id),
            document_filter=normalized_documents,
            query_sha256=query_digest,
            candidate_count=len(chunks),
            selected_count=len(selected),
            evidence_required=require_evidence,
            evidence_satisfied=evidence_satisfied,
            selected_sources=tuple(
                {
                    "content_sha256": str(item.chunk.content_hash),
                    "source_id": str(item.chunk.id),
                    "source_ref": str(item.chunk.source_ref or ""),
                    "source_type": item.chunk.source_type,
                }
                for item in selected
            ),
        )
        return RetrievalBundle(items=selected, receipt=receipt)

    async def _postgres_hybrid_candidates(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        query_text: str,
        query_embedding: list[float],
        allowed_sources: set[str],
        document_ids: tuple[str, ...] | None,
        task_id: UUID | None,
        candidate_limit: int,
    ) -> tuple[list[KnowledgeChunk], dict[str, tuple[float, float]]]:
        clauses = [
            "workspace_id = CAST(:workspace_id AS uuid)",
            "matter_id = CAST(:matter_id AS uuid)",
            "source_type IN :allowed_sources",
        ]
        bind_parameters = [bindparam("allowed_sources", expanding=True)]
        vector_enabled = await self._postgres_vector_enabled()
        parameters: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "matter_id": str(matter_id),
            "query_text": query_text,
            "allowed_sources": sorted(allowed_sources),
            "candidate_limit": candidate_limit,
        }
        if vector_enabled:
            parameters["query_vector"] = json.dumps(
                query_embedding,
                separators=(",", ":"),
            )
        if task_id is not None:
            clauses.append("(task_id IS NULL OR task_id = CAST(:task_id AS uuid))")
            parameters["task_id"] = str(task_id)
        if document_ids is not None:
            bind_parameters.append(bindparam("document_sources", expanding=True))
            parameters["document_sources"] = sorted(DOCUMENT_EVIDENCE_SOURCES)
            if document_ids:
                clauses.append(
                    "(source_type NOT IN :document_sources OR source_ref IN :document_ids)"
                )
                bind_parameters.append(bindparam("document_ids", expanding=True))
                parameters["document_ids"] = list(document_ids)
            else:
                clauses.append("source_type NOT IN :document_sources")

        vector_score_sql = (
            "CASE WHEN embedding_vector IS NULL THEN 0.0 "
            "ELSE 1.0 - (embedding_vector <=> CAST(:query_vector AS vector)) END"
            if vector_enabled
            else "0.0"
        )
        statement = text(
            """
            SELECT
                id,
                ts_rank_cd(
                    search_document,
                    websearch_to_tsquery('portuguese', :query_text)
                ) AS lexical_score,
                """
            + vector_score_sql
            + """ AS vector_score
            FROM knowledge_chunks
            WHERE """
            + " AND ".join(clauses)
            + """
            ORDER BY (
                ts_rank_cd(
                    search_document,
                    websearch_to_tsquery('portuguese', :query_text)
                ) * 0.65
                + ("""
            + vector_score_sql
            + """) * 0.35
                + LEAST(GREATEST(rank_hint, -20), 20) * 0.001
            ) DESC, updated_at DESC
            LIMIT :candidate_limit
            """
        ).bindparams(*bind_parameters)
        rows = (await self.db.execute(statement, parameters)).mappings().all()
        if not rows:
            return [], {}
        ids = [row["id"] for row in rows]
        loaded = list(
            (await self.db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))).all()
        )
        by_id = {str(chunk.id): chunk for chunk in loaded}
        ordered = [by_id[str(row["id"])] for row in rows if str(row["id"]) in by_id]
        scores = {
            str(row["id"]): (
                float(row["lexical_score"] or 0.0),
                float(row["vector_score"] or 0.0),
            )
            for row in rows
        }
        return ordered, scores

    async def _postgres_vector_enabled(self) -> bool:
        bind_key = id(self.db.get_bind())
        cached = _POSTGRES_VECTOR_CAPABILITY.get(bind_key)
        if cached is not None:
            return cached
        enabled = bool(
            await self.db.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'knowledge_chunks' "
                    "AND column_name = 'embedding_vector'"
                    ")"
                )
            )
        )
        _POSTGRES_VECTOR_CAPABILITY[bind_key] = enabled
        return enabled

    def _dialect_name(self) -> str:
        bind = self.db.get_bind()
        return str(bind.dialect.name)

    async def build_context_bundle(
        self,
        *,
        matter: Matter,
        query_text: str | None,
        context_mode: str,
        use_rag: bool,
        multimodal_enabled: bool,
        task_id: UUID | None = None,
        document_ids: Iterable[UUID | str] | None = None,
        require_evidence: bool = False,
        include_internal: bool = False,
    ) -> dict[str, Any]:
        retrieval = await self.retrieve_hybrid(
            workspace_id=matter.workspace_id,
            matter_id=matter.id,
            query_text=query_text,
            context_mode=context_mode,
            task_id=task_id,
            use_rag=use_rag,
            multimodal_enabled=multimodal_enabled,
            document_ids=document_ids,
            require_evidence=require_evidence,
            include_internal=include_internal,
        )
        chunks = retrieval.chunks
        return {
            "chunks": chunks,
            "rendered_context": self.render_context(chunks),
            "retrieval_receipt": retrieval.receipt,
        }

    def render_context(self, chunks: list[KnowledgeChunk]) -> str:
        if not chunks:
            return ""
        lines = []
        for chunk in chunks:
            title = chunk.title or chunk.source_type
            summary = chunk.summary or chunk.content_text
            if len(summary) > 240:
                summary = summary[:240] + "..."
            lines.append(f"- [{chunk.source_type}] {title}: {summary}")
        return "\n".join(lines)

    def _score_chunk(
        self,
        chunk: KnowledgeChunk,
        *,
        query_text: str | None,
        task_id: UUID | None,
        lexical_score: float = 0.0,
        vector_score: float = 0.0,
        source_priority: int = 0,
    ) -> float:
        if chunk.source_type == "agent_run":
            return -1.0

        score = float(min(max(chunk.rank_hint or 0, -20), 20))
        score += float(source_priority * 100)
        score += lexical_score * 20.0
        score += vector_score * 12.0

        if task_id and chunk.task_id == task_id:
            score += 4.0

        if "multimodal" in (chunk.tags or []):
            score += 0.5

        return score

    def _lexical_score(
        self,
        chunk: KnowledgeChunk,
        *,
        query_text: str | None,
    ) -> float:
        tokens = self._extract_tokens(query_text)
        if not tokens:
            return 0.0
        searchable = self._normalize_search_text(
            " ".join(
                [
                    chunk.title or "",
                    chunk.summary or "",
                    chunk.content_text or "",
                    " ".join(chunk.tags or []),
                ]
            )
        )
        matches = sum(1 for token in tokens if token in searchable)
        phrase_bonus = 0.5 if self._normalize_search_text(query_text or "") in searchable else 0.0
        return min(1.5, matches / max(1, len(tokens)) + phrase_bonus)

    def _chunk_embedding(self, chunk: KnowledgeChunk) -> list[float]:
        raw = getattr(chunk, "embedding_data", None)
        if (
            isinstance(raw, list)
            and len(raw) == HASH_EMBEDDING_DIMENSIONS
            and all(isinstance(value, (int, float)) for value in raw)
        ):
            return [float(value) for value in raw]
        return self._hashed_embedding(
            " ".join(
                part
                for part in (
                    chunk.title,
                    chunk.summary,
                    chunk.content_text,
                    " ".join(chunk.tags or []),
                )
                if part
            )
        )

    def _hashed_embedding(self, text: str) -> list[float]:
        vector = [0.0] * HASH_EMBEDDING_DIMENSIONS
        tokens = self._extract_tokens(text)
        if not tokens:
            return vector
        features = list(tokens)
        features.extend(f"{left}_{right}" for left, right in zip(tokens, tokens[1:], strict=False))
        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % HASH_EMBEDDING_DIMENSIONS
            sign = -1.0 if digest[2] & 1 else 1.0
            weight = 0.65 if "_" in feature else 1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 8) for value in vector]

    def _vector_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        similarity = sum(a * b for a, b in zip(left, right, strict=True))
        return max(-1.0, min(1.0, similarity))

    def _chunk_provenance(self, chunk: KnowledgeChunk) -> dict[str, Any]:
        return {
            "content_sha256": chunk.content_hash,
            "matter_id": str(chunk.matter_id),
            "source_id": str(chunk.id),
            "source_ref": str(chunk.source_ref or ""),
            "source_type": chunk.source_type,
            "workspace_id": str(chunk.workspace_id),
        }

    def _allowed_sources(
        self,
        *,
        context_mode: str,
        multimodal_enabled: bool,
        include_internal: bool = False,
    ) -> set[str]:
        if context_mode == "essential":
            allowed = set(DOCUMENT_EVIDENCE_SOURCES) | {
                *PUBLIC_CONVERSATION_SOURCES,
                "human_override",
                "conversation_insight",
            }
        elif context_mode == "full":
            allowed = set(DOCUMENT_EVIDENCE_SOURCES) | {
                *PUBLIC_CONVERSATION_SOURCES,
                "whatsapp_message",
                "human_override",
                "conversation_insight",
            }
        else:
            allowed = set(DOCUMENT_EVIDENCE_SOURCES) | {
                *PUBLIC_CONVERSATION_SOURCES,
                "whatsapp_message",
                "human_override",
                "conversation_insight",
            }

        if multimodal_enabled:
            allowed.add("whatsapp_media")
        if include_internal:
            allowed.add("internal_conversation")
        return allowed

    def _source_priority(self, source_type: str) -> int:
        if source_type in PRIMARY_EVIDENCE_SOURCES:
            return 2
        if source_type in {"conversation_message", "human_override"}:
            return 1
        return 0

    @staticmethod
    def _conversation_supplemental_text(
        metadata: Mapping[str, Any],
    ) -> tuple[str, list[dict[str, str]]]:
        """Extract useful media text without copying opaque provider payloads or secrets."""

        supplemental: list[str] = []
        attachment_refs: list[dict[str, str]] = []

        for key in ("transcript", "transcription", "caption", "ocrText", "ocr_text"):
            value = metadata.get(key)
            if isinstance(value, Mapping):
                value = value.get("text") or value.get("content")
            if isinstance(value, str) and value.strip():
                supplemental.append(f"{key}: {value.strip()[:4_000]}")

        raw_attachments = metadata.get("attachments") or metadata.get("media") or []
        if isinstance(raw_attachments, Mapping):
            raw_attachments = [raw_attachments]
        if isinstance(raw_attachments, list):
            for index, item in enumerate(raw_attachments[:20]):
                if not isinstance(item, Mapping):
                    continue
                name = str(
                    item.get("name")
                    or item.get("filename")
                    or item.get("fileName")
                    or f"anexo-{index + 1}"
                )[:255]
                kind = str(
                    item.get("mimeType") or item.get("mime_type") or item.get("type") or "arquivo"
                )[:120]
                reference = str(
                    item.get("storageObjectId") or item.get("documentId") or item.get("id") or ""
                )[:180]
                attachment_refs.append({"name": name, "type": kind, "reference": reference})
                supplemental.append(f"Anexo: {name} ({kind}).")
                for text_key in ("transcript", "caption", "ocrText", "ocr_text", "text"):
                    text_value = item.get(text_key)
                    if isinstance(text_value, Mapping):
                        text_value = text_value.get("text") or text_value.get("content")
                    if isinstance(text_value, str) and text_value.strip():
                        supplemental.append(
                            f"Conteúdo extraído de {name}: {text_value.strip()[:4_000]}"
                        )

        return " ".join(supplemental)[:8_000], attachment_refs

    def _document_text(self, document: Document) -> tuple[str | None, str | None]:
        for attribute in DOCUMENT_TEXT_ATTRIBUTES:
            value = getattr(document, attribute, None)
            if isinstance(value, str) and value.strip():
                return value.strip(), f"model.{attribute}"

        for attribute in DOCUMENT_METADATA_ATTRIBUTES:
            metadata = getattr(document, attribute, None)
            if not isinstance(metadata, Mapping):
                continue
            value, key = self._document_text_from_metadata(metadata)
            if value:
                return value, f"{attribute}.{key}"
        return None, None

    def _document_text_from_metadata(
        self,
        metadata: Mapping[str, Any],
    ) -> tuple[str | None, str | None]:
        for key in DOCUMENT_METADATA_TEXT_KEYS:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), key

        for section_name in ("extraction", "ocr"):
            section = metadata.get(section_name)
            if not isinstance(section, Mapping):
                continue
            for key in (*DOCUMENT_METADATA_TEXT_KEYS, "text"):
                value = section.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), f"{section_name}.{key}"
        return None, None

    def _extract_output_summary(self, output_payload: dict[str, Any] | None) -> str | None:
        if not output_payload:
            return None
        for key in ("summary", "message", "conversation_summary", "viability_notes", "next_action"):
            value = output_payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _hash_content(
        self,
        content_text: str,
        summary: str | None,
        metadata: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            {
                "content_text": content_text,
                "summary": summary,
                "metadata": metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _extract_tokens(self, query_text: str | None) -> list[str]:
        if not query_text:
            return []
        return _TOKEN_PATTERN.findall(self._normalize_search_text(query_text))

    def _normalize_search_text(self, value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.lower())
        return "".join(
            character for character in decomposed if not unicodedata.combining(character)
        )
