from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.playbook import (
    PlaybookLifecycleState,
    PlaybookScopeDimension,
    PlaybookSimulationRun,
)
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.playbook import (
    PlaybookContent,
    PlaybookCreate,
    PlaybookDocumentPayload,
    PlaybookDraftWrite,
    PlaybookHandoffPolicy,
    PlaybookScope,
    PlaybookSimulationWrite,
)
from app.services.configuration_service import _payload_hash
from app.services.playbook_defaults import default_playbook_content
from app.services.playbook_service import (
    DEFAULT_HEALTH_PLAYBOOK_NAMESPACE,
    PlaybookConflictError,
    PlaybookNotFoundError,
    PlaybookOfficeTemplateMissingError,
    PlaybookService,
    PlaybookValidationError,
)


async def _store_office_document(
    db_session: AsyncSession,
    *,
    workspace_id,
    filename: str = "contrato-honorarios-klm.docx",
) -> StorageObject:
    object_id = uuid4()
    storage_object = StorageObject(
        workspace_id=workspace_id,
        object_id=object_id,
        version=1,
        original_filename=filename,
        declared_content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        detected_content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        byte_size=2048,
        checksum_sha256="b" * 64,
        storage_key=f"playbooks/office-templates/{object_id}/v1",
        state=StorageObjectState.available,
        av_status=StorageAVStatus.simulated_unverified,
    )
    db_session.add(storage_object)
    await db_session.flush()
    return storage_object


def _create_data(mode: str = "ai_triage_human_meeting", *, name: str = "Saúde"):
    return PlaybookCreate(
        name=name,
        description="Triagem, qualificação e contratação em negativas de cobertura.",
        scope=PlaybookScope(
            areas=["negativas_plano_saude"],
            channels=["whatsapp"],
            stages=["triagem", "qualificacao", "proposta", "fechamento"],
            agents=["orquestrador", "sdr", "juridico", "closer", "hermes"],
            offers=["servicos_advocaticios"],
        ),
        autonomy_mode=mode,
    )


def test_legacy_hermes_dispatcher_is_migrated_without_losing_handoff_content():
    handoff = PlaybookHandoffPolicy.model_validate(
        {
            "normalDispatcher": "hermes",
            "fallbackQueue": "atendimento_humano",
            "requiredSummaryFields": ["resumo", "próxima ação"],
            "destinations": [{"key": "juridico", "label": "Jurídico", "slaMinutes": 15}],
        }
    )

    assert handoff.normal_dispatcher == "orquestrador"
    assert handoff.fallback_queue == "atendimento_humano"
    assert handoff.required_summary_fields == ["resumo", "próxima ação"]


@pytest.mark.asyncio
async def test_playbook_full_lifecycle_is_versioned_and_reversible(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )

    assert created.state is PlaybookLifecycleState.draft
    assert created.current_version == 1
    assert created.structural_coverage == 100
    assert len(created.content.agents) == 20
    assert any(agent.key == "auditor" for agent in created.content.agents)
    assert created.content.handoff.normal_dispatcher == "orquestrador"
    assert len(created.content.objections) == 14
    assert created.content.commercial.minimum_initial_fee_brl == 4000
    assert created.content.commercial.maximum_initial_fee_brl is None
    assert len(created.content.document_templates) == 7
    assert len(created.content.faqs) == 10
    assert len(created.content.response_templates) == 7
    assert len(created.content.document_variables) == 30
    simulation = await service.simulate(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookSimulationWrite(
            scenario="Medicamento oncológico urgente",
            customer_message="O plano negou meu medicamento e preciso começar nesta semana.",
            context={"channel": "whatsapp", "stage": "triagem"},
        ),
    )
    assert simulation.version == 1
    assert simulation.external_actions_executed == 0
    assert f"playbook:{created.id}:v1" in simulation.sources
    assert "policy:ai_triage_human_meeting" in simulation.sources
    assert any(trace.rule == "playbook.version_pin" for trace in simulation.policy_trace)

    in_review = await service.transition(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        target=PlaybookLifecycleState.in_review,
        reason="Cobertura e claims revisados.",
    )
    assert in_review.state is PlaybookLifecycleState.in_review
    approved = await service.transition(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        target=PlaybookLifecycleState.approved,
        reason="Simulação local aprovada.",
    )
    assert approved.state is PlaybookLifecycleState.approved
    published = await service.transition(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        target=PlaybookLifecycleState.published,
        reason="Publicação autorizada.",
    )
    assert published.state is PlaybookLifecycleState.published
    assert published.published_version == 1

    effective = await service.effective(
        workspace_id=auth_context.workspace_id,
        selectors={
            PlaybookScopeDimension.area: "negativas_plano_saude",
            PlaybookScopeDimension.channel: "whatsapp",
            PlaybookScopeDimension.stage: "triagem",
            PlaybookScopeDimension.agent: "sdr",
            PlaybookScopeDimension.offer: "servicos_advocaticios",
        },
    )
    assert effective.id == created.id
    assert effective.state is PlaybookLifecycleState.published
    assert effective.published_version == 1

    restored = await service.rollback(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        target_version=1,
        reason="Criar revisão sem alterar a versão publicada.",
    )
    assert restored.current_version == 2
    assert restored.state is PlaybookLifecycleState.draft
    assert restored.published_version == 1
    assert [version.state for version in restored.versions] == [
        PlaybookLifecycleState.draft,
        PlaybookLifecycleState.published,
    ]
    diff = await service.diff(
        workspace_id=auth_context.workspace_id,
        document_id=created.id,
        from_version=1,
        to_version=2,
    )
    assert diff.entries == []


@pytest.mark.asyncio
async def test_tenant_playbook_writes_preserve_platform_budget_without_exposing_costs(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    document = await service._document(auth_context.workspace_id, created.id)
    assert document.payload["content"]["policy"]["dailyBudgetBrl"] == 50
    assert (
        "dailyBudgetBrl" not in created.model_dump(mode="json", by_alias=True)["content"]["policy"]
    )
    assert created.content.commercial.minimum_initial_fee_brl == 4000

    content = created.content.model_dump(mode="json", by_alias=True)
    content["identityVoice"]["tone"] = "Objetivo, humano e reservado."
    content["policy"]["dailyBudgetBrl"] = 999_999
    saved = await service.save_draft(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookDraftWrite.model_validate(
            {
                "name": created.name,
                "description": created.description,
                "scope": created.scope.model_dump(mode="json", by_alias=True),
                "content": content,
                "reason": "Tenant não controla orçamento interno.",
                "expectedEtag": created.etag,
            }
        ),
        expected_etag=created.etag,
    )

    document = await service._document(auth_context.workspace_id, created.id)
    assert document.payload["content"]["policy"]["dailyBudgetBrl"] == 50
    assert "dailyBudgetBrl" not in saved.model_dump(mode="json", by_alias=True)["content"]["policy"]

    simulation = await service.simulate(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookSimulationWrite(
            scenario="Privacidade de custo interno",
            customer_message="Meu plano negou o medicamento.",
            context={"estimatedCostBrl": 123.45, "channel": "whatsapp"},
        ),
    )
    metrics = await service.metrics(
        workspace_id=auth_context.workspace_id,
        document_id=created.id,
    )
    stored_run = await db_session.scalar(
        select(PlaybookSimulationRun).where(PlaybookSimulationRun.id == simulation.id)
    )
    assert stored_run is not None
    assert "estimatedCostBrl" not in stored_run.context
    assert "estimatedCostBrl" not in simulation.model_dump(mode="json", by_alias=True)
    assert "averageCostBrl" not in metrics.model_dump(mode="json", by_alias=True)


@pytest.mark.asyncio
async def test_approved_faq_is_attributed_and_controls_the_local_answer(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )

    result = await service.simulate(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookSimulationWrite(
            scenario="Objeção comercial",
            customer_message="Quanto custa o atendimento?",
            context={"channel": "whatsapp", "stage": "qualificacao"},
        ),
    )

    assert "R$ 4.000,00" in result.response
    assert "faq:faq-fees" in result.sources
    assert "faq-source:playbook/commercial/minimum_initial_fee_brl" in result.sources
    assert any(trace.rule == "knowledge.faq" for trace in result.policy_trace)
    assert result.external_actions_executed == 0


def test_studio_library_rejects_unknown_document_variables():
    payload = default_playbook_content("ai_triage_human_meeting").model_dump(
        mode="json",
        by_alias=True,
    )
    payload["documentTemplates"][0]["sections"][0]["body"] += " {{unknown.value}}"

    with pytest.raises(ValidationError, match="references unknown variables"):
        PlaybookContent.model_validate(payload)


@pytest.mark.asyncio
async def test_health_baseline_is_idempotent_wildcard_and_loses_to_specific_scope(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    baseline = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    replay = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )

    assert replay.id == baseline.id
    assert replay.state is PlaybookLifecycleState.published
    assert replay.scope.channels == []
    assert replay.scope.stages == []
    assert replay.scope.agents == []
    assert replay.scope.offers == []
    assert replay.simulations == 1
    assert replay.simulation_pass_rate == 100

    wildcard = await service.effective(
        workspace_id=auth_context.workspace_id,
        selectors={
            PlaybookScopeDimension.area: "negativas_plano_saude",
            PlaybookScopeDimension.channel: "form",
            PlaybookScopeDimension.stage: "proposta",
            PlaybookScopeDimension.agent: "crm_precisao",
            PlaybookScopeDimension.offer: "servicos_advocaticios",
        },
    )
    assert wildcard.id == baseline.id

    specific = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(name="Saúde específica"),
    )
    await service.simulate(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=specific.id,
        data=PlaybookSimulationWrite(
            scenario="Escopo específico",
            customer_message="Meu plano negou o medicamento.",
            context={"intent": "triagem"},
        ),
    )
    for target in (
        PlaybookLifecycleState.in_review,
        PlaybookLifecycleState.approved,
        PlaybookLifecycleState.published,
    ):
        await service.transition(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            document_id=specific.id,
            target=target,
            reason=f"Validar precedência do escopo {target.value}.",
        )

    resolved = await service.effective(
        workspace_id=auth_context.workspace_id,
        selectors={
            PlaybookScopeDimension.area: "negativas_plano_saude",
            PlaybookScopeDimension.channel: "whatsapp",
            PlaybookScopeDimension.stage: "triagem",
            PlaybookScopeDimension.agent: "sdr",
            PlaybookScopeDimension.offer: "servicos_advocaticios",
        },
    )
    assert resolved.id == specific.id


@pytest.mark.asyncio
async def test_system_health_baseline_upgrades_without_overwriting_custom_playbooks(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    baseline = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    document = await service._document(auth_context.workspace_id, baseline.id, lock=True)
    legacy_content = baseline.content.model_copy(
        update={"agents": [agent for agent in baseline.content.agents if agent.key != "auditor"]},
        deep=True,
    )
    legacy_payload = PlaybookDocumentPayload(
        name=baseline.name,
        slug=baseline.slug,
        description=baseline.description,
        scope=baseline.scope,
        content=legacy_content,
        revision_nonce="system-health-baseline-v1",
    )
    document = await service.config.write_tenant(
        workspace_id=auth_context.workspace_id,
        scope=document.scope,
        namespace=DEFAULT_HEALTH_PLAYBOOK_NAMESPACE,
        payload=legacy_payload.model_dump(mode="json", by_alias=True),
        actor_user_id=auth_context.user_id,
        expected_etag=document.etag,
        reason="Fixture de baseline legado",
    )
    version_row = await service._version(document, document.current_version)
    await service._add_bindings(document, version_row, legacy_payload)
    await service._add_event(
        document=document,
        version_row=version_row,
        state=PlaybookLifecycleState.draft,
        reason="Fixture de baseline legado",
        actor_user_id=auth_context.user_id,
    )
    await service.transition(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=document.id,
        target=PlaybookLifecycleState.in_review,
        reason="Revisar fixture legado",
    )
    await service.simulate(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=document.id,
        data=PlaybookSimulationWrite(
            scenario="Baseline legado",
            customer_message="Meu plano negou o tratamento.",
            context={"source": "test"},
            version=document.current_version,
        ),
    )
    for target in (
        PlaybookLifecycleState.approved,
        PlaybookLifecycleState.published,
    ):
        await service.transition(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            document_id=document.id,
            target=target,
            reason=f"Publicar fixture legado em {target.value}",
        )

    upgraded = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    replay = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )

    assert upgraded.current_version == 3
    assert replay.current_version == upgraded.current_version
    assert upgraded.state is PlaybookLifecycleState.published
    assert len(upgraded.content.agents) == 20
    assert any(agent.key == "auditor" for agent in upgraded.content.agents)


@pytest.mark.asyncio
async def test_invalid_legacy_system_baseline_is_migrated_without_rewriting_history(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    baseline = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    document = await service._document(auth_context.workspace_id, baseline.id, lock=True)
    legacy_version = document.current_version
    legacy_row = await service._version(document, legacy_version)
    legacy_payload = deepcopy(document.payload)
    legacy_payload["revisionNonce"] = "system-health-baseline-v2-conversation-rag"
    orchestrator = next(
        agent
        for agent in legacy_payload["content"]["agents"]
        if agent["key"] == "orquestrador"
    )
    orchestrator["handoffs"] = [*orchestrator["handoffs"], "auditor", "hermes"]
    legacy_hash = _payload_hash(legacy_payload)
    document.payload = deepcopy(legacy_payload)
    document.etag = legacy_hash
    legacy_row.payload = deepcopy(legacy_payload)
    legacy_row.payload_hash = legacy_hash
    await db_session.flush()

    upgraded = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    replay = await service.ensure_health_baseline(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )

    assert upgraded.current_version == legacy_version + 1
    assert replay.current_version == upgraded.current_version
    assert upgraded.state is PlaybookLifecycleState.published
    by_key = {agent.key: agent for agent in upgraded.content.agents}
    assert "auditor" not in by_key["orquestrador"].handoffs
    assert "hermes" not in by_key["orquestrador"].handoffs
    assert by_key["secretaria"].handoffs == ["hermes"]
    assert by_key["hermes"].handoffs == []
    assert "fila_humana" in by_key["orquestrador"].handoffs

    persisted_legacy = await service._version(document, legacy_version)
    persisted_orchestrator = next(
        agent
        for agent in persisted_legacy.payload["content"]["agents"]
        if agent["key"] == "orquestrador"
    )
    assert persisted_legacy.payload["revisionNonce"] == (
        "system-health-baseline-v2-conversation-rag"
    )
    assert persisted_orchestrator["handoffs"][-2:] == ["auditor", "hermes"]


@pytest.mark.asyncio
async def test_draft_write_enforces_etag_and_creates_immutable_revision(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    content = created.content.model_copy(deep=True)
    content.identity_voice.tone = "Objetivo, humano, elegante e sem promessa de resultado."
    write = PlaybookDraftWrite(
        name=created.name,
        description=created.description,
        scope=created.scope,
        content=content,
        reason="Ajuste de tom.",
        expected_etag=created.etag,
    )
    saved = await service.save_draft(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=write,
        expected_etag=f'"{created.etag}"',
    )
    assert saved.current_version == 2
    assert saved.etag != created.etag
    assert saved.versions[1].state is PlaybookLifecycleState.superseded

    with pytest.raises(PlaybookConflictError):
        await service.save_draft(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            document_id=created.id,
            data=write,
            expected_etag=f'"{created.etag}"',
        )


@pytest.mark.asyncio
async def test_all_autonomy_modes_simulate_with_attribution_and_no_external_action(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    modes = [
        "human_only",
        "ai_triage_human_meeting",
        "ai_close_guarded",
        "ai_autonomous_supervised",
    ]
    for index, mode in enumerate(modes):
        created = await service.create(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            data=_create_data(mode, name=f"Saúde {index}"),
        )
        result = await service.simulate(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            document_id=created.id,
            data=PlaybookSimulationWrite(
                scenario="Jornada completa de saúde",
                customer_message="Quero entender a negativa e decidir a contratação.",
                context={"intent": "fechamento", "localOnly": True},
            ),
        )
        assert result.status.value == "passed"
        assert result.external_actions_executed == 0
        assert result.autonomy_mode == mode
        assert f"policy:{mode}" in result.sources
        assert any(trace.rule == "sandbox.external_actions" for trace in result.policy_trace)


@pytest.mark.asyncio
async def test_playbook_document_cannot_cross_workspace_boundary(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    other = Workspace(
        id=uuid4(),
        name="Outro escritório",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other)
    await db_session.flush()
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=other.id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    with pytest.raises(PlaybookNotFoundError):
        await service.detail(
            workspace_id=auth_context.workspace_id,
            document_id=created.id,
        )


def test_legacy_persisted_content_with_secretaria_human_queue_loads() -> None:
    """Regressão: documento persistido do modelo antigo (fila_humana num agente
    != orquestrador) deve carregar via migração, não estourar 500."""
    from app.schemas.playbook import InternalPlaybookContent

    base = default_playbook_content().model_dump(mode="json", by_alias=True)
    for agent in base["agents"]:
        if agent["key"] == "orquestrador":
            agent["handoffs"] = [h for h in agent["handoffs"] if h != "fila_humana"]
        if agent["key"] == "secretaria":
            agent.setdefault("handoffs", [])
            if "fila_humana" not in agent["handoffs"]:
                agent["handoffs"].append("fila_humana")

    migrated = InternalPlaybookContent.model_validate(base)
    by_key = {a.key: a for a in migrated.agents}
    assert "fila_humana" in by_key["orquestrador"].handoffs
    assert "fila_humana" not in by_key["secretaria"].handoffs


@pytest.mark.asyncio
async def test_office_template_upload_pins_storage_object_and_versions_playbook(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    """Item 1/4 da Frente C: o modelo timbrado do escritório é vinculado ao
    playbook versionado e trocar o modelo abre uma nova revisão."""
    storage_object = await _store_office_document(
        db_session, workspace_id=auth_context.workspace_id
    )
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    default_slots = {template.key for template in created.content.office_templates}
    assert default_slots == {
        "contrato_honorarios",
        "peticao_inicial",
        "procuracao",
        "proposta",
        "outro",
    }
    assert all(
        template.document_object_id is None for template in created.content.office_templates
    )

    content = created.content.model_copy(deep=True)
    for template in content.office_templates:
        if template.key == "contrato_honorarios":
            template.document_object_id = storage_object.object_id
            template.document_object_version = storage_object.version
            template.document_filename = storage_object.original_filename

    saved = await service.save_draft(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookDraftWrite(
            name=created.name,
            description=created.description,
            scope=created.scope,
            content=content,
            reason="Envio do contrato de honorários timbrado do escritório.",
            expected_etag=created.etag,
        ),
        expected_etag=f'"{created.etag}"',
    )

    assert saved.current_version == 2
    uploaded = next(
        template
        for template in saved.content.office_templates
        if template.key == "contrato_honorarios"
    )
    assert uploaded.document_object_id == storage_object.object_id
    assert uploaded.document_object_version == storage_object.version
    assert uploaded.formato == "docx"
    assert uploaded.obrigatorio is True


@pytest.mark.asyncio
async def test_office_template_reference_must_be_a_real_available_storage_object(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    content = created.content.model_copy(deep=True)
    for template in content.office_templates:
        if template.key == "procuracao":
            template.document_object_id = uuid4()
            template.document_object_version = 1
            template.document_filename = "procuracao-inexistente.docx"

    with pytest.raises(PlaybookValidationError, match="Invalid references"):
        await service.save_draft(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            document_id=created.id,
            data=PlaybookDraftWrite(
                name=created.name,
                description=created.description,
                scope=created.scope,
                content=content,
                reason="Tentativa com objeto de storage inexistente.",
                expected_etag=created.etag,
            ),
            expected_etag=f'"{created.etag}"',
        )


async def _publish(
    service: PlaybookService,
    *,
    workspace_id,
    actor_user_id,
    document_id,
) -> None:
    await service.transition(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        document_id=document_id,
        target=PlaybookLifecycleState.in_review,
        reason="Revisão local.",
    )
    await service.simulate(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        document_id=document_id,
        data=PlaybookSimulationWrite(
            scenario="Cenário local de verificação antes da aprovação.",
            customer_message="Preciso entender o próximo passo.",
            context={"channel": "whatsapp", "stage": "triagem"},
        ),
    )
    for target, reason in (
        (PlaybookLifecycleState.approved, "Aprovação local."),
        (PlaybookLifecycleState.published, "Publicação local."),
    ):
        await service.transition(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            document_id=document_id,
            target=target,
            reason=reason,
        )


@pytest.mark.asyncio
async def test_generation_is_fail_closed_without_an_uploaded_office_template(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    """Item 3 da Frente C: sem modelo do tipo, a geração falha com erro claro
    em vez de silenciosamente usar um modelo genérico."""
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    await _publish(
        service,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
    )

    with pytest.raises(PlaybookOfficeTemplateMissingError, match="Envie o modelo timbrado"):
        await service.resolve_office_template(
            workspace_id=auth_context.workspace_id,
            key="contrato_honorarios",
        )


@pytest.mark.asyncio
async def test_generation_resolves_the_uploaded_office_template_after_publish(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    storage_object = await _store_office_document(
        db_session,
        workspace_id=auth_context.workspace_id,
        filename="procuracao-klm.docx",
    )
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    content = created.content.model_copy(deep=True)
    for template in content.office_templates:
        if template.key == "procuracao":
            template.document_object_id = storage_object.object_id
            template.document_object_version = storage_object.version
            template.document_filename = storage_object.original_filename

    await service.save_draft(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookDraftWrite(
            name=created.name,
            description=created.description,
            scope=created.scope,
            content=content,
            reason="Envio da procuração timbrada.",
            expected_etag=created.etag,
        ),
        expected_etag=f'"{created.etag}"',
    )
    await _publish(
        service,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
    )

    resolved = await service.resolve_office_template(
        workspace_id=auth_context.workspace_id,
        key="procuracao",
    )
    assert resolved.document_object_id == storage_object.object_id
    assert resolved.document_object_version == storage_object.version

    # Outro tipo (contrato_honorarios) segue sem modelo: continua bloqueado.
    with pytest.raises(PlaybookOfficeTemplateMissingError):
        await service.resolve_office_template(
            workspace_id=auth_context.workspace_id,
            key="contrato_honorarios",
        )


@pytest.mark.asyncio
async def test_office_template_resolution_never_leaks_across_workspaces(
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    """RLS/isolamento: um workspace sem playbook publicado nunca resolve o
    modelo de outro workspace, mesmo que o tipo/chave sejam iguais."""
    other = Workspace(
        id=uuid4(),
        name="Outro escritório",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other)
    await db_session.flush()
    storage_object = await _store_office_document(
        db_session,
        workspace_id=other.id,
        filename="contrato-outro-escritorio.docx",
    )
    service = PlaybookService(db_session)
    created = await service.create(
        workspace_id=other.id,
        actor_user_id=auth_context.user_id,
        data=_create_data(),
    )
    content = created.content.model_copy(deep=True)
    for template in content.office_templates:
        if template.key == "contrato_honorarios":
            template.document_object_id = storage_object.object_id
            template.document_object_version = storage_object.version
            template.document_filename = storage_object.original_filename
    await service.save_draft(
        workspace_id=other.id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
        data=PlaybookDraftWrite(
            name=created.name,
            description=created.description,
            scope=created.scope,
            content=content,
            reason="Envio no outro workspace.",
            expected_etag=created.etag,
        ),
        expected_etag=f'"{created.etag}"',
    )
    await _publish(
        service,
        workspace_id=other.id,
        actor_user_id=auth_context.user_id,
        document_id=created.id,
    )

    with pytest.raises(PlaybookOfficeTemplateMissingError):
        await service.resolve_office_template(
            workspace_id=auth_context.workspace_id,
            key="contrato_honorarios",
        )
