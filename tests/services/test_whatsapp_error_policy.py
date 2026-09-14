"""
Testes da política de erros Meta no motor de campanhas (Sprint F1.1 / W1.2-W1.4).

Cobre:
- classificação dos códigos numéricos da Graph API (classify_meta_error);
- extração de error_subcode no MetaCloudClient;
- despacho por política no motor (_process_ready_jobs): supressão permanente
  (131050), invalidação de membro (131026), parada total (368), backoff de
  lote (130429);
- kill switch automático por taxa de falha;
- template approved obrigatório no launch real (pending só em dry-run);
- claim durável: crash entre envio e commit não gera reenvio.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.whatsapp.meta_cloud_client as meta_module
from app.models.whatsapp import (
    WaConnectionStatus,
    WhatsAppConnection,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppAudienceList,
    WhatsAppAudienceListMember,
    WhatsAppCampaign,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
    WhatsAppSuppressionEntry,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.whatsapp_campaign import CampaignLaunchRequest
from app.services.whatsapp import campaign_service as campaign_service_module
from app.services.whatsapp.campaign_service import WhatsAppCampaignService
from app.services.whatsapp.errors import (
    MetaErrorDisposition,
    WhatsAppProviderError,
    classify_meta_error,
)
from app.services.whatsapp.meta_cloud_client import MetaCloudClient

# ── Helpers de cenário ──────────────────────────────────────────────────────


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"error-policy-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _campaign_scenario(
    db_session: AsyncSession,
    *,
    phones: list[str],
    template_status: MetaTemplateStatus = MetaTemplateStatus.approved,
    campaign_status: CampaignStatus = CampaignStatus.running,
):
    workspace = _workspace(f"Error policy {uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"phone-{uuid4().hex[:10]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha política de erros",
        status=campaign_status,
    )
    db_session.add(campaign)
    await db_session.flush()
    db_session.add(
        WhatsAppCampaignTemplateSnapshot(
            campaign_id=campaign.id,
            template_name="politica_erros",
            language="pt_BR",
            status=template_status,
            header_parameters=[],
            body_parameters=[],
            components=[],
            raw_payload={},
        )
    )
    recipients: list[WhatsAppCampaignRecipient] = []
    jobs: list[WhatsAppCampaignSendJob] = []
    base = datetime.now(UTC) - timedelta(minutes=10)
    for index, phone in enumerate(phones):
        recipient = WhatsAppCampaignRecipient(
            campaign_id=campaign.id,
            workspace_id=workspace.id,
            phone=phone,
            status=CampaignRecipientStatus.queued,
            send_context={},
        )
        db_session.add(recipient)
        await db_session.flush()
        job = WhatsAppCampaignSendJob(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            status=CampaignSendJobStatus.pending,
            scheduled_for=base + timedelta(seconds=index),
        )
        db_session.add(job)
        recipients.append(recipient)
        jobs.append(job)
    await db_session.commit()
    return workspace, connection, campaign, recipients, jobs


class FakeConnectionService:
    def __init__(self, connection: WhatsAppConnection) -> None:
        self.connection = connection

    async def get_connection(self, connection_id, workspace_id):
        assert connection_id == self.connection.id
        assert workspace_id == self.connection.workspace_id
        return self.connection

    async def get_default_connection(self, workspace_id, *, connected_only=False):
        return self.connection

    def get_meta_access_token(self, connection):
        return "local-test-token"

    async def resolve_meta_access_token(self, connection):
        return self.get_meta_access_token(connection)


class PolicyFakeMetaClient:
    """Cliente fake: falha com o erro configurado por telefone; senão envia."""

    def __init__(self, failures: dict[str, WhatsAppProviderError] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[str] = []

    async def send_template_message(self, **kwargs):
        to = kwargs["to"]
        self.calls.append(to)
        if to in self.failures:
            raise self.failures[to]
        return {"messages": [{"id": f"wamid-{len(self.calls)}"}]}


def _provider_error(code: int, *, message: str = "erro da graph api") -> WhatsAppProviderError:
    return WhatsAppProviderError(
        code="WA-002",
        message=message,
        status_code=400,
        provider_status_code=code,
        request_started=True,
        delivery_outcome_known=True,
    )


class PolicyCampaignService(WhatsAppCampaignService):
    """Mantém estes testes focados na Meta com uma cota comercial explícita."""

    async def _campaign_recipient_limit(
        self,
        *,
        workspace_id: UUID,
        effective_at: datetime,
    ) -> int:
        assert workspace_id
        assert effective_at
        return 100_000


def _service(
    db_session: AsyncSession,
    connection: WhatsAppConnection,
    client,
) -> WhatsAppCampaignService:
    return PolicyCampaignService(
        db_session,
        connection_service=FakeConnectionService(connection),
        meta_client=client,
    )


@pytest.mark.asyncio
async def test_exact_delivery_status_replay_keeps_one_event_and_stable_timestamp(
    db_session: AsyncSession,
):
    workspace, connection, campaign, recipients, _jobs = await _campaign_scenario(
        db_session,
        phones=["5511988880099"],
    )
    recipient = recipients[0]
    recipient.wamid = "wamid-delivery-status-replay"
    recipient.status = CampaignRecipientStatus.sent
    await db_session.commit()
    payload = {
        "id": recipient.wamid,
        "status": "delivered",
        "timestamp": "1712345678",
    }
    service = _service(db_session, connection, PolicyFakeMetaClient())

    await service.sync_delivery_status(recipient.wamid, "delivered", payload)
    await db_session.refresh(recipient)
    first_delivered_at = recipient.delivered_at

    await service.sync_delivery_status(recipient.wamid, "delivered", payload)
    await db_session.refresh(recipient)
    delivery_events = (
        await db_session.scalars(
            select(WhatsAppCampaignDeliveryEvent).where(
                WhatsAppCampaignDeliveryEvent.campaign_id == campaign.id,
                WhatsAppCampaignDeliveryEvent.provider_event_key.is_not(None),
            )
        )
    ).all()

    assert len(delivery_events) == 1
    assert len(delivery_events[0].provider_event_key or "") == 64
    occurred_at = delivery_events[0].occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    assert occurred_at == datetime.fromtimestamp(1712345678, tz=UTC)
    assert recipient.delivered_at == first_delivered_at


# ── Classificação pura ──────────────────────────────────────────────────────


def test_classify_meta_error_cobre_o_mapa_completo():
    assert classify_meta_error(131047).disposition == MetaErrorDisposition.fail_terminal
    assert classify_meta_error(131049).disposition == MetaErrorDisposition.skip_terminal
    opt_out = classify_meta_error(131050)
    assert opt_out.disposition == MetaErrorDisposition.skip_terminal
    assert opt_out.suppress_contact is True
    invalid = classify_meta_error(131026)
    assert invalid.disposition == MetaErrorDisposition.fail_terminal
    assert invalid.invalidate_member is True
    assert classify_meta_error(132000).disposition == MetaErrorDisposition.pause_campaign
    assert classify_meta_error(132001).disposition == MetaErrorDisposition.pause_campaign
    assert classify_meta_error(130429).disposition == MetaErrorDisposition.retry_batch
    assert classify_meta_error(368).disposition == MetaErrorDisposition.halt_campaign
    assert classify_meta_error(131031).disposition == MetaErrorDisposition.halt_campaign
    assert classify_meta_error(190).disposition == MetaErrorDisposition.retry_job
    assert classify_meta_error(102).disposition == MetaErrorDisposition.retry_job
    # Código desconhecido cai no retry padrão do job.
    assert classify_meta_error(999999).disposition == MetaErrorDisposition.retry_job
    assert classify_meta_error(None).disposition == MetaErrorDisposition.retry_job
    # O subcódigo tem precedência sobre o código genérico.
    assert (
        classify_meta_error(131000, 131050).disposition == MetaErrorDisposition.skip_terminal
    )


@pytest.mark.asyncio
async def test_meta_cloud_client_extrai_codigo_e_subcodigo(monkeypatch):
    class _AsyncClientStub:
        def __init__(self, response: httpx.Response) -> None:
            self.response = response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            return self.response

    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    response = httpx.Response(
        400,
        json={
            "error": {
                "message": "user opted out",
                "code": 131000,
                "error_subcode": 131050,
            }
        },
    )
    monkeypatch.setattr(
        meta_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientStub(response),
    )

    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})

    assert captured.value.provider_status_code == 131000
    assert captured.value.provider_error_subcode == 131050


# ── Política aplicada no motor ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_131050_gera_supressao_permanente_sem_retry(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phone = "5511988880001"
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=[phone]
    )
    client = PolicyFakeMetaClient({phone: _provider_error(131050)})
    service = _service(db_session, connection, client)

    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(recipients[0])
    await db_session.refresh(jobs[0])
    assert recipients[0].status == CampaignRecipientStatus.skipped
    assert "131050" in (recipients[0].last_error or "")
    assert jobs[0].status == CampaignSendJobStatus.cancelled
    suppression = (
        await db_session.execute(
            select(WhatsAppSuppressionEntry).where(
                WhatsAppSuppressionEntry.workspace_id == workspace.id,
                WhatsAppSuppressionEntry.phone == phone,
            )
        )
    ).scalars().first()
    assert suppression is not None
    assert suppression.is_active is True
    assert suppression.reason == "meta_opt_out_marketing"
    assert suppression.details["providerCode"] == 131050

    # Sem retry: reprocessar não dispara nova chamada à Meta.
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)
    assert client.calls == [phone]


@pytest.mark.asyncio
async def test_131026_invalida_membro_e_falha_terminal(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phone = "5511988880002"
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=[phone]
    )
    audience_list = WhatsAppAudienceList(workspace_id=workspace.id, name="Lista base")
    db_session.add(audience_list)
    await db_session.flush()
    member = WhatsAppAudienceListMember(
        workspace_id=workspace.id,
        audience_list_id=audience_list.id,
        phone=phone,
        status="valid",
    )
    db_session.add(member)
    await db_session.commit()

    client = PolicyFakeMetaClient({phone: _provider_error(131026)})
    service = _service(db_session, connection, client)
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(recipients[0])
    await db_session.refresh(jobs[0])
    await db_session.refresh(member)
    await db_session.refresh(audience_list)
    assert recipients[0].status == CampaignRecipientStatus.failed
    assert jobs[0].status == CampaignSendJobStatus.failed
    assert "131026" in (recipients[0].last_error or "")
    assert member.status == "invalid"
    assert member.member_metadata["invalidatedBy"]["providerCode"] == 131026
    assert audience_list.invalid_count == 1
    # Falha terminal: sem retry.
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)
    assert client.calls == [phone]


@pytest.mark.asyncio
async def test_368_para_a_campanha_imediatamente(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phones = ["5511988880003", "5511988880004"]
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=phones
    )
    client = PolicyFakeMetaClient({phones[0]: _provider_error(368)})
    service = _service(db_session, connection, client)

    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(campaign)
    await db_session.refresh(jobs[0])
    await db_session.refresh(jobs[1])
    await db_session.refresh(recipients[0])
    assert campaign.status == CampaignStatus.paused
    assert campaign.paused_at is not None
    # Parada total: nenhum envio adicional depois do erro de conta restrita.
    assert client.calls == [phones[0]]
    # O job que disparou o erro e o restante do lote voltam para a fila.
    assert jobs[0].status == CampaignSendJobStatus.pending
    assert jobs[1].status == CampaignSendJobStatus.pending
    assert "368" in (recipients[0].last_error or "")
    pause_event = (
        await db_session.execute(
            select(WhatsAppCampaignDeliveryEvent).where(
                WhatsAppCampaignDeliveryEvent.campaign_id == campaign.id,
            )
        )
    ).scalars().all()
    assert any(
        event.payload.get("event") == "campaign_auto_paused"
        and event.payload.get("providerCode") == 368
        for event in pause_event
    )
    # Campanha pausada: reprocessar não envia nada.
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)
    assert client.calls == [phones[0]]


@pytest.mark.asyncio
async def test_130429_reagenda_o_lote_inteiro_com_backoff(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phones = ["5511988880005", "5511988880006"]
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=phones
    )
    client = PolicyFakeMetaClient({phones[0]: _provider_error(130429)})
    service = _service(db_session, connection, client)

    before = datetime.now(UTC)
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(campaign)
    for job in jobs:
        await db_session.refresh(job)
    for recipient in recipients:
        await db_session.refresh(recipient)
    # Só a primeira chamada aconteceu; o lote inteiro foi reagendado.
    assert client.calls == [phones[0]]
    assert campaign.status == CampaignStatus.running
    for job in jobs:
        assert job.status == CampaignSendJobStatus.pending
        scheduled_for = job.scheduled_for
        assert scheduled_for is not None
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=UTC)
        assert scheduled_for > before
    for recipient in recipients:
        assert recipient.status == CampaignRecipientStatus.queued
    # Backoff de lote não consome tentativa do job.
    assert jobs[0].attempt_count == 0


@pytest.mark.asyncio
async def test_kill_switch_pausa_campanha_acima_de_20_pct(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    failing_phone = "5511988881000"
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=[failing_phone]
    )
    # Histórico prévio: 16 enviados + 4 falhas. Com a falha nova: 5/21 ≈ 23,8%.
    for index in range(20):
        db_session.add(
            WhatsAppCampaignRecipient(
                campaign_id=campaign.id,
                workspace_id=workspace.id,
                phone=f"55119888810{index + 1:02d}",
                status=CampaignRecipientStatus.sent
                if index < 16
                else CampaignRecipientStatus.failed,
                send_context={},
            )
        )
    await db_session.commit()

    client = PolicyFakeMetaClient({failing_phone: _provider_error(131047)})
    service = _service(db_session, connection, client)
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(campaign)
    assert campaign.status == CampaignStatus.paused
    assert campaign.failed_count == 5
    assert campaign.sent_count == 16
    events = (
        await db_session.execute(
            select(WhatsAppCampaignDeliveryEvent).where(
                WhatsAppCampaignDeliveryEvent.campaign_id == campaign.id,
            )
        )
    ).scalars().all()
    assert any(
        event.payload.get("event") == "campaign_auto_paused"
        and "Kill switch" in str(event.payload.get("pauseReason"))
        for event in events
    )


@pytest.mark.asyncio
async def test_kill_switch_respeita_o_minimo_de_tentativas(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    failing_phone = "5511988882000"
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=[failing_phone]
    )
    # Apenas 2 tentativas anteriores: taxa alta, mas abaixo do mínimo de 20.
    db_session.add(
        WhatsAppCampaignRecipient(
            campaign_id=campaign.id,
            workspace_id=workspace.id,
            phone="5511988882001",
            status=CampaignRecipientStatus.sent,
            send_context={},
        )
    )
    db_session.add(
        WhatsAppCampaignRecipient(
            campaign_id=campaign.id,
            workspace_id=workspace.id,
            phone="5511988882002",
            status=CampaignRecipientStatus.failed,
            send_context={},
        )
    )
    await db_session.commit()

    client = PolicyFakeMetaClient({failing_phone: _provider_error(131047)})
    service = _service(db_session, connection, client)
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(campaign)
    # Sem kill switch: com a fila vazia a campanha fecha normalmente.
    assert campaign.status == CampaignStatus.completed


# ── Template approved obrigatório no launch real ────────────────────────────


@pytest.mark.asyncio
async def test_template_pending_bloqueia_launch_real_mas_permite_dry_run(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    workspace, connection, campaign, _recipients, _jobs = await _campaign_scenario(
        db_session,
        phones=[],
        template_status=MetaTemplateStatus.pending,
        campaign_status=CampaignStatus.draft,
    )
    service = _service(db_session, connection, PolicyFakeMetaClient())

    # Dry-run com template pendente continua permitido.
    read = await service.launch_campaign(
        workspace.id,
        campaign.id,
        CampaignLaunchRequest(dry_run=True, sample_size=5),
    )
    assert read.status == CampaignStatus.draft

    # Disparo real com template pendente é bloqueado com código claro.
    with pytest.raises(HTTPException) as blocked:
        await service.launch_campaign(
            workspace.id,
            campaign.id,
            CampaignLaunchRequest(dry_run=False, sample_size=5),
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "WA-CAM-012"


# ── Claim durável / idempotência ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_duravel_impede_reenvio_apos_crash(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phone = "5511988883000"
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=[phone]
    )
    client = PolicyFakeMetaClient()
    service = _service(db_session, connection, client)
    # Snapshot dos IDs: o rollback pós-crash expira os objetos ORM.
    campaign_id = campaign.id
    workspace_id = workspace.id

    real_commit = db_session.commit
    commit_calls = {"count": 0}

    async def crashing_commit():
        commit_calls["count"] += 1
        if client.calls:
            raise RuntimeError("crash simulado entre o envio e o commit")
        await real_commit()

    # O número de commits anteriores ao provider é detalhe de implementação
    # (por exemplo, a reserva transacional de contatos). A prova relevante é
    # que o crash acontece depois do efeito Meta e antes de persistir o resultado.
    monkeypatch.setattr(db_session, "commit", crashing_commit)
    with pytest.raises(RuntimeError, match="crash simulado"):
        await service._process_ready_jobs(campaign_id, connection, workspace_id=workspace_id)
    monkeypatch.setattr(db_session, "commit", real_commit)
    await db_session.rollback()

    # O envio aconteceu exatamente uma vez e o claim ficou durável.
    assert client.calls == [phone]
    await db_session.refresh(jobs[0])
    await db_session.refresh(recipients[0])
    assert jobs[0].status == CampaignSendJobStatus.processing
    assert recipients[0].send_context.get("claim", {}).get("claimedAt")

    # Reprocessar NÃO reenvia: o job em processing não volta para a fila.
    await service._process_ready_jobs(campaign_id, connection, workspace_id=workspace_id)
    assert client.calls == [phone]
    await db_session.refresh(campaign)
    # O job segue aguardando reconciliação; a campanha não fecha sozinha.
    assert campaign.status == CampaignStatus.running


@pytest.mark.asyncio
async def test_delivery_uncertain_congela_o_job_sem_retry(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phone = "5511988884000"
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=[phone]
    )
    uncertain = WhatsAppProviderError(
        code="WA-DELIVERY-UNCERTAIN",
        message="A conexão com a Meta foi interrompida após o início da requisição.",
        status_code=502,
        request_started=True,
        delivery_outcome_known=False,
    )
    client = PolicyFakeMetaClient({phone: uncertain})
    service = _service(db_session, connection, client)

    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    await db_session.refresh(jobs[0])
    await db_session.refresh(recipients[0])
    await db_session.refresh(campaign)
    assert jobs[0].status == CampaignSendJobStatus.processing
    assert (jobs[0].last_error or "").startswith("WA-DELIVERY-UNCERTAIN")
    assert recipients[0].status == CampaignRecipientStatus.processing
    assert recipients[0].send_context.get("deliveryUncertain")
    # Entrega incerta não bloqueia o fechamento: a fila útil está vazia.
    assert campaign.status == CampaignStatus.completed
    # Sem retry automático: reprocessar não chama a Meta de novo.
    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)
    assert client.calls == [phone]


# ── V4 (backend): lançamento fora de rascunho ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "campaign_status", [CampaignStatus.completed, CampaignStatus.paused]
)
async def test_launch_estrito_recusa_campanha_fora_de_rascunho(
    db_session: AsyncSession, monkeypatch, campaign_status
):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    workspace, connection, campaign, _recipients, _jobs = await _campaign_scenario(
        db_session, phones=[], campaign_status=campaign_status
    )
    service = _service(db_session, connection, PolicyFakeMetaClient())

    # Padrão (envio rápido, flows, API pública): replay silencioso, nada muda.
    read = await service.launch_campaign(
        workspace.id, campaign.id, CampaignLaunchRequest(dry_run=False)
    )
    assert read.status == campaign_status

    # Endpoint explícito: 409 com o status na resposta.
    with pytest.raises(HTTPException) as rejected:
        await service.launch_campaign(
            workspace.id,
            campaign.id,
            CampaignLaunchRequest(dry_run=False),
            reject_terminal_status=True,
        )
    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "WA-CAM-015"
    assert rejected.value.detail["status"] == campaign_status.value
    message = rejected.value.detail["message"]
    assert "rascunho" in message
    assert campaign_service_module._LAUNCH_STATUS_LABELS[campaign_status] in message
    assert ("se retoma" in message) == (campaign_status == CampaignStatus.paused)


@pytest.mark.asyncio
async def test_launch_estrito_preserva_replay_de_campanha_em_execucao(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    workspace, connection, campaign, _recipients, _jobs = await _campaign_scenario(
        db_session, phones=[], campaign_status=CampaignStatus.running
    )
    service = _service(db_session, connection, PolicyFakeMetaClient())

    read = await service.launch_campaign(
        workspace.id,
        campaign.id,
        CampaignLaunchRequest(dry_run=False),
        reject_terminal_status=True,
    )
    assert read.status == CampaignStatus.running

