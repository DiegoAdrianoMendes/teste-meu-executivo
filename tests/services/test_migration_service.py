from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.finance import FinanceEntry
from app.models.matter import Matter
from app.models.migration_job import (
    MigrationConflict,
    MigrationDomain,
    MigrationJobStatus,
    MigrationRecordStatus,
    MigrationSourceSystem,
)
from app.services.migration_parsers import (
    UnsupportedMigrationFormatError,
    parse_content,
)
from app.services.migration_service import (
    MigrationInvalidTransitionError,
    MigrationJobNotFoundError,
    MigrationService,
)

_HEADER = (
    "dominio,nome,documento,email,telefone,referencia_externa,cliente_ref,area,demanda,"
    "numero_cnj,caso_ref,descricao,valor,vencimento"
)

# CNJs válidos (dígito verificador calculado por módulo 97 base 10).
_CNJ_LINKED = "0000001-05.2026.8.07.0001"
_CNJ_UNLINKED = "0000002-87.2026.8.07.0001"
_CNJ_INVALID = "0000003-99.2026.8.07.0001"  # dígito errado de propósito


def _row(**fields: str) -> str:
    columns = _HEADER.split(",")
    return ",".join(fields.get(col, "") for col in columns)


def _build_synthetic_csv() -> bytes:
    rows = [
        _HEADER,
        _row(
            dominio="clientes",
            nome="Maria Silva",
            documento="12345678900",
            email="maria@example.com",
            telefone="61999990000",
            referencia_externa="cli-001",
        ),
        _row(
            dominio="clientes",
            nome="Joao Souza",
            email="joao1@example.com",
            telefone="61988880001",
        ),
        _row(
            dominio="clientes",
            nome="Joao Souza",
            email="joao2@example.com",
            telefone="61988880002",
        ),
        _row(
            dominio="casos",
            referencia_externa="caso-001",
            cliente_ref="cli-001",
            area="civel",
            demanda="Cobranca judicial",
        ),
        _row(dominio="processos", numero_cnj=_CNJ_LINKED, caso_ref="caso-001"),
        _row(dominio="processos", numero_cnj=_CNJ_UNLINKED),
        _row(dominio="processos", numero_cnj=_CNJ_INVALID),
        _row(
            dominio="recebimentos",
            caso_ref="caso-001",
            descricao="Honorarios contratuais",
            valor="1000.00",
            referencia_externa="rec-001",
            vencimento="2026-08-01",
        ),
        _row(
            dominio="despesas",
            caso_ref="caso-001",
            descricao="Custas processuais",
            valor="200.50",
            referencia_externa="desp-001",
            vencimento="2026-08-01",
        ),
        _row(
            dominio="prazos",
            descricao="Prazo recursal",
            caso_ref="caso-001",
            referencia_externa="prazo-001",
            vencimento="2020-01-01",
        ),
        _row(dominio="contatos", nome="Contato Teste", email="contato@example.com"),
    ]
    return ("\n".join(rows) + "\n").encode("utf-8")


async def _run_full_pipeline(db_session, workspace_id, *, batch_size: int = 50):
    service = MigrationService(db_session, workspace_id)
    job = await service.receive_and_stage(
        filename="migracao.csv",
        content_type="text/csv",
        content=_build_synthetic_csv(),
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job.id)
    while True:
        result = await service.import_batch(job.id, batch_size=batch_size)
        if result["remaining"] == 0:
            break
    job = await service.reconcile(job.id)
    return service, job


# ── Pipeline ponta a ponta ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_to_end_pipeline_clientes_casos_financeiro(db_session, auth_context):
    service, job = await _run_full_pipeline(db_session, auth_context.workspace_id)

    assert job.status == MigrationJobStatus.concluido

    clients = (await db_session.scalars(select(Client))).all()
    assert len(clients) == 3  # Maria + 2 Joao Souza (homonimos, nao mesclados)

    matters = (await db_session.scalars(select(Matter))).all()
    assert len(matters) == 1
    assert matters[0].case_number == _CNJ_LINKED

    entries = (await db_session.scalars(select(FinanceEntry))).all()
    assert len(entries) == 2
    amounts = sorted(str(e.amount) for e in entries)
    assert amounts == ["1000.00", "200.50"]
    for entry in entries:
        assert entry.matter_id == matters[0].id
        assert entry.entry_metadata["migrationJobId"] == str(job.id)

    summary = job.reconciliation_summary
    assert summary["clientes"]["origem"] == 3
    assert summary["clientes"]["importado"] == 3
    assert summary["casos"]["importado"] == 1
    assert summary["processos"]["origem"] == 3
    assert summary["processos"]["importado"] == 1
    assert summary["processos"]["rejeitado"] == 2  # sem vinculo + CNJ invalido
    assert summary["recebimentos"]["importado"] == 1
    assert summary["despesas"]["importado"] == 1
    assert summary["contatos"]["aguardandoImportador"] == 1

    divergences = await service.list_divergences(job.id)
    tipos = {d.tipo for d in divergences}
    assert "processo_sem_vinculo" in tipos
    assert "cnj_invalido" in tipos
    assert "prazo_fatal_perdido" in tipos
    assert "identidade_ambigua" in tipos
    assert "importador_pendente" in tipos

    # Vermelho: ha divergencia critica nao resolvida (processo sem vinculo /
    # prazo fatal perdido).
    assert job.semaphore == "vermelho"
    critical = [d for d in divergences if d.severity.value == "critica"]
    assert {d.tipo for d in critical} == {"processo_sem_vinculo", "prazo_fatal_perdido"}


# ── Idempotencia de recebimento ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_receive_same_bytes_is_idempotent(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    content = _build_synthetic_csv()

    job1 = await service.receive_and_stage(
        filename="migracao.csv",
        content_type="text/csv",
        content=content,
        source_system=MigrationSourceSystem.universal_csv,
    )
    job2 = await service.receive_and_stage(
        filename="migracao.csv",
        content_type="text/csv",
        content=content,
        source_system=MigrationSourceSystem.universal_csv,
    )
    assert job1.id == job2.id

    jobs = await service.list_jobs()
    assert len(jobs) == 1


# ── CNJ invalido rejeitado com motivo ─────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_cnj_rejected_with_reason(db_session, auth_context):
    service, job = await _run_full_pipeline(db_session, auth_context.workspace_id)
    records = await service.list_staging_records(job.id, domain=MigrationDomain.processos)
    invalid = next(r for r in records if r.normalized.get("numero_cnj_bruto"))
    assert invalid.record_status == MigrationRecordStatus.rejeitado
    assert "CNJ inválido" in invalid.validation_error


# ── Homonimos: ambiguous, nao merge cego ─────────────────────────────────


@pytest.mark.asyncio
async def test_homonyms_are_flagged_ambiguous_and_not_merged(db_session, auth_context):
    service, job = await _run_full_pipeline(db_session, auth_context.workspace_id)
    records = await service.list_staging_records(
        job.id, domain=MigrationDomain.clientes, conflict=MigrationConflict.ambiguous
    )
    assert len(records) == 2
    assert {r.normalized["nome"] for r in records} == {"Joao Souza"}
    target_ids = {r.target_id for r in records}
    assert len(target_ids) == 2  # duas entidades distintas, nunca mescladas


# ── Interrupcao e retomada / lote idempotente ─────────────────────────────


@pytest.mark.asyncio
async def test_import_batch_interruption_and_resumption_has_no_duplicates(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    job = await service.receive_and_stage(
        filename="migracao.csv",
        content_type="text/csv",
        content=_build_synthetic_csv(),
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job.id)

    calls = 0
    while True:
        result = await service.import_batch(job.id, batch_size=1)
        calls += 1
        if result["remaining"] == 0:
            break
        if calls > 50:
            pytest.fail("import_batch nao convergiu — possivel loop infinito")
    assert calls > 1  # de fato interrompeu em varias chamadas pequenas

    clients = (await db_session.scalars(select(Client))).all()
    assert len(clients) == 3
    matters = (await db_session.scalars(select(Matter))).all()
    assert len(matters) == 1
    entries = (await db_session.scalars(select(FinanceEntry))).all()
    assert len(entries) == 2

    # Uma vez que o lote esgotou (remaining == 0), o job já avançou para
    # 'reconciliando' — reimportar depois disso é rejeitado (fail-closed),
    # nunca duplica silenciosamente.
    with pytest.raises(MigrationInvalidTransitionError):
        await service.import_batch(job.id, batch_size=50)
    clients_again = (await db_session.scalars(select(Client))).all()
    assert len(clients_again) == 3


# ── Migracao encadeada: job2 mescla no que job1 ja criou ──────────────────


@pytest.mark.asyncio
async def test_chained_migration_merges_into_existing_client(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    csv1 = (
        _HEADER
        + "\n"
        + _row(
            dominio="clientes",
            nome="Pedro Alves",
            documento="98765432100",
            email="pedro@example.com",
            referencia_externa="cli-pedro",
        )
        + "\n"
    ).encode("utf-8")
    job1 = await service.receive_and_stage(
        filename="lote1.csv",
        content_type="text/csv",
        content=csv1,
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job1.id)
    await service.import_batch(job1.id, batch_size=50)
    await service.reconcile(job1.id)

    clients = (await db_session.scalars(select(Client))).all()
    assert len(clients) == 1
    original_client_id = clients[0].id

    # Job 2: mesmo CPF, dados levemente diferentes (reimport de outra fonte).
    csv2 = (
        _HEADER
        + "\n"
        + _row(
            dominio="clientes",
            nome="Pedro Alves Souza",
            documento="987.654.321-00",
            email="pedro.alves@example.com",
            referencia_externa="cli-pedro-v2",
        )
        + "\n"
    ).encode("utf-8")
    job2 = await service.receive_and_stage(
        filename="lote2.csv",
        content_type="text/csv",
        content=csv2,
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job2.id)
    await service.import_batch(job2.id, batch_size=50)
    await service.reconcile(job2.id)

    clients_after = (await db_session.scalars(select(Client))).all()
    assert len(clients_after) == 1  # nao duplicou — mesclou no cliente do job1

    records_job2 = await service.list_staging_records(job2.id, domain=MigrationDomain.clientes)
    assert records_job2[0].record_status == MigrationRecordStatus.mesclado
    assert records_job2[0].target_id == original_client_id


# ── Saldo financeiro divergente -> semaforo vermelho ──────────────────────


@pytest.mark.asyncio
async def test_saldo_financeiro_divergente_triggers_red_semaphore(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    csv_content = (
        _HEADER
        + "\n"
        + _row(
            dominio="recebimentos",
            descricao="Honorarios",
            valor="500.00",
            referencia_externa="rec-drift",
        )
        + "\n"
    ).encode("utf-8")
    job = await service.receive_and_stage(
        filename="financeiro.csv",
        content_type="text/csv",
        content=csv_content,
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job.id)
    await service.import_batch(job.id, batch_size=50)

    # Simula um desvio entre o que foi staged e o que está em produção
    # (ex.: edição concorrente) — a reconciliação deve pegar isso mesmo sem
    # nenhuma falha de importação.
    entry = (await db_session.scalars(select(FinanceEntry))).one()
    entry.amount = Decimal("1.00")
    await db_session.flush()

    job = await service.reconcile(job.id)
    assert job.semaphore == "vermelho"
    divergences = await service.list_divergences(job.id)
    assert any(d.tipo == "saldo_financeiro_divergente" for d in divergences)


# ── Rollback restaura estado ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_restores_state(db_session, auth_context):
    service, job = await _run_full_pipeline(db_session, auth_context.workspace_id)
    assert (await db_session.scalars(select(Client))).all()

    reverted = await service.rollback(job.id, rolled_back_by=auth_context.user_id)
    assert reverted.status == MigrationJobStatus.revertido

    assert (await db_session.scalars(select(Client))).all() == []
    assert (await db_session.scalars(select(Matter))).all() == []
    assert (await db_session.scalars(select(FinanceEntry))).all() == []

    records = await service.list_staging_records(job.id, domain=MigrationDomain.clientes)
    assert all(r.target_id is None for r in records)
    assert all(r.record_status == MigrationRecordStatus.pendente for r in records)

    # Idempotente: rollback de novo é um no-op seguro.
    reverted_again = await service.rollback(job.id, rolled_back_by=auth_context.user_id)
    assert reverted_again.status == MigrationJobStatus.revertido


@pytest.mark.asyncio
async def test_rollback_preserves_merged_entities(db_session, auth_context):
    """Job2 mesclou no cliente do job1 — revertendo job2, o cliente
    (criado pelo job1) continua existindo."""
    service = MigrationService(db_session, auth_context.workspace_id)
    csv1 = (
        _HEADER + "\n" + _row(dominio="clientes", nome="Ana Lima", documento="11122233344") + "\n"
    ).encode("utf-8")
    job1 = await service.receive_and_stage(
        filename="lote1.csv",
        content_type="text/csv",
        content=csv1,
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job1.id)
    await service.import_batch(job1.id, batch_size=50)
    await service.reconcile(job1.id)

    csv2 = (
        _HEADER
        + "\n"
        + _row(dominio="clientes", nome="Ana Lima", documento="111.222.333-44")
        + "\n"
    ).encode("utf-8")
    job2 = await service.receive_and_stage(
        filename="lote2.csv",
        content_type="text/csv",
        content=csv2,
        source_system=MigrationSourceSystem.universal_csv,
    )
    await service.build_preview(job2.id)
    await service.import_batch(job2.id, batch_size=50)
    await service.reconcile(job2.id)

    await service.rollback(job2.id, rolled_back_by=auth_context.user_id)

    clients = (await db_session.scalars(select(Client))).all()
    assert len(clients) == 1  # cliente do job1 sobreviveu


# ── RLS isola job entre workspaces ────────────────────────────────────────


@pytest.mark.asyncio
async def test_rls_isolates_job_between_workspaces(db_session, auth_context):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    other_workspace = Workspace(
        name="Outro Workspace",
        slug="outro-workspace-migracao",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    service_a = MigrationService(db_session, auth_context.workspace_id)
    job = await service_a.receive_and_stage(
        filename="migracao.csv",
        content_type="text/csv",
        content=_build_synthetic_csv(),
        source_system=MigrationSourceSystem.universal_csv,
    )

    service_b = MigrationService(db_session, other_workspace.id)
    with pytest.raises(MigrationJobNotFoundError):
        await service_b.get_job(job.id)

    assert await service_b.list_jobs() == []
    jobs_a = await service_a.list_jobs()
    assert len(jobs_a) == 1


# ── Mapeamento de campos configuravel ─────────────────────────────────────


@pytest.mark.asyncio
async def test_configurable_field_mapping(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    csv_content = (b"dominio,full_name,doc_number\nclientes,Carla Nunes,55566677788\n")
    job = await service.receive_and_stage(
        filename="mapeado.csv",
        content_type="text/csv",
        content=csv_content,
        source_system=MigrationSourceSystem.universal_csv,
        field_mapping={"clientes": {"nome": "full_name", "documento": "doc_number"}},
    )
    records = await service.list_staging_records(job.id, domain=MigrationDomain.clientes)
    assert len(records) == 1
    assert records[0].normalized["nome"] == "Carla Nunes"
    assert records[0].normalized["documento"] == "55566677788"


# ── XLSX sem lib disponivel: erro honesto, nunca finge ler ────────────────


@pytest.mark.asyncio
async def test_xlsx_without_library_marks_job_as_erro(db_session, auth_context, monkeypatch):
    import app.services.migration_parsers as parsers

    monkeypatch.setattr(parsers, "openpyxl_available", lambda: False)
    service = MigrationService(db_session, auth_context.workspace_id)
    job = await service.receive_and_stage(
        filename="planilha.xlsx",
        content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        content=b"conteudo-xlsx-fake",
        source_system=MigrationSourceSystem.universal_xlsx,
    )
    assert job.status == MigrationJobStatus.erro
    assert "openpyxl" in job.error_message


# ── Transicoes invalidas sao rejeitadas (fail-closed) ─────────────────────


@pytest.mark.asyncio
async def test_import_before_preview_is_rejected(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    job = await service.receive_and_stage(
        filename="migracao.csv",
        content_type="text/csv",
        content=_build_synthetic_csv(),
        source_system=MigrationSourceSystem.universal_csv,
    )
    with pytest.raises(MigrationInvalidTransitionError):
        await service.import_batch(job.id)


@pytest.mark.asyncio
async def test_get_job_unknown_id_raises_not_found(db_session, auth_context):
    service = MigrationService(db_session, auth_context.workspace_id)
    with pytest.raises(MigrationJobNotFoundError):
        await service.get_job(uuid4())


# ── Parser JSON ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_json_parser_grouped_by_domain(db_session, auth_context):
    import json

    service = MigrationService(db_session, auth_context.workspace_id)
    payload = {
        "clientes": [
            {"nome": "Fernanda Costa", "documento": "22233344455"},
        ],
        "casos": [
            {"cliente_ref": "cli-inexistente", "area": "civel", "demanda": "Teste JSON"},
        ],
    }
    job = await service.receive_and_stage(
        filename="migracao.json",
        content_type="application/json",
        content=json.dumps(payload).encode("utf-8"),
        source_system=MigrationSourceSystem.json,
    )
    assert job.status == MigrationJobStatus.validado
    assert job.source_manifest["formatDetected"] == "json"
    records = await service.list_staging_records(job.id, domain=MigrationDomain.clientes)
    assert len(records) == 1
    assert records[0].normalized["nome"] == "Fernanda Costa"


# ── Parser ZIP de documentos ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zip_docs_parser_stages_as_documentos_pending_importer(db_session, auth_context):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("procuracao.pdf", b"conteudo-fake-pdf")
        archive.writestr("contrato.docx", b"conteudo-fake-docx")
    service = MigrationService(db_session, auth_context.workspace_id)
    job = await service.receive_and_stage(
        filename="documentos.zip",
        content_type="application/zip",
        content=buffer.getvalue(),
        source_system=MigrationSourceSystem.zip_docs,
    )
    assert job.status == MigrationJobStatus.validado
    records = await service.list_staging_records(job.id, domain=MigrationDomain.documentos)
    assert len(records) == 2
    assert all(r.record_status == MigrationRecordStatus.pendente for r in records)
    assert all(r.normalized.get("sha256") for r in records)

    await service.build_preview(job.id)
    await service.import_batch(job.id, batch_size=50)
    job = await service.reconcile(job.id)
    divergences = await service.list_divergences(job.id)
    assert any(
        d.tipo == "importador_pendente" and d.domain == MigrationDomain.documentos
        for d in divergences
    )


@pytest.mark.parametrize("filename", ["dados.zip", "dados.xlsx"])
def test_migration_archives_are_rejected_before_zip_bomb_expansion(
    filename,
    monkeypatch,
):
    from app.services import migration_parsers

    monkeypatch.setattr(
        migration_parsers.settings,
        "IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO",
        2.0,
    )
    monkeypatch.setattr(migration_parsers, "openpyxl_available", lambda: True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * 100_000)

    with pytest.raises(UnsupportedMigrationFormatError, match="compressão"):
        parse_content(
            filename=filename,
            content_type="application/zip",
            content=buffer.getvalue(),
        )
