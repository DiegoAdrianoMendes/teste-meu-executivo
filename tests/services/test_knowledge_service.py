from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import EvidenceRequiredError
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import Matter, PracticeArea
from app.schemas.klm_operations import OmnichannelMessageCreate, OmnichannelThreadCreate
from app.services.klm_operations_service import KlmOperationsService
from app.services.knowledge_service import KnowledgeService


@pytest.mark.asyncio
async def test_indexes_workspace_agent_run_without_inventing_a_matter(
    db_session: AsyncSession,
    auth_context,
):
    run_id = uuid4()

    chunk = await KnowledgeService(db_session).index_agent_run(
        run_id=run_id,
        workspace_id=auth_context.workspace_id,
        matter_id=None,
        agent_name="orquestrador",
        trigger_source="manual_blackbox",
        output_payload={"summary": "Checkpoint de plataforma."},
    )

    assert chunk.workspace_id == auth_context.workspace_id
    assert chunk.matter_id is None
    assert chunk.source_ref == str(run_id)


def _document() -> Document:
    return Document(
        id=uuid4(),
        matter_id=uuid4(),
        name="Negativa do plano.pdf",
        doc_type=DocumentType.comprovante,
        status=DocumentStatus.completo,
        mime_type="application/pdf",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attribute", "value", "expected_text", "expected_source"),
    [
        (
            "extracted_text",
            "A operadora negou formalmente o tratamento prescrito.",
            "A operadora negou formalmente o tratamento prescrito.",
            "model.extracted_text",
        ),
        (
            "document_metadata",
            {"extraction": {"text": "Laudo médico indica urgência de 24 horas."}},
            "Laudo médico indica urgência de 24 horas.",
            "document_metadata.extraction.text",
        ),
    ],
)
async def test_index_document_uses_only_available_extracted_text(
    attribute: str,
    value: object,
    expected_text: str,
    expected_source: str,
):
    document = _document()
    setattr(document, attribute, value)
    service = KnowledgeService(None)  # type: ignore[arg-type]
    indexed = object()
    upsert = AsyncMock(return_value=indexed)
    service.upsert_chunk = upsert

    result = await service.index_document(workspace_id=uuid4(), document=document)

    assert result is indexed
    payload = upsert.await_args.kwargs
    assert payload["content_text"] == expected_text
    assert payload["summary"] == expected_text
    assert payload["metadata"]["hasExtractedText"] is True
    assert payload["metadata"]["textSource"] == expected_source


@pytest.mark.asyncio
async def test_index_document_does_not_claim_extraction_when_no_text_exists():
    document = _document()
    service = KnowledgeService(None)  # type: ignore[arg-type]
    upsert = AsyncMock(return_value=object())
    service.upsert_chunk = upsert

    await service.index_document(workspace_id=uuid4(), document=document)

    payload = upsert.await_args.kwargs
    assert payload["content_text"].startswith("Documento 'Negativa do plano.pdf'")
    assert payload["summary"] == "Negativa do plano.pdf disponível no card."
    assert "hasExtractedText" not in payload["metadata"]
    assert "textSource" not in payload["metadata"]


def _knowledge_chunk(
    *,
    workspace_id,
    matter_id,
    source_type: str,
    source_ref: str,
    content_text: str,
    rank_hint: int,
    updated_at: datetime,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        workspace_id=workspace_id,
        matter_id=matter_id,
        source_type=source_type,
        source_ref=source_ref,
        title=source_ref,
        content_text=content_text,
        summary=content_text,
        content_hash=sha256(source_ref.encode()).hexdigest(),
        rank_hint=rank_hint,
        tags=[],
        chunk_metadata={},
        created_at=updated_at,
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_retrieval_ranks_all_eligible_chunks_and_never_refeeds_agent_runs(
    db_session: AsyncSession,
):
    workspace_id = uuid4()
    matter_id = uuid4()
    now = datetime.now(UTC)
    recent_derived_chunks = [
        _knowledge_chunk(
            workspace_id=workspace_id,
            matter_id=matter_id,
            source_type="conversation",
            source_ref=f"conversation-{index}",
            content_text="Resumo interno derivado sem valor probatório primário.",
            rank_hint=100,
            updated_at=now - timedelta(minutes=index),
        )
        for index in range(20)
    ]
    old_primary_document = _knowledge_chunk(
        workspace_id=workspace_id,
        matter_id=matter_id,
        source_type="document",
        source_ref="old-primary-document",
        content_text="Negativa hospitalar formal assinada pela operadora.",
        rank_hint=1,
        updated_at=now - timedelta(days=60),
    )
    high_rank_agent_run = _knowledge_chunk(
        workspace_id=workspace_id,
        matter_id=matter_id,
        source_type="agent_run",
        source_ref="agent-run-with-feedback",
        content_text="Negativa hospitalar segundo uma resposta anterior do agente.",
        rank_hint=10_000,
        updated_at=now + timedelta(minutes=1),
    )
    db_session.add_all([*recent_derived_chunks, old_primary_document, high_rank_agent_run])
    await db_session.flush()

    result = await KnowledgeService(db_session).retrieve_relevant_chunks(
        workspace_id=workspace_id,
        matter_id=matter_id,
        query_text="negativa hospitalar",
        context_mode="balanced",
        limit=3,
    )

    assert result[0].id == old_primary_document.id
    assert all(chunk.source_type != "agent_run" for chunk in result)


def test_document_source_variants_are_eligible_but_agent_runs_are_not():
    service = KnowledgeService(None)  # type: ignore[arg-type]

    allowed = service._allowed_sources(context_mode="balanced", multimodal_enabled=False)

    assert {"document", "email_attachment", "local_document"} <= allowed
    assert "agent_run" not in allowed
    assert "whatsapp_media" not in allowed


@pytest.mark.asyncio
async def test_canonical_conversation_is_persisted_in_rag_without_exposing_internal_notes(
    db_session: AsyncSession,
    auth_context,
):
    client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente RAG",
        phone="+5511999999999",
    )
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Negativa de medicamento",
    )
    db_session.add(matter)
    await db_session.flush()
    operations = KlmOperationsService(db_session)
    thread = await operations.create_thread(
        auth_context.workspace_id,
        auth_context.user_id,
        OmnichannelThreadCreate(
            matter_id=matter.id,
            client_id=client.id,
            primary_channel=OmnichannelChannel.instagram,
            subject="Atendimento omnichannel",
        ),
    )

    message, deduplicated = await operations.create_message(
        auth_context.workspace_id,
        thread.id,
        None,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.instagram,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente RAG",
            body="O plano negou olaparibe e o tratamento é urgente.",
            idempotency_key="rag-conversation-public-1",
            message_metadata={
                "accessToken": "must-never-enter-rag",
                "transcript": "A médica pediu início em 24 horas.",
                "attachments": [
                    {
                        "name": "negativa.pdf",
                        "mimeType": "application/pdf",
                        "documentId": "doc-1",
                        "ocrText": "Negativa por diretriz interna.",
                    }
                ],
            },
        ),
    )
    assert deduplicated is False

    public_chunk = await db_session.scalar(
        select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == auth_context.workspace_id,
            KnowledgeChunk.matter_id == matter.id,
            KnowledgeChunk.source_type == "conversation_message",
            KnowledgeChunk.source_ref == str(message.id),
        )
    )
    assert public_chunk is not None
    assert "olaparibe" in public_chunk.content_text
    assert "A médica pediu início em 24 horas" in public_chunk.content_text
    assert "Negativa por diretriz interna" in public_chunk.content_text
    assert "must-never-enter-rag" not in str(public_chunk.chunk_metadata)
    assert public_chunk.chunk_metadata["threadId"] == str(thread.id)

    replay, replayed = await operations.create_message(
        auth_context.workspace_id,
        thread.id,
        None,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.instagram,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente RAG",
            body="O plano negou olaparibe e o tratamento é urgente.",
            idempotency_key="rag-conversation-public-1",
            message_metadata={
                "accessToken": "must-never-enter-rag",
                "transcript": "A médica pediu início em 24 horas.",
                "attachments": [
                    {
                        "name": "negativa.pdf",
                        "mimeType": "application/pdf",
                        "documentId": "doc-1",
                        "ocrText": "Negativa por diretriz interna.",
                    }
                ],
            },
        ),
    )
    assert replayed is True
    assert replay.id == message.id
    assert (
        await db_session.scalar(
            select(func.count(KnowledgeChunk.id)).where(
                KnowledgeChunk.workspace_id == auth_context.workspace_id,
                KnowledgeChunk.matter_id == matter.id,
                KnowledgeChunk.source_type == "conversation_message",
                KnowledgeChunk.source_ref == str(message.id),
            )
        )
        == 1
    )

    internal_message, _ = await operations.create_message(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.internal,
            direction=MessageDirection.internal,
            author_type=MessageAuthorType.human,
            author_label="Advogado",
            audience="internal",
            body="Nota sigilosa: não compartilhar esta estratégia com o lead.",
            idempotency_key="rag-conversation-internal-1",
        ),
    )
    internal_chunk = await db_session.scalar(
        select(KnowledgeChunk).where(
            KnowledgeChunk.source_type == "internal_conversation",
            KnowledgeChunk.source_ref == str(internal_message.id),
        )
    )
    assert internal_chunk is not None

    public_retrieval = await KnowledgeService(db_session).retrieve_relevant_chunks(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        query_text="estratégia sigilosa olaparibe",
        limit=20,
    )
    assert public_chunk.id in {chunk.id for chunk in public_retrieval}
    assert internal_chunk.id not in {chunk.id for chunk in public_retrieval}

    internal_retrieval = await KnowledgeService(db_session).retrieve_relevant_chunks(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        query_text="estratégia sigilosa",
        limit=20,
        include_internal=True,
    )
    assert internal_chunk.id in {chunk.id for chunk in internal_retrieval}


@pytest.mark.asyncio
async def test_hybrid_retrieval_hard_filters_workspace_matter_and_document(
    db_session: AsyncSession,
):
    workspace_a = uuid4()
    workspace_b = uuid4()
    matter_a = uuid4()
    matter_b = uuid4()
    selected_document_id = uuid4()
    excluded_document_id = uuid4()
    now = datetime.now(UTC)
    chunks = [
        _knowledge_chunk(
            workspace_id=workspace_a,
            matter_id=matter_a,
            source_type="document",
            source_ref=str(selected_document_id),
            content_text="Laudo prescreve tratamento oncológico urgente.",
            rank_hint=2,
            updated_at=now,
        ),
        _knowledge_chunk(
            workspace_id=workspace_a,
            matter_id=matter_a,
            source_type="document",
            source_ref=str(excluded_document_id),
            content_text="Outro laudo oncológico não selecionado.",
            rank_hint=200,
            updated_at=now,
        ),
        _knowledge_chunk(
            workspace_id=workspace_b,
            matter_id=matter_a,
            source_type="document",
            source_ref=str(selected_document_id),
            content_text="Documento de outro escritório.",
            rank_hint=200,
            updated_at=now,
        ),
        _knowledge_chunk(
            workspace_id=workspace_a,
            matter_id=matter_b,
            source_type="document",
            source_ref=str(selected_document_id),
            content_text="Documento de outro caso.",
            rank_hint=200,
            updated_at=now,
        ),
    ]
    db_session.add_all(chunks)
    await db_session.flush()

    bundle = await KnowledgeService(db_session).retrieve_hybrid(
        workspace_id=workspace_a,
        matter_id=matter_a,
        query_text="tratamento oncologico urgente",
        document_ids=[selected_document_id],
        require_evidence=True,
    )

    assert [chunk.source_ref for chunk in bundle.chunks] == [str(selected_document_id)]
    assert bundle.receipt.workspace_id == str(workspace_a)
    assert bundle.receipt.matter_id == str(matter_a)
    assert bundle.receipt.document_filter == (str(selected_document_id),)
    assert bundle.receipt.evidence_satisfied is True
    assert bundle.receipt.selected_sources[0]["content_sha256"]


@pytest.mark.asyncio
async def test_hybrid_retrieval_fails_closed_when_required_document_is_missing(
    db_session: AsyncSession,
):
    workspace_id = uuid4()
    matter_id = uuid4()
    db_session.add(
        _knowledge_chunk(
            workspace_id=workspace_id,
            matter_id=matter_id,
            source_type="conversation",
            source_ref="conversation-only",
            content_text="Cliente relata urgência, sem documento anexado.",
            rank_hint=10,
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    with pytest.raises(EvidenceRequiredError, match="No scoped document evidence"):
        await KnowledgeService(db_session).retrieve_hybrid(
            workspace_id=workspace_id,
            matter_id=matter_id,
            query_text="urgencia",
            document_ids=[uuid4()],
            require_evidence=True,
        )


def test_hash_embedding_is_deterministic_accent_insensitive_and_versioned():
    service = KnowledgeService(None)  # type: ignore[arg-type]

    accented = service._hashed_embedding("medicação oncológica urgente")
    plain = service._hashed_embedding("medicacao oncologica urgente")

    assert accented == plain
    assert len(accented) == 384
    assert sum(value * value for value in accented) == pytest.approx(1.0, abs=1e-6)
