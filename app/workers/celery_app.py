"""
Configuração do Celery para execução assíncrona de agentes.
"""
import importlib

import httpx
import redis.exceptions
from celery import Celery, Task
from celery.schedules import crontab
from celery.signals import setup_logging as celery_setup_logging
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy.exc import InterfaceError

from app.core.config import settings
from app.core.logging import setup_logging

# S14: os builtins cobrem ConnectionRefusedError e asyncio.TimeoutError, mas não o
# que a stack levanta de fato: httpx (fase de conexão, antes de o corpo sair; nunca
# ReadTimeout, que pode ter sido processado), redis, broker (kombu) e a queda de
# conexão do SQLAlchemy. Nunca DBAPIError genérico (pegaria IntegrityError).
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    redis.exceptions.ConnectionError,
    redis.exceptions.TimeoutError,
    KombuOperationalError,
    InterfaceError,
)


class KLMResilientTask(Task):
    """Defaults conservadores para falhas transitórias internas.

    Tasks que já possuem fluxo de retry próprio continuam podendo sobrescrever
    estes valores. O retry automático fica restrito a erros de transporte ou
    timeout, evitando repetir validações de negócio ou efeitos externos não
    idempotentes.
    """

    autoretry_for = TRANSIENT_ERRORS
    max_retries = 3
    retry_backoff = True
    retry_backoff_max = 60
    retry_jitter = True
    default_retry_delay = 5
    soft_time_limit = 55
    time_limit = 60


celery_app = Celery(
    "klm_legal_hub",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.agent_tasks"],
    task_cls=KLMResilientTask,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    worker_max_tasks_per_child=200,
    task_soft_time_limit=55,
    task_time_limit=60,
)

celery_app.autodiscover_tasks(["app.workers"])


@celery_setup_logging.connect
def _configure_worker_logging(**_kwargs) -> None:
    """S12: conectar o sinal `setup_logging` impede o Celery de sequestrar o root
    logger no boot do worker e do beat; sem isso o JSON do structlog virava um
    `%(message)s` prefixado no CloudWatch."""
    setup_logging()
importlib.import_module("app.workers.ads_feedback_tasks")
importlib.import_module("app.workers.agent_tasks")
importlib.import_module("app.workers.auth_revocation_tasks")
importlib.import_module("app.workers.blog_publication_tasks")
importlib.import_module("app.workers.conversation_auditor_tasks")
importlib.import_module("app.workers.finance_export_tasks")
importlib.import_module("app.workers.finance_tasks")
importlib.import_module("app.workers.flow_tasks")
importlib.import_module("app.workers.follow_up_tasks")
importlib.import_module("app.workers.funnel_automation_tasks")
importlib.import_module("app.workers.lead_hydration_tasks")
importlib.import_module("app.workers.meta_oauth_tasks")
importlib.import_module("app.workers.meeting_notification_tasks")
importlib.import_module("app.workers.meta_webhook_tasks")
importlib.import_module("app.workers.notification_tasks")
importlib.import_module("app.workers.native_outbox_tasks")
importlib.import_module("app.workers.omnichannel_action_tasks")
importlib.import_module("app.workers.omnichannel_scheduled_tasks")
importlib.import_module("app.workers.platform_bulk_tasks")
importlib.import_module("app.workers.platform_export_tasks")
importlib.import_module("app.workers.platform_official_whatsapp_tasks")
importlib.import_module("app.workers.platform_restoration_tasks")
importlib.import_module("app.workers.process_monitoring_tasks")
importlib.import_module("app.workers.staff_reassignment_tasks")
importlib.import_module("app.workers.storage_tasks")
importlib.import_module("app.workers.support_notification_tasks")
importlib.import_module("app.workers.support_transcript_tasks")
importlib.import_module("app.workers.support_lifecycle_tasks")
importlib.import_module("app.workers.task_reminder_tasks")
importlib.import_module("app.workers.whatsapp_campaign_tasks")
importlib.import_module("app.workers.whatsapp_inbound_tasks")
importlib.import_module("app.workers.whatsapp_utility_template_tasks")
importlib.import_module("app.workers.whatsapp_webhook_tasks")

# Beat schedule: tarefas periódicas
celery_app.conf.beat_schedule = {
    "publish-due-blog-posts-every-minute": {
        "task": "blog_publication_tasks.publish_due",
        "schedule": crontab(minute="*"),
    },
    "recover-platform-bulk-jobs-every-minute": {
        "task": "platform_bulk_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "recover-platform-exports-every-minute": {
        "task": "platform_export_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "recover-finance-exports-every-minute": {
        "task": "finance_export_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "recover-platform-official-whatsapp-every-minute": {
        "task": "platform_official_whatsapp_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "recover-platform-restorations-every-minute": {
        "task": "platform_restoration_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "retry-auth-revocations-every-minute": {
        "task": "auth_revocation_tasks.retry_pending",
        "schedule": crontab(minute="*"),
    },
    "purge-expired-identity-outbox-every-five-minutes": {
        "task": "auth_revocation_tasks.purge_expired_outbox",
        "schedule": crontab(minute="*/5"),
    },
    "deliver-identity-outbox-every-minute": {
        "task": "auth_revocation_tasks.deliver_identity_outbox",
        "schedule": crontab(minute="*"),
    },
    "recover-agent-runs-every-minute": {
        "task": "app.workers.recover_agent_runs",
        "schedule": crontab(minute="*"),
    },
    "finance-recurrence-tick-hourly": {
        "task": "finance_tasks.finance_recurrence_tick",
        "schedule": crontab(minute="0"),
    },
    "finance-dunning-tick-daily": {
        "task": "finance_tasks.dunning_tick",
        "schedule": crontab(hour="8", minute="0"),
    },
    "finance-tax-reform-alerts-daily": {
        "task": "finance_tasks.tax_reform_alert_tick",
        "schedule": crontab(hour="8", minute="10"),
    },
    "expire-finance-exports-hourly": {
        "task": "finance_export_tasks.expire",
        "schedule": crontab(minute="15"),
    },
    "recover-flow-step-dispatches-every-minute": {
        "task": "flow_tasks.recover_step_dispatches",
        "schedule": crontab(minute="*"),
    },
    # Dispara mensagens agendadas a cada minuto
    "process-scheduled-messages-every-minute": {
        "task": "flow_tasks.process_scheduled_messages",
        "schedule": crontab(minute="*"),
    },
    "process-human-escalations-every-minute": {
        "task": "flow_tasks.process_human_escalations",
        "schedule": crontab(minute="*"),
    },
    "process-native-omnichannel-schedules-every-minute": {
        "task": "omnichannel_scheduled_tasks.process_due",
        "schedule": crontab(minute="*"),
    },
    "process-native-social-actions-every-minute": {
        "task": "omnichannel_action_tasks.process_due",
        "schedule": crontab(minute="*"),
    },
    "process-lead-hydrations-every-minute": {
        "task": "lead_hydration_tasks.process_due",
        "schedule": crontab(minute="*"),
    },
    "process-ads-conversion-feedback-every-minute": {
        "task": "ads_feedback_tasks.process_due",
        "schedule": crontab(minute="*"),
    },
    "process-whatsapp-campaigns-every-minute": {
        "task": "whatsapp_campaign_tasks.process_due_campaigns",
        "schedule": crontab(minute="*"),
    },
    "sweep-orphan-whatsapp-campaign-claims-every-ten-minutes": {
        "task": "whatsapp_campaign_tasks.sweep_orphan_campaign_claims",
        "schedule": crontab(minute="*/10"),
    },
    "reconcile-whatsapp-inbound-media-every-minute": {
        "task": "whatsapp_inbound_tasks.reconcile_media",
        "schedule": crontab(minute="*"),
    },
    "recover-whatsapp-webhooks-every-minute": {
        "task": "whatsapp_webhook_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "reconcile-local-storage-every-minute": {
        "task": "storage_tasks.reconcile_local_objects",
        "schedule": crontab(minute="*"),
    },
    "dispatch-private-document-scans-every-minute": {
        "task": "storage_tasks.dispatch_private_document_scans",
        "schedule": crontab(minute="*"),
    },
    "cleanup-abandoned-private-uploads-every-fifteen-minutes": {
        "task": "storage_tasks.cleanup_abandoned_private_uploads",
        "schedule": crontab(minute="*/15"),
    },
    "dispatch-generated-document-deliveries-every-minute": {
        "task": "storage_tasks.dispatch_generated_document_deliveries",
        "schedule": crontab(minute="*"),
    },
    "deliver-support-notifications-every-minute": {
        "task": "support_notification_tasks.process_due",
        "schedule": crontab(minute="*"),
    },
    "recover-meeting-notifications-every-minute": {
        "task": "meeting_notification_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "recover-task-reminders-every-minute": {
        "task": "task_reminder_tasks.recover",
        "schedule": crontab(minute="*"),
    },
    "deliver-support-transcript-emails-every-minute": {
        "task": "support_transcript_tasks.process_due",
        "schedule": crontab(minute="*"),
    },
    "process-support-lifecycle-every-five-minutes": {
        "task": "support_lifecycle_tasks.process_due",
        "schedule": crontab(minute="*/5"),
    },
    "restore-expired-staff-reassignments-every-five-minutes": {
        "task": "staff_reassignment_tasks.dispatch_due",
        "schedule": crontab(minute="*/5"),
    },
    "recover-native-outbox-every-minute": {
        "task": "native_outbox_tasks.recover_active_workspaces",
        "schedule": crontab(minute="*"),
    },
    "audit-active-conversations-every-minute": {
        "task": "conversation_auditor_tasks.recover_active_workspaces",
        "schedule": crontab(minute="*"),
    },
    "send-meeting-reminders-every-5-minutes": {
        "task": "notification_tasks.send_meeting_reminders",
        "schedule": crontab(minute="*/5"),
    },
    # Onda 1 dormentes item 2 — fila de follow-up sem disparo proativo;
    # mesmo padrão de send-meeting-reminders-every-5-minutes acima.
    "process-due-follow-ups-every-5-minutes": {
        "task": "follow_up_tasks.process_due",
        "schedule": crontab(minute="*/5"),
    },
    "send-daily-digest-at-7am": {
        "task": "notification_tasks.send_daily_digest",
        "schedule": crontab(minute=0, hour=7),
    },
    "refresh-instagram-oauth-tokens-hourly": {
        "task": "meta_oauth_tasks.refresh_due_tokens",
        "schedule": crontab(minute=0),
    },
    # Módulo processual contratável à parte — 1x/dia; cada workspace é
    # despachado com jitter próprio (ver process_monitoring_tasks.py) para
    # não gerar um pico simultâneo contra o DJEN/DataJud.
    "djen-poll-tick-daily": {
        "task": "process_monitoring_tasks.djen_poll_tick",
        "schedule": crontab(hour=3, minute=0),
    },
    "datajud-poll-tick-daily": {
        "task": "process_monitoring_tasks.datajud_poll_tick",
        "schedule": crontab(hour=4, minute=0),
    },
}

# Dormente #9 — estado por task individual do beat. Registro CENTRALIZADO
# via signal do Celery: nenhuma das tasks acima é alterada, e uma falha
# silenciosa de qualquer uma delas passa a aparecer em GET /status/tasks.
from app.workers.periodic_task_tracking import (  # noqa: E402
    register_periodic_task_tracking,
)

register_periodic_task_tracking()

# Env vars aposentadas continuam sendo aceitas em silêncio por causa de
# extra="ignore" em Settings. O worker é onde elas mais doem, porque é ele que
# roda o beat: avisa aqui também, não só na API.
from app.core.klm_runtime import warn_retired_flags  # noqa: E402

warn_retired_flags()
