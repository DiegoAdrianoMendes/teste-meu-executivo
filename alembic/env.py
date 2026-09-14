"""
Alembic env.py — usa psycopg2 (síncrono) para migrations.
O FastAPI usa asyncpg em runtime, mas o Alembic roda de forma síncrona.
A DATABASE_URL deve ser no formato: postgresql://user:pass@host:port/db
"""

import os
import re
from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401
from alembic import context
from app.db.alembic_metadata import align_published_index_names

# Importar todos os modelos para o Alembic detectar as tabelas
from app.db.base import Base, FlexibleJSON  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
align_published_index_names(target_metadata)

# A migration inicial publicada criou estes enums inline, mas seu downgrade
# histórico remove apenas as tabelas. Não alteramos a revision já distribuída:
# quando Alembic chega de fato a ``base``, o ambiente remove exclusivamente os
# tipos conhecidos daquela revision para que um round-trip base -> head seja
# reprodutível. Tipos externos ou futuros nunca entram nesta limpeza.
INITIAL_SCHEMA_ENUM_TYPES = (
    "timeline_event_type_enum",
    "task_priority_enum",
    "task_status_enum",
    "notification_type_enum",
    "document_status_enum",
    "document_type_enum",
    "approval_status_enum",
    "approval_priority_enum",
    "client_classification_enum",
    "urgency_enum",
    "priority_enum",
    "micro_status_enum",
    "kanban_column_id_enum",
    "practice_area_enum",
    "user_role_enum",
    "agent_type_enum",
)

# Índices parciais, GIN e de expressão vivem nas migrations porque carregam
# detalhes PostgreSQL que não devem contaminar os modelos portáveis do SQLite.
MIGRATION_MANAGED_INDEXES = frozenset(
    {
        "ix_agent_runs_workspace_matter_status",
        "ix_clients_name_trgm",
        "ix_clients_phone_trgm",
        "ix_executive_messages_thread_sequence",
        "ix_executive_reports_workspace_period",
        "ix_executive_threads_workspace_status_activity",
        "ix_conversation_insights_close_intent",
        "ix_conversation_insights_closed_signal",
        "ix_conversation_insights_summary_trgm",
        "ix_conversations_matter_internal_ts",
        "ix_finance_entries_workspace_status_due",
        "ix_flow_runs_flow_id",
        "ix_flow_runs_matter_id",
        "ix_flow_runs_status",
        "ix_flow_runs_workspace_id",
        "ix_flow_runs_workspace_status",
        "ix_flows_is_template",
        "ix_flows_practice_area",
        "ix_flows_workspace_id",
        "ix_knowledge_chunks_workspace_matter_updated",
        "ix_knowledge_chunks_scope_source_ref",
        "ix_knowledge_chunks_search_document_gin",
        "ix_klm_action_intent_workspace_delivery_due",
        "ix_klm_legal_artifact_version_protocolability_state",
        "ix_klm_legal_artifact_workspace_matter_kind",
        "ix_klm_meta_groups_last_lifecycle_at",
        "ix_klm_process_workspace_deadline",
        "ix_klm_process_workspace_matter",
        "ix_klm_process_workspace_status",
        "ix_legal_demand_sla_effective_at",
        "ix_matters_assigned_agent_ids_gin",
        "ix_matters_claim_value",
        "ix_matters_demand_type_trgm",
        "ix_matters_executive_summary_trgm",
        "ix_matters_sdr_qualification",
        "ix_matters_workspace_column",
        "ix_matters_workspace_health_subtheme_custom",
        "ix_matters_workspace_legal_route",
        "ix_matters_workspace_microstatus",
        "ix_matters_workspace_priority",
        "ix_matters_workspace_responsible",
        "ix_matters_workspace_updated",
        "ix_notifications_user_read_ts",
        "ix_notifications_workspace_user",
        "ix_monitored_process_responsible_user_id",
        "ix_platform_bulk_jobs_resource_created",
        "ix_platform_bulk_previews_resource_created",
        "ix_scheduled_messages_matter_id",
        "ix_scheduled_messages_status_scheduled_at",
        "ix_scheduled_messages_workspace_id",
        "ix_timeline_events_matter_ts",
        "ix_users_platform_access_package",
        "ix_wa_contacts_workspace_matter",
        "ix_workspace_memberships_access_package",
    }
)

# PostgreSQL-only search projection created and maintained by migration
# ``f4a6c8e0b209``.  It uses a stored ``tsvector`` expression and a composite
# tenant FK that are deliberately absent from the portable SQLite ORM model.
MIGRATION_MANAGED_COLUMNS = frozenset({("knowledge_chunks", "search_document")})
MIGRATION_MANAGED_FOREIGN_KEYS = frozenset(
    {("knowledge_chunks", "fk_knowledge_chunks_matter_workspace")}
)


def include_schema_object(obj, name, type_, reflected, compare_to) -> bool:
    """Preserva índices especializados mantidos explicitamente por migration."""
    if type_ == "index" and reflected and compare_to is None and name in MIGRATION_MANAGED_INDEXES:
        return False
    if (
        type_ == "column"
        and reflected
        and compare_to is None
        and (obj.table.name, name) in MIGRATION_MANAGED_COLUMNS
    ):
        return False
    if (
        type_ == "foreign_key_constraint"
        and reflected
        and compare_to is None
        and (obj.table.name, name) in MIGRATION_MANAGED_FOREIGN_KEYS
    ):
        return False
    return True


def compare_column_type(
    migration_context,
    inspected_column,
    metadata_column,
    inspected_type,
    metadata_type,
):
    """Evita drift falso para tipos portáveis com representação equivalente."""
    if isinstance(metadata_type, FlexibleJSON) and isinstance(inspected_type, sa.JSON):
        return False
    if (
        isinstance(metadata_type, sa.Enum)
        and not metadata_type.native_enum
        and isinstance(inspected_type, sa.String)
    ):
        return False
    return None


def get_database_url() -> str:
    """
    Retorna a DATABASE_URL síncrona (psycopg2).
    Converte automaticamente de asyncpg para psycopg2 se necessário.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise ValueError("DATABASE_URL não está definida nas variáveis de ambiente")

    # Converter postgresql+asyncpg:// -> postgresql+psycopg2://
    url = re.sub(r"postgresql\+asyncpg://", "postgresql+psycopg2://", url)
    # Converter postgres:// -> postgresql+psycopg2:// (formato Heroku/AWS)
    url = re.sub(r"^postgres://", "postgresql+psycopg2://", url)
    # Se for postgresql:// puro, adicionar driver psycopg2
    url = re.sub(r"^postgresql://", "postgresql+psycopg2://", url)

    return url


def cleanup_initial_schema_enums_at_base(connection) -> None:
    """Remove resíduos conhecidos somente depois de chegar a Alembic base."""
    if connection.dialect.name != "postgresql":
        return
    version_table_exists = connection.scalar(sa.text("SELECT to_regclass('alembic_version')"))
    if version_table_exists:
        remaining_revisions = connection.scalar(sa.text("SELECT count(*) FROM alembic_version"))
        if remaining_revisions:
            return
    preparer = connection.dialect.identifier_preparer
    for enum_name in INITIAL_SCHEMA_ENUM_TYPES:
        connection.execute(sa.text(f"DROP TYPE IF EXISTS {preparer.quote(enum_name)}"))


def run_migrations_offline() -> None:
    """Roda migrations em modo offline (sem conexão real)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=compare_column_type,
        include_object=include_schema_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Roda migrations em modo online (com conexão real ao banco)."""
    url = get_database_url()

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=compare_column_type,
            include_object=include_schema_object,
        )
        with context.begin_transaction():
            context.run_migrations()
            cleanup_initial_schema_enums_at_base(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
