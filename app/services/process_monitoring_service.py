"""ProcessMonitoringService — módulo processual contratável à parte.

Regras do dossiê `app/agents/knowledge/processual_monitoramento.py`:
  * quota por `EntitlementKey.monitored_processes` (stock — ver
    `app/services/entitlement_service.py`); 0/ausente desliga TODA a
    superfície do módulo com uma recusa limpa
    (`ProcessMonitoringModuleNotContractedError`), inclusive leitura;
  * ingestão de publicação é idempotente por `workspace_id + fonte +
    external_id` (dedupe — reprocessar a mesma publicação nunca duplica);
  * cálculo de prazo é 100% determinístico
    (`app/services/prazo_calculator.py`) e nasce sempre `prazo_sugerido`:
    NUNCA vira prazo fatal/confirmado sem confirmação humana explícita
    (dossiê regra 10) — ver `confirm_deadline`/`dismiss_deadline`;
  * alerta de publicação nova gera sempre uma notificação interna e, só com
    o gate de entrega externa aberto, WhatsApp ao advogado responsável via
    `PlatformWhatsAppAlertService` (motor institucional já existente);
  * publicação nova (com matter vinculado) ou prazo confirmado, quando o
    caso tem cliente com conversa canônica já existente, enfileira a
    intenção de comunicação do agente de Pós-venda
    (`app.agents.stubs.klm_pos_venda`) como evento de auditoria local — nunca
    envia nada; é sempre "registro simulado com o texto pronto" (dossiê
    `app/agents/knowledge/pos_venda_cliente.py`).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Optional, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.stubs.klm_pos_venda import classify_evento_tipo, run_pos_venda
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.approval import ApprovalPriority
from app.models.klm_operations import KlmAuditEvent, OmnichannelThread
from app.models.legal_exception import ExceptionKind
from app.models.matter import Matter
from app.models.notification import Notification, NotificationType
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
    ProcessPublicationSource,
)
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.cnj_numero import InvalidCnjNumberError, normalizar_numero_cnj
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementDisabledError,
    EntitlementEnforcementService,
    EntitlementError,
    EntitlementKey,
    EntitlementNotConfiguredError,
)
from app.services.exception_center_service import ExceptionCenterService
from app.services.oab_numero import InvalidOabError, normalizar_oab
from app.services.platform_whatsapp_alert_service import PlatformWhatsAppAlertService
from app.services.prazo_calculator import (
    TipoContagem,
    TipoParte,
    calcular_prazo,
    classificar_urgencia,
)
from app.services.process_monitoring_errors import (
    ProcessMonitoringError,
    ProcessMonitoringInvalidOabError,
)
from app.services.tribunal_datajud import alias_datajud_do_cnj, normalizar_alias_tribunal


def resolver_alias_tribunal(
    numero_cnj: str, informado: str | None
) -> tuple[str | None, str | None]:
    """`(alias, motivo)`: o derivado do CNJ quando existe; o informado só como último recurso.

    O número diz o tribunal (J e TR). Um alias informado válido mas errado para
    o número ("tjsp" num 8.07) consultaria o índice errado para sempre, sem 404
    e sem hit, e a consulta "bem-sucedida" ainda limparia o erro anterior. Por
    isso o informado só vale quando o número não deriva nada.
    """
    derivado, motivo = alias_datajud_do_cnj(numero_cnj)
    informado_normalizado = normalizar_alias_tribunal(informado)
    if derivado:
        if informado_normalizado and informado_normalizado != derivado:
            logger.warning(
                "process_monitoring.tribunal_informado_diverge_do_cnj",
                numero_cnj=numero_cnj,
                informado=informado_normalizado,
                derivado=derivado,
            )
        return derivado, None
    if informado_normalizado:
        return informado_normalizado, None
    return None, motivo


def _oab_canonica_ou_erro(valor: object) -> str | None:
    if valor is None:
        return None
    try:
        return normalizar_oab(str(valor))
    except InvalidOabError as error:
        raise ProcessMonitoringInvalidOabError(str(error)) from error


def registrar_erro_de_fonte(process: MonitoredProcess, fonte: str, mensagem: str) -> None:
    """`last_error` com prefixo da fonte, para que a limpeza saiba de quem é o erro."""
    process.last_error = f"{fonte}: {mensagem}"


def limpar_erro_de_fonte(process: MonitoredProcess, fonte: str) -> None:
    """Só apaga o erro que a própria fonte escreveu. Sucesso no DJEN não pode
    apagar um 404 do DataJud, que continua verdadeiro."""
    if process.last_error and process.last_error.startswith(f"{fonte}: "):
        process.last_error = None


class ProcessMonitoringModuleNotContractedError(ProcessMonitoringError):
    def __init__(self, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id
        super().__init__(
            f"Módulo de monitoramento processual não contratado para o workspace {workspace_id}."
        )


class ProcessMonitoringInvalidCnjError(ProcessMonitoringError):
    pass


class ProcessMonitoringDuplicateProcessError(ProcessMonitoringError):
    def __init__(self, numero_cnj: str) -> None:
        self.numero_cnj = numero_cnj
        super().__init__(f"Processo {numero_cnj} já está cadastrado neste workspace.")


class ProcessMonitoringNotFoundError(ProcessMonitoringError):
    pass


class ProcessMonitoringDeadlineStateError(ProcessMonitoringError):
    pass


class ProcessMonitoringService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Gate do módulo (add-on) ───────────────────────────────────────────

    async def ensure_module_contracted(self, workspace_id: UUID) -> None:
        """Fachada pública do gate do add-on, para o worker checar antes da rede."""
        await self._require_module_contracted(workspace_id)

    async def _require_module_contracted(self, workspace_id: UUID) -> None:
        enforcement = EntitlementEnforcementService(self.db)
        try:
            # `lock_workspace` ficava fora do try e levantava
            # EntitlementNotConfiguredError / EntitlementWorkspaceNotFoundError
            # cruas para workspace sem registro de assinatura (piloto, demo,
            # provisionamento em andamento). Na API isso virava 500; no beat,
            # com o fan-out por workspace ativo, viraria estouro diário. Não ter
            # assinatura é exatamente "não contratou", e passa a ser respondido assim.
            has_subscription = await enforcement.lock_workspace(workspace_id)
            if not has_subscription:
                # Diferente do bypass legado de outros entitlements de estoque:
                # este add-on nasceu depois do catálogo de planos e deve ficar
                # OFF por padrão até existir uma assinatura com quota explícita.
                raise ProcessMonitoringModuleNotContractedError(workspace_id)
            await enforcement.enforce_feature(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.legal_suite,
            )
            resolved = await enforcement.resolver.resolve(
                workspace_id, EntitlementKey.monitored_processes
            )
        except EntitlementError as error:
            raise ProcessMonitoringModuleNotContractedError(workspace_id) from error
        if not resolved.enabled or (
            resolved.limit_quantity is not None and resolved.limit_quantity <= 0
        ):
            raise ProcessMonitoringModuleNotContractedError(workspace_id)

    async def _enforce_quota(
        self,
        workspace_id: UUID,
        *,
        exclude_process_id: Optional[UUID] = None,
    ) -> None:
        enforcement = EntitlementEnforcementService(self.db)

        async def _load_current_quantity() -> int:
            statement = select(func.count(MonitoredProcess.id)).where(
                MonitoredProcess.workspace_id == workspace_id,
                MonitoredProcess.status == MonitoredProcessStatus.ativo,
            )
            if exclude_process_id is not None:
                statement = statement.where(MonitoredProcess.id != exclude_process_id)
            return int(await self.db.scalar(statement) or 0)

        try:
            await enforcement.enforce_stock(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.monitored_processes,
                load_current_quantity=_load_current_quantity,
            )
        except (EntitlementDisabledError, EntitlementNotConfiguredError) as error:
            raise ProcessMonitoringModuleNotContractedError(workspace_id) from error

    # ── Processos monitorados ─────────────────────────────────────────────

    async def register_process(
        self,
        workspace_id: UUID,
        *,
        numero_cnj: str,
        created_by_user_id: UUID,
        tribunal: Optional[str] = None,
        matter_id: Optional[UUID] = None,
        oab_vinculada: Optional[str] = None,
        responsible_user_id: Optional[UUID] = None,
    ) -> MonitoredProcess:
        await self._require_module_contracted(workspace_id)

        try:
            numero_normalizado = normalizar_numero_cnj(numero_cnj)
        except InvalidCnjNumberError as error:
            raise ProcessMonitoringInvalidCnjError(str(error)) from error
        oab_canonica = _oab_canonica_ou_erro(oab_vinculada)

        existing = await self.db.scalar(
            select(MonitoredProcess).where(
                MonitoredProcess.workspace_id == workspace_id,
                MonitoredProcess.numero_cnj == numero_normalizado,
            )
        )
        if existing is not None:
            raise ProcessMonitoringDuplicateProcessError(numero_normalizado)

        if matter_id is not None:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == matter_id, Matter.workspace_id == workspace_id
                )
            )
            if matter is None:
                raise ProcessMonitoringNotFoundError(
                    f"Caso {matter_id} não encontrado neste workspace."
                )
            if responsible_user_id is None and matter.responsible_human_id is not None:
                # Herdado do caso: se o advogado do caso foi suspenso e ninguém
                # reatribuiu o caso, isso não pode derrubar um cadastro em que o
                # usuário nem preencheu o campo. Cai para "sem responsável", com log.
                if await self._e_membro_ativo(workspace_id, matter.responsible_human_id):
                    responsible_user_id = matter.responsible_human_id
                else:
                    logger.warning(
                        "process_monitoring.responsavel_do_caso_inativo",
                        workspace_id=str(workspace_id),
                        matter_id=str(matter_id),
                        user_id=str(matter.responsible_human_id),
                    )
        else:
            await self._validar_responsavel(workspace_id, responsible_user_id)

        if matter_id is None:
            await self._validar_responsavel(workspace_id, responsible_user_id)

        await self._enforce_quota(workspace_id)

        # `tribunal` guarda o alias do índice do DataJud, e não texto livre. O
        # próprio número CNJ diz o tribunal (J e TR): se o que veio já é um alias
        # válido, fica; senão, deriva-se do número. Sem alias, o erro vai para
        # `last_error` já no cadastro, e não às 4h da manhã num log que ninguém lê.
        alias, motivo = resolver_alias_tribunal(numero_normalizado, tribunal)

        process = MonitoredProcess(
            workspace_id=workspace_id,
            matter_id=matter_id,
            numero_cnj=numero_normalizado,
            tribunal=alias,
            status=MonitoredProcessStatus.ativo,
            oab_vinculada=oab_canonica,
            responsible_user_id=responsible_user_id,
            created_by_user_id=created_by_user_id,
            last_error=(f"DataJud: {motivo}" if motivo else None),
        )
        self.db.add(process)
        await self.db.flush()
        return process

    async def _validar_responsavel(self, workspace_id: UUID, user_id: UUID | None) -> None:
        """Responsável tem de ser membro ativo do workspace.

        O cadastro nunca validava isso, e `_notify_new_publication` cria
        notificação e manda WhatsApp para esse id: aceitar id de fora era um
        caminho de vazamento. Mesmo critério de `MatterService`.
        """
        if user_id is None:
            return
        if not await self._e_membro_ativo(workspace_id, user_id):
            raise ProcessMonitoringNotFoundError(
                "Responsável não é membro ativo deste workspace."
            )

    async def _e_membro_ativo(self, workspace_id: UUID, user_id: UUID) -> bool:
        existe = await self.db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        return existe is not None

    async def update_process(
        self, workspace_id: UUID, process_id: UUID, *, campos: dict[str, object]
    ) -> MonitoredProcess:
        """Atualização parcial: campo ausente não mexe; `None` explícito limpa.

        `campos` vem de `MonitoredProcessUpdate.model_dump(exclude_unset=True)`.
        Aceita `oab_vinculada`, `matter_id` e `responsible_user_id`. `tribunal`
        não entra: o worker recalcula o alias do próprio número a cada tick e
        reverteria qualquer override no tick seguinte.

        `last_error` só é limpo pela regra de prefixo, e só quando a OAB muda
        de fato: a OAB só diz respeito ao DJEN, então um 404 do DataJud
        continua verdadeiro depois de trocar a OAB, e gravar a mesma OAB de
        novo não conserta um 400 que vai voltar.
        """
        await self._require_module_contracted(workspace_id)
        process = await self._get_process(workspace_id, process_id)

        if "oab_vinculada" in campos:
            nova = _oab_canonica_ou_erro(campos["oab_vinculada"])  # type: ignore[arg-type]
            if nova != process.oab_vinculada:
                process.oab_vinculada = nova
                limpar_erro_de_fonte(process, "DJEN")
        if "matter_id" in campos:
            matter_id = campos["matter_id"]
            if matter_id is not None:
                matter = await self.db.scalar(
                    select(Matter).where(
                        Matter.id == matter_id, Matter.workspace_id == workspace_id
                    )
                )
                if matter is None:
                    raise ProcessMonitoringNotFoundError(
                        f"Caso {matter_id} não encontrado neste workspace."
                    )
            # Sem herdar o responsável do caso: no PATCH só vale o explícito.
            process.matter_id = matter_id  # type: ignore[assignment]
        if "responsible_user_id" in campos:
            responsavel = campos["responsible_user_id"]
            await self._validar_responsavel(workspace_id, responsavel)  # type: ignore[arg-type]
            process.responsible_user_id = responsavel  # type: ignore[assignment]
        await self.db.flush()
        return process

    async def _get_process(self, workspace_id: UUID, process_id: UUID) -> MonitoredProcess:
        process = await self.db.scalar(
            select(MonitoredProcess).where(
                MonitoredProcess.id == process_id,
                MonitoredProcess.workspace_id == workspace_id,
            )
        )
        if process is None:
            raise ProcessMonitoringNotFoundError(
                f"Processo monitorado {process_id} não encontrado neste workspace."
            )
        return process

    async def pause_process(self, workspace_id: UUID, process_id: UUID) -> MonitoredProcess:
        await self._require_module_contracted(workspace_id)
        process = await self._get_process(workspace_id, process_id)
        if process.status != MonitoredProcessStatus.pausado:
            process.status = MonitoredProcessStatus.pausado
            await self.db.flush()
        return process

    async def resume_process(self, workspace_id: UUID, process_id: UUID) -> MonitoredProcess:
        await self._require_module_contracted(workspace_id)
        process = await self._get_process(workspace_id, process_id)
        if process.status != MonitoredProcessStatus.ativo:
            await self._enforce_quota(workspace_id, exclude_process_id=process.id)
            process.status = MonitoredProcessStatus.ativo
            process.last_error = None
            await self.db.flush()
        return process

    async def list_processes(
        self,
        workspace_id: UUID,
        *,
        status: Optional[MonitoredProcessStatus] = None,
    ) -> list[MonitoredProcess]:
        await self._require_module_contracted(workspace_id)
        statement = select(MonitoredProcess).where(MonitoredProcess.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(MonitoredProcess.status == status)
        statement = statement.order_by(MonitoredProcess.created_at.desc())
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    # ── Ingestão idempotente de publicações ───────────────────────────────

    async def ingest_publication(
        self,
        workspace_id: UUID,
        *,
        fonte: ProcessPublicationSource,
        external_id: str,
        numero_cnj: str,
        conteudo: str,
        published_at: datetime,
        tipo: Optional[str] = None,
        tipo_ato: Optional[str] = None,
        dias: Optional[int] = None,
        tipo_contagem: Optional[TipoContagem] = None,
        tipo_parte: TipoParte = TipoParte.padrao,
        data_disponibilizacao: Optional[Union[date, datetime]] = None,
        data_consulta: Optional[Union[date, datetime]] = None,
    ) -> tuple[ProcessPublication, bool]:
        """Ingesta idempotente. Retorna `(publicação, criada)` — `criada=False`
        numa reingestão da mesma `(workspace_id, fonte, external_id)`."""
        await self._require_module_contracted(workspace_id)

        existing = await self.db.scalar(
            select(ProcessPublication).where(
                ProcessPublication.workspace_id == workspace_id,
                ProcessPublication.fonte == fonte,
                ProcessPublication.external_id == external_id,
            )
        )
        if existing is not None:
            return existing, False

        monitored_process = await self.db.scalar(
            select(MonitoredProcess).where(
                MonitoredProcess.workspace_id == workspace_id,
                MonitoredProcess.numero_cnj == numero_cnj,
            )
        )

        dias_calculados: Optional[int] = None
        tipo_contagem_final: Optional[TipoContagem] = None
        data_fatal = None
        memoria_calculo: Optional[list] = None
        prazo_status: Optional[DeadlineStatus] = None
        dias_efetivos_para_urgencia = 0

        if dias is not None and tipo_contagem is not None:
            resultado = calcular_prazo(
                data_disponibilizacao=(
                    data_disponibilizacao.date()
                    if isinstance(data_disponibilizacao, datetime)
                    else (data_disponibilizacao or published_at.date())
                ),
                dias=dias,
                tipo_contagem=tipo_contagem,
                tipo_parte=tipo_parte,
                data_consulta=(
                    data_consulta.date() if isinstance(data_consulta, datetime) else data_consulta
                ),
            )
            dias_calculados = resultado.dias_efetivos
            tipo_contagem_final = resultado.tipo_contagem
            data_fatal = resultado.data_fatal
            memoria_calculo = [dict(passo) for passo in resultado.memoria_calculo]
            prazo_status = DeadlineStatus.prazo_sugerido
            dias_efetivos_para_urgencia = resultado.dias_efetivos

        urgencia = classificar_urgencia(
            tipo_ato=tipo_ato, dias_uteis_equivalentes=dias_efetivos_para_urgencia
        )

        publication = ProcessPublication(
            workspace_id=workspace_id,
            monitored_process_id=monitored_process.id if monitored_process else None,
            fonte=fonte,
            external_id=external_id,
            numero_cnj=numero_cnj,
            conteudo=conteudo,
            published_at=published_at,
            tipo=tipo,
            urgencia=urgencia,
            dias=dias_calculados,
            tipo_contagem=tipo_contagem_final,
            data_fatal=data_fatal,
            memoria_calculo=memoria_calculo,
            prazo_status=prazo_status,
        )
        self.db.add(publication)
        await self.db.flush()

        if monitored_process is not None:
            last_movement_at = monitored_process.last_movement_at
            if last_movement_at is not None and last_movement_at.tzinfo is None:
                # SQLite não retém tzinfo — normaliza antes de comparar.
                last_movement_at = last_movement_at.replace(tzinfo=UTC)
            published_at_comparable = published_at
            if published_at_comparable.tzinfo is None:
                published_at_comparable = published_at_comparable.replace(tzinfo=UTC)
            if last_movement_at is None or published_at_comparable > last_movement_at:
                monitored_process.last_movement_at = published_at
            await self._notify_new_publication(monitored_process, publication)
            evento_tipo = classify_evento_tipo(tipo=tipo, tipo_ato=tipo_ato)
            await self._enqueue_pos_venda_intent(
                monitored_process, publication, evento_tipo=evento_tipo
            )
        else:
            # Publicação ingerida mas nenhum MonitoredProcess bate com o
            # numero_cnj — hoje isso ficava mudo (nem log, nem notificação).
            # Abre a Central de Exceções (Plano Mestre §6/§3) para que um
            # humano decida o vínculo, em vez de a publicação sumir sem
            # ninguém saber que ela existe.
            await self._open_unlinked_publication_exception(workspace_id, publication)

        return publication, True

    async def _open_unlinked_publication_exception(
        self,
        workspace_id: UUID,
        publication: ProcessPublication,
    ) -> None:
        await ExceptionCenterService(self.db, workspace_id).open_exception(
            exception_kind=ExceptionKind.publicacao_sem_vinculo_confiavel,
            origin="process_monitoring.ingest_publication",
            dedupe_key=f"{publication.fonte.value}:{publication.external_id}",
            title=f"Publicação sem processo vinculado — {publication.numero_cnj}",
            description=(
                "Nenhum MonitoredProcess deste workspace corresponde ao "
                f"número CNJ {publication.numero_cnj}. Confirme o vínculo "
                "manualmente ou cadastre o processo monitorado."
            ),
            priority=(
                ApprovalPriority.alta
                if publication.urgencia == "critica"
                else ApprovalPriority.media
            ),
            payload={
                "numeroCnj": publication.numero_cnj,
                "publicationId": str(publication.id),
                "fonte": publication.fonte.value,
                "externalId": publication.external_id,
                "urgencia": publication.urgencia,
            },
        )

    async def _notify_new_publication(
        self,
        monitored_process: MonitoredProcess,
        publication: ProcessPublication,
    ) -> None:
        target_user_id = (
            monitored_process.responsible_user_id or monitored_process.created_by_user_id
        )
        title = f"Nova publicação — processo {monitored_process.numero_cnj}"
        message = publication.conteudo[:500]
        if publication.data_fatal:
            message = (
                f"Prazo sugerido: {publication.data_fatal.isoformat()} "
                f"({publication.dias} dias {publication.tipo_contagem.value if publication.tipo_contagem else ''}). "
                "Confirme a memória de cálculo antes de lançar na agenda. "
                + message
            )
        self.db.add(
            Notification(
                user_id=target_user_id,
                workspace_id=monitored_process.workspace_id,
                matter_id=monitored_process.matter_id,
                title=title,
                message=message,
                notification_type=(
                    NotificationType.urgent
                    if publication.urgencia == "critica"
                    else NotificationType.warning
                    if publication.urgencia == "alta"
                    else NotificationType.info
                ),
            )
        )
        await self.db.flush()

        if not external_delivery_allowed():
            return
        responsible = await self.db.scalar(select(User).where(User.id == target_user_id))
        if responsible is None or not responsible.phone:
            logger.warning(
                "process_monitoring.whatsapp_alert_skipped_no_phone",
                workspace_id=str(monitored_process.workspace_id),
                monitored_process_id=str(monitored_process.id),
            )
            return
        try:
            await PlatformWhatsAppAlertService(self.db).send_text(
                responsible.phone,
                message,
                idempotency_key=(
                    f"process-publication-alert:{publication.id}:{target_user_id}"
                ),
                source_id=f"process-publication:{publication.id}",
                requested_by_user_id=target_user_id,
            )
        except Exception:  # pragma: no cover — alerta nunca pode derrubar a ingestão
            logger.warning(
                "process_monitoring.whatsapp_alert_failed",
                workspace_id=str(monitored_process.workspace_id),
                monitored_process_id=str(monitored_process.id),
            )

    # ── Pós-venda: intenção de comunicação ao cliente (gated, local) ──────

    async def _enqueue_pos_venda_intent(
        self,
        monitored_process: MonitoredProcess,
        publication: ProcessPublication,
        *,
        evento_tipo: str,
    ) -> None:
        """Enfileira (local, gated) a intenção de comunicação de pós-venda.

        Só age quando o processo monitorado está vinculado a um `Matter` com
        cliente e esse cliente já tem uma conversa canônica
        (`OmnichannelThread`) no workspace — sem isso, não há para onde
        endereçar a mensagem e a função não faz nada. Nunca envia mensagem
        real: chama `run_pos_venda` (fail-closed em três camadas — evento
        trivial, evento sensível e gate de modelo fechado nunca fabricam
        texto sem necessidade) e persiste o resultado como evento de
        auditoria — "registro simulado com o texto pronto", nunca um
        dispatch real (ver `app.agents.stubs.klm_pos_venda`).
        """
        if monitored_process.matter_id is None:
            return
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == monitored_process.matter_id,
                Matter.workspace_id == monitored_process.workspace_id,
            )
        )
        if matter is None:
            return
        thread = await self.db.scalar(
            select(OmnichannelThread)
            .where(
                OmnichannelThread.workspace_id == monitored_process.workspace_id,
                OmnichannelThread.client_id == matter.client_id,
            )
            .order_by(OmnichannelThread.updated_at.desc())
            .limit(1)
        )
        if thread is None:
            return

        try:
            resultado = await run_pos_venda(
                monitored_process.workspace_id,
                evento_tipo=evento_tipo,
                evento_resumo={
                    "numero_cnj": publication.numero_cnj,
                    "tipo": publication.tipo,
                    "urgencia": publication.urgencia,
                    "publication_id": str(publication.id),
                },
                dentro_da_janela_24h=True,
            )
        except ValueError as error:  # evento_tipo desconhecido — nunca derruba a ingestão
            logger.warning(
                "process_monitoring.pos_venda_intent_skipped",
                workspace_id=str(monitored_process.workspace_id),
                monitored_process_id=str(monitored_process.id),
                error_type=safe_error_type(error),
            )
            return

        if resultado.get("agrupar_resumo_periodico"):
            # Movimentação trivial: nunca vira mensagem individual — fica só
            # para o resumo periódico da régua de comunicação.
            return

        self.db.add(
            KlmAuditEvent(
                workspace_id=monitored_process.workspace_id,
                actor_type="agent",
                actor_label="Pós-venda / Sucesso do Cliente (IA)",
                event_type="pos_venda_intent_queued",
                entity_type="process_publication",
                entity_id=publication.id,
                correlation_id=str(thread.id),
                summary=(
                    f"Intenção de comunicação de pós-venda preparada para o caso "
                    f"{matter.id} (evento: {evento_tipo}, status: {resultado.get('status')})."
                ),
                before_data={"eventoTipo": evento_tipo, "publicationId": str(publication.id)},
                after_data={
                    "status": resultado.get("status"),
                    "rascunhoMensagem": resultado.get("rascunho_mensagem"),
                    "usarTemplate": resultado.get("usar_template"),
                    "templateSugerido": resultado.get("template_sugerido"),
                    "requerAprovacaoAdvogado": resultado.get("requer_aprovacao_advogado"),
                    "requiresHumanApproval": resultado.get("requires_human_approval"),
                    "registroSimulado": resultado.get("registro_simulado"),
                    "threadId": str(thread.id),
                    "clientId": str(matter.client_id),
                },
            )
        )
        await self.db.flush()

    async def list_publications(
        self,
        workspace_id: UUID,
        *,
        urgencia: Optional[str] = None,
        prazo_status: Optional[DeadlineStatus] = None,
        monitored_process_id: Optional[UUID] = None,
    ) -> list[ProcessPublication]:
        await self._require_module_contracted(workspace_id)
        statement = select(ProcessPublication).where(
            ProcessPublication.workspace_id == workspace_id
        )
        if urgencia is not None:
            statement = statement.where(ProcessPublication.urgencia == urgencia)
        if prazo_status is not None:
            statement = statement.where(ProcessPublication.prazo_status == prazo_status)
        if monitored_process_id is not None:
            statement = statement.where(
                ProcessPublication.monitored_process_id == monitored_process_id
            )
        statement = statement.order_by(ProcessPublication.published_at.desc())
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_deadlines(
        self,
        workspace_id: UUID,
        *,
        prazo_status: Optional[DeadlineStatus] = None,
    ) -> list[ProcessPublication]:
        """Agenda de prazos do módulo (`GET /processos/prazos`) — só as
        publicações que carregam prazo calculado (`dias` não nulo), num
        único feed ordenado por data fatal (mais urgente primeiro). A
        página `/app/prazos` (PrazosPage) usa `status=prazo_sugerido` para
        'Aguardando confirmação' e `status=confirmado` para 'Confirmados';
        sem filtro, devolve os três estados juntos."""
        await self._require_module_contracted(workspace_id)
        statement = select(ProcessPublication).where(
            ProcessPublication.workspace_id == workspace_id,
            ProcessPublication.dias.is_not(None),
        )
        if prazo_status is not None:
            statement = statement.where(ProcessPublication.prazo_status == prazo_status)
        statement = statement.order_by(
            ProcessPublication.data_fatal.asc().nullslast(),
            ProcessPublication.published_at.desc(),
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    # ── Confirmação humana do prazo (nunca automática) ────────────────────

    async def _get_publication(
        self, workspace_id: UUID, publication_id: UUID
    ) -> ProcessPublication:
        publication = await self.db.scalar(
            select(ProcessPublication).where(
                ProcessPublication.id == publication_id,
                ProcessPublication.workspace_id == workspace_id,
            )
        )
        if publication is None:
            raise ProcessMonitoringNotFoundError(
                f"Publicação {publication_id} não encontrada neste workspace."
            )
        return publication

    async def confirm_deadline(
        self,
        workspace_id: UUID,
        publication_id: UUID,
        *,
        user_id: UUID,
    ) -> ProcessPublication:
        return await self._resolve_deadline(
            workspace_id, publication_id, user_id=user_id, target=DeadlineStatus.confirmado
        )

    async def dismiss_deadline(
        self,
        workspace_id: UUID,
        publication_id: UUID,
        *,
        user_id: UUID,
    ) -> ProcessPublication:
        return await self._resolve_deadline(
            workspace_id, publication_id, user_id=user_id, target=DeadlineStatus.dispensado
        )

    async def _resolve_deadline(
        self,
        workspace_id: UUID,
        publication_id: UUID,
        *,
        user_id: UUID,
        target: DeadlineStatus,
    ) -> ProcessPublication:
        await self._require_module_contracted(workspace_id)
        publication = await self._get_publication(workspace_id, publication_id)
        if publication.dias is None or publication.prazo_status is None:
            raise ProcessMonitoringDeadlineStateError(
                "Esta publicação não tem prazo calculado para confirmar ou dispensar."
            )
        if publication.prazo_status == target:
            return publication  # replay idempotente — exactly-once
        if publication.prazo_status != DeadlineStatus.prazo_sugerido:
            raise ProcessMonitoringDeadlineStateError(
                f"Prazo já está '{publication.prazo_status.value}' — não pode virar "
                f"'{target.value}'."
            )

        actor_label = await resolve_human_actor_label(self.db, user_id)
        before_status = publication.prazo_status.value
        publication.prazo_status = target
        publication.confirmed_by_user_id = user_id
        publication.confirmed_at = datetime.now(UTC)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type=(
                    "process_publication_deadline_confirmed"
                    if target == DeadlineStatus.confirmado
                    else "process_publication_deadline_dismissed"
                ),
                entity_type="process_publication",
                entity_id=publication.id,
                summary=(
                    f"Prazo da publicação {publication.id} ({publication.numero_cnj}) "
                    f"passou de '{before_status}' para '{target.value}' — "
                    f"data fatal sugerida {publication.data_fatal.isoformat() if publication.data_fatal else '—'}."
                ),
                before_data={"prazoStatus": before_status},
                after_data={
                    "prazoStatus": target.value,
                    "dataFatal": publication.data_fatal.isoformat()
                    if publication.data_fatal
                    else None,
                },
            )
        )
        await self.db.flush()

        if target == DeadlineStatus.confirmado and publication.monitored_process_id is not None:
            monitored_process = await self.db.scalar(
                select(MonitoredProcess).where(
                    MonitoredProcess.id == publication.monitored_process_id,
                    MonitoredProcess.workspace_id == workspace_id,
                )
            )
            if monitored_process is not None:
                await self._enqueue_pos_venda_intent(
                    monitored_process, publication, evento_tipo="prazo_confirmado"
                )

        return publication


__all__ = [
    "ProcessMonitoringService",
    "ProcessMonitoringError",
    "ProcessMonitoringModuleNotContractedError",
    "ProcessMonitoringInvalidCnjError",
    "ProcessMonitoringInvalidOabError",
    "ProcessMonitoringDuplicateProcessError",
    "ProcessMonitoringNotFoundError",
    "ProcessMonitoringDeadlineStateError",
    "CurrentStockLimitExceededError",
]
