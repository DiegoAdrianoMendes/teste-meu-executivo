"""
Sprint S2 — o drafter gera a peça de verdade.

Com provider fake e armazenamento local real (sqlite + filesystem temporário):
- o draft_text vira documento do caso via DocumentService.store_generated_document;
- o run permanece SEMPRE waiting_human com needs_approval=True;
- feedback gera nova versão persistida como novo documento, com a minuta
  anterior entrando no contexto de revisão.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import select

from app.agents.stubs import klm_drafter
from app.ai.base import AICompletionResult
from app.core.config import settings
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.document import Document, DocumentStatus
from app.models.matter import Matter, PracticeArea
from app.models.storage_object import StorageObject, StorageObjectState
from app.services.s3_service import S3Service

DRAFT_V1 = {
    "summary": "Minuta integral de cobrança",
    "draft_title": "Petição Inicial — Ação de Cobrança",
    "draft_text": (
        "# Endereçamento\n\nAo Juízo da Vara Cível competente.\n\n"
        "# Qualificação das partes\n\nCliente Drafter, já qualificado nos autos.\n\n"
        "# Dos Fatos\n\nO contrato foi celebrado e a contraprestação não foi paga.\n\n"
        "# Do Direito\n\nAplica-se o inadimplemento contratual (CC, art. 389).\n\n"
        "# Dos Pedidos\n\na) A condenação da parte ré ao pagamento do principal."
    ),
    "key_arguments": ["Inadimplemento contratual"],
    "required_attachments": ["Contrato assinado"],
    "confidence_score": 80,
    "next_action": "Revisar antes de protocolar.",
}
DRAFT_V2 = {
    **DRAFT_V1,
    "summary": "Minuta revisada com feedback",
    "draft_text": DRAFT_V1["draft_text"]
    + "\n\nb) Subsidiariamente, a condenação em perdas e danos.",
}


class SequenceProvider:
    """Provider fake: devolve resultados na ordem, sem chamada externa real."""

    def __init__(self, results: list[AICompletionResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self._results.pop(0)


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "object-storage")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")


async def _matter_fixture(db_session, auth_context) -> Matter:
    client = Client(workspace_id=auth_context.workspace_id, name="Cliente Drafter")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Cobrança contratual",
        executive_summary="Contrato inadimplido; cliente busca cobrança.",
    )
    db_session.add(matter)
    await db_session.commit()
    return matter


def _bind_drafter_runtime(monkeypatch, db_session, provider) -> None:
    class _SessionFactory:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(klm_drafter, "AsyncSessionLocal", _SessionFactory)
    monkeypatch.setattr(klm_drafter, "get_provider_for_task", lambda _complexity: provider)
    monkeypatch.setattr(
        klm_drafter,
        "WorkspacePolicyService",
        lambda _db: SimpleNamespace(
            resolve_stage_policy=AsyncMock(
                return_value={
                    "context_mode": "full",
                    "use_rag": True,
                    "multimodal_enabled": True,
                }
            )
        ),
    )
    monkeypatch.setattr(
        klm_drafter,
        "KnowledgeService",
        lambda _db: SimpleNamespace(
            build_context_bundle=AsyncMock(return_value={"chunks": []})
        ),
    )


def _read_stored_payload(storage_key: str) -> bytes:
    handle = S3Service().open_local_object(storage_key)
    try:
        return handle.read()
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_draft_text_persists_as_case_document_and_run_waits_human(
    db_session,
    auth_context,
    monkeypatch,
):
    matter = await _matter_fixture(db_session, auth_context)
    provider = SequenceProvider(
        [AICompletionResult(text="not-json", structured_data=DRAFT_V1)]
    )
    _bind_drafter_runtime(monkeypatch, db_session, provider)

    result = await klm_drafter.run_drafter(
        matter.id,
        "thread-drafter-persistencia",
        workspace_id=auth_context.workspace_id,
    )
    await db_session.refresh(matter)

    assert result["status"] == "waiting_human"
    assert matter.needs_approval is True
    assert result["draft_text"] == DRAFT_V1["draft_text"]
    assert result["draft_persisted"] is True
    assert result["draft_persistence_status"] == "persistido"

    document = await db_session.get(Document, UUID(result["draft_document_id"]))
    assert document is not None
    assert document.matter_id == matter.id
    assert document.name.endswith(".md")
    assert document.status is DocumentStatus.completo

    stored = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == document.id)
    )
    assert stored is not None
    assert stored.workspace_id == auth_context.workspace_id
    assert stored.state is StorageObjectState.available
    assert _read_stored_payload(document.s3_key).decode("utf-8") == DRAFT_V1["draft_text"]


@pytest.mark.asyncio
async def test_feedback_generates_new_persisted_version_with_previous_draft_in_context(
    db_session,
    auth_context,
    monkeypatch,
):
    matter = await _matter_fixture(db_session, auth_context)
    provider = SequenceProvider(
        [
            AICompletionResult(text="not-json", structured_data=DRAFT_V1),
            AICompletionResult(text="not-json", structured_data=DRAFT_V2),
        ]
    )
    _bind_drafter_runtime(monkeypatch, db_session, provider)

    first = await klm_drafter.run_drafter(
        matter.id,
        "thread-drafter-v1",
        workspace_id=auth_context.workspace_id,
    )
    assert first["draft_persisted"] is True

    # Simula o que o worker faz: a rodada anterior fica registrada como AgentRun
    # waiting_human com o output completo (inclusive draft_text).
    db_session.add(
        AgentRun(
            workspace_id=auth_context.workspace_id,
            matter_id=matter.id,
            agent_name="klm_drafter",
            trigger_source="test:drafter-v1",
            status=AgentRunStatus.waiting_human,
            output_payload=first,
            attempt_count=1,
        )
    )
    await db_session.commit()

    second = await klm_drafter.run_drafter_with_feedback(
        matter.id,
        "thread-drafter-v2",
        "Incluir pedido subsidiário de perdas e danos.",
        workspace_id=auth_context.workspace_id,
    )
    await db_session.refresh(matter)

    assert second["status"] == "waiting_human"
    assert matter.needs_approval is True
    assert second["draft_persisted"] is True
    assert second["draft_document_id"] != first["draft_document_id"]
    assert second["feedback_received"] == "Incluir pedido subsidiário de perdas e danos."

    # Nova versão persistida como novo documento do caso, com o texto revisado.
    revised = await db_session.get(Document, UUID(second["draft_document_id"]))
    assert revised is not None
    assert revised.matter_id == matter.id
    assert _read_stored_payload(revised.s3_key).decode("utf-8") == DRAFT_V2["draft_text"]

    documents = (
        await db_session.scalars(select(Document).where(Document.matter_id == matter.id))
    ).all()
    assert len(documents) == 2

    # A revisão recebeu a minuta anterior e o feedback no contexto compilado.
    revision_payload = provider.calls[1]["messages"][1].content
    assert "Minuta anterior (base para revisão)" in revision_payload
    assert "Incluir pedido subsidiário" in revision_payload
    assert json.dumps(DRAFT_V1["draft_text"], ensure_ascii=False)[1:-1] in revision_payload


@pytest.mark.asyncio
async def test_fallback_without_draft_text_stays_waiting_human_without_artifact(
    db_session,
    auth_context,
    monkeypatch,
):
    matter = await _matter_fixture(db_session, auth_context)
    provider = SequenceProvider([AICompletionResult(text="resposta invalida")])
    _bind_drafter_runtime(monkeypatch, db_session, provider)

    result = await klm_drafter.run_drafter(
        matter.id,
        "thread-drafter-fallback",
        workspace_id=auth_context.workspace_id,
    )
    await db_session.refresh(matter)

    assert result["status"] == "waiting_human"
    assert matter.needs_approval is True
    assert result["draft_persisted"] is False
    assert result["draft_document_id"] is None
    assert result["draft_persistence_status"] == "sem_texto_integral"

    documents = (
        await db_session.scalars(select(Document).where(Document.matter_id == matter.id))
    ).all()
    assert documents == []
