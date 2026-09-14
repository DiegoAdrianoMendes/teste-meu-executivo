"""performance: composite indexes, pg_trgm, GIN for text search

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-05

Fixes críticos de performance identificados na auditoria:
1. pg_trgm: busca de texto substring sem full table scan
2. Índices compostos em matters (workspace_id + column_id, micro_status, updated_at)
3. GIN index em matters.assigned_agent_ids (JSONB contains query)
4. Índices compostos em conversations, finance_entries, agent_runs, notifications
5. Índice em matters.claim_value para filtros de range
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Habilitar pg_trgm para busca substring eficiente ────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── 2. Índices GIN para busca de texto (ILIKE "%x%" → trigram index) ──
    # clients.name
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_clients_name_trgm
        ON clients USING GIN(name gin_trgm_ops)
    """)
    # matters.demand_type
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_demand_type_trgm
        ON matters USING GIN(demand_type gin_trgm_ops)
    """)
    # matters.executive_summary
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_executive_summary_trgm
        ON matters USING GIN(executive_summary gin_trgm_ops)
    """)
    # conversation_insights.summary
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_conversation_insights_summary_trgm
        ON conversation_insights USING GIN(summary gin_trgm_ops)
    """)
    # clients.phone (busca por número)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_clients_phone_trgm
        ON clients USING GIN(phone gin_trgm_ops)
    """)

    # ── 3. GIN index no JSONB assigned_agent_ids ──────────────────────────
    # Usar jsonb_path_ops (compatível com JSON e JSONB) ou ignorar se for JSON puro
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_matters_assigned_agent_ids_gin'
            ) THEN
                BEGIN
                    EXECUTE 'CREATE INDEX ix_matters_assigned_agent_ids_gin
                             ON matters USING GIN((assigned_agent_ids::jsonb) jsonb_path_ops)';
                EXCEPTION WHEN OTHERS THEN
                    NULL; -- ignora se coluna não existir ou não for conversível
                END;
            END IF;
        END$$;
    """)

    # ── 4. Índices compostos em matters ───────────────────────────────────
    # Kanban board: filtrar por coluna dentro do workspace
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_workspace_column
        ON matters(workspace_id, column_id)
        WHERE deleted_at IS NULL
    """)
    # Filtro por micro_status
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_workspace_microstatus
        ON matters(workspace_id, micro_status)
        WHERE deleted_at IS NULL
    """)
    # ORDER BY updated_at DESC (query principal do kanban)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_workspace_updated
        ON matters(workspace_id, updated_at DESC)
        WHERE deleted_at IS NULL
    """)
    # Filtro por priority
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_workspace_priority
        ON matters(workspace_id, priority)
        WHERE deleted_at IS NULL
    """)
    # claim_value para filtros de range (min/max)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_claim_value
        ON matters(claim_value)
        WHERE deleted_at IS NULL AND claim_value IS NOT NULL
    """)
    # responsible_human_id (filtro "meus casos")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_workspace_responsible
        ON matters(workspace_id, responsible_human_id)
        WHERE deleted_at IS NULL AND responsible_human_id IS NOT NULL
    """)

    # ── 5. Índices compostos em conversations ─────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_conversations_matter_internal_ts
        ON conversations(matter_id, is_internal, timestamp DESC)
    """)

    # ── 6. Índices compostos em finance_entries ───────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_finance_entries_workspace_status_due
        ON finance_entries(workspace_id, status, due_date)
        WHERE deleted_at IS NULL
    """)

    # ── 7. Índices compostos em agent_runs ────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_runs_workspace_matter_status
        ON agent_runs(workspace_id, matter_id, status)
    """)

    # ── 8. Índice composto em notifications ───────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_notifications_user_read_ts
        ON notifications(user_id, is_read, timestamp DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_notifications_workspace_user
        ON notifications(workspace_id, user_id)
        WHERE workspace_id IS NOT NULL
    """)

    # ── 9. Índices em whatsapp_contacts ───────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_wa_contacts_workspace_matter
        ON whatsapp_contacts(workspace_id, matter_id)
        WHERE matter_id IS NOT NULL
    """)

    # ── 10. Índice em knowledge_chunks para RAG ───────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_workspace_matter_updated
        ON knowledge_chunks(workspace_id, matter_id, updated_at DESC)
    """)

    # ── 11. Índice em timeline_events para histórico ──────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_timeline_events_matter_ts
        ON timeline_events(matter_id, timestamp DESC)
    """)

    # ── 12. Índice em flow_runs ───────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_flow_runs_workspace_status
        ON flow_runs(workspace_id, status)
        WHERE status IN ('active', 'waiting_human')
    """)

    # ── 13. Índice em scheduled_messages ─────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_scheduled_messages_status_scheduled_at
        ON scheduled_messages(status, scheduled_at)
        WHERE status = 'pending'
    """)


def downgrade() -> None:
    indexes = [
        'ix_clients_name_trgm',
        'ix_matters_demand_type_trgm',
        'ix_matters_executive_summary_trgm',
        'ix_conversation_insights_summary_trgm',
        'ix_clients_phone_trgm',
        'ix_matters_assigned_agent_ids_gin',
        'ix_matters_workspace_column',
        'ix_matters_workspace_microstatus',
        'ix_matters_workspace_updated',
        'ix_matters_workspace_priority',
        'ix_matters_claim_value',
        'ix_matters_workspace_responsible',
        'ix_conversations_matter_internal_ts',
        'ix_finance_entries_workspace_status_due',
        'ix_agent_runs_workspace_matter_status',
        'ix_notifications_user_read_ts',
        'ix_notifications_workspace_user',
        'ix_wa_contacts_workspace_matter',
        'ix_knowledge_chunks_workspace_matter_updated',
        'ix_timeline_events_matter_ts',
        'ix_flow_runs_workspace_status',
        'ix_scheduled_messages_status_scheduled_at',
    ]
    for idx in indexes:
        op.execute(f"DROP INDEX IF EXISTS {idx} CASCADE")
