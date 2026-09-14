"""Narrow tenant bootstrap lookups for authenticated inbound webhooks.

PostgreSQL RLS intentionally fails closed before a workspace context exists.
Only the two tenant-bound identifiers below may cross that bootstrap boundary;
the corresponding SECURITY DEFINER functions return a workspace UUID, never a
tenant row or secret. All subsequent ORM work still runs with RLS enabled.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.matter import Matter
from app.models.whatsapp import WhatsAppConnection
from app.services.integration_secrets import WEBHOOK_SECRET_DIGEST_KEY, webhook_secret_digest


async def resolve_zapsign_webhook_workspace(
    session: AsyncSession,
    *,
    matter_id: UUID,
    document_token: str,
) -> UUID | None:
    """Resolve a tenant only when matter ID and persisted ZapSign token match."""
    if not document_token:
        return None
    if session.get_bind().dialect.name == "postgresql":
        value = await session.scalar(
            text(
                "SELECT public.klm_resolve_zapsign_webhook_workspace(:matter_id, :document_token)"
            ),
            {"matter_id": matter_id, "document_token": document_token},
        )
    else:
        value = await session.scalar(
            select(Matter.workspace_id).where(
                Matter.id == matter_id,
                Matter.zapsign_doc_token == document_token,
                Matter.deleted_at.is_(None),
            )
        )
    return UUID(str(value)) if value is not None else None


async def resolve_whatsapp_webhook_workspace(
    session: AsyncSession,
    *,
    phone_number_id: str,
) -> UUID | None:
    """Resolve the tenant bound to Meta's globally unique phone-number ID."""
    normalized = phone_number_id.strip()
    if not normalized:
        return None
    if session.get_bind().dialect.name == "postgresql":
        value = await session.scalar(
            text("SELECT public.klm_resolve_whatsapp_webhook_workspace(:phone_number_id)"),
            {"phone_number_id": normalized},
        )
    else:
        value = await session.scalar(
            select(WhatsAppConnection.workspace_id).where(
                WhatsAppConnection.phone_number_id == normalized
            )
        )
    return UUID(str(value)) if value is not None else None


async def resolve_whatsapp_template_webhook_workspace(
    session: AsyncSession,
    *,
    waba_id: str,
) -> UUID | None:
    """Resolve the tenant bound to a WABA ID for template-status webhooks.

    `message_template_status_update` / `template_category_update` are
    delivered by Meta at the WABA level (no `phone_number_id` in the
    payload), so `resolve_whatsapp_webhook_workspace` above cannot route them.
    PostgreSQL uses the narrow SECURITY DEFINER resolver installed by the
    current Alembic chain; SQLite keeps the equivalent ORM lookup for local
    tests. The database function returns a workspace only when every active
    connection for the WABA belongs to that same workspace, failing closed
    for ambiguous cross-workspace bindings.
    """
    normalized = waba_id.strip()
    if not normalized:
        return None
    if session.get_bind().dialect.name == "postgresql":
        try:
            value = await session.scalar(
                text("SELECT public.klm_resolve_whatsapp_template_webhook_workspace(:waba_id)"),
                {"waba_id": normalized},
            )
        except DBAPIError as exc:
            raise RuntimeError(
                "O banco PostgreSQL não expõe o resolver de tenant exigido "
                "pela versão atual da aplicação: "
                "public.klm_resolve_whatsapp_template_webhook_workspace(text). "
                "Interrompa o processamento e aplique a cadeia Alembic da "
                "mesma candidata antes de receber webhooks Meta."
            ) from exc
    else:
        value = await session.scalar(
            select(WhatsAppConnection.workspace_id).where(WhatsAppConnection.waba_id == normalized)
        )
    return UUID(str(value)) if value is not None else None


async def resolve_asaas_webhook_workspace(
    session: AsyncSession,
    *,
    token: str,
) -> UUID | None:
    """Resolve o tenant dono do token de webhook do Asaas pelo digest do token.

    O token é definido por nós, por workspace, e fica cifrado em
    `integration_connections.settings`; sob FORCE RLS a varredura sem contexto
    devolvia zero linhas e todo evento respondia 401 (S7). O resolver
    SECURITY DEFINER procura só pelo digest (gravado em claro ao lado do
    cifrado) e devolve NULL quando o mesmo digest aparece em mais de um
    workspace. O segredo completo é conferido depois, já sob RLS, pelo
    endpoint. SQLite mantém o equivalente em ORM para os testes locais."""
    normalized = (token or "").strip()
    if not normalized:
        return None
    digest = webhook_secret_digest(normalized)
    if session.get_bind().dialect.name == "postgresql":
        try:
            value = await session.scalar(
                text("SELECT public.klm_resolve_asaas_webhook_workspace(:token_digest)"),
                {"token_digest": digest},
            )
        except DBAPIError as exc:
            # Nunca virar None: 401 silencioso esconderia banco sem a migração.
            raise RuntimeError(
                "O banco PostgreSQL não expõe o resolver de tenant exigido "
                "pela versão atual da aplicação: "
                "public.klm_resolve_asaas_webhook_workspace(text). "
                "Aplique a cadeia Alembic da mesma candidata antes de receber "
                "webhooks do Asaas."
            ) from exc
    else:
        result = await session.execute(
            select(IntegrationConnection.workspace_id, IntegrationConnection.settings).where(
                IntegrationConnection.provider == IntegrationProvider.asaas,
                IntegrationConnection.status != IntegrationStatus.disconnected,
            )
        )
        candidatos = {
            row.workspace_id
            for row in result.all()
            if (row.settings or {}).get(WEBHOOK_SECRET_DIGEST_KEY) == digest
        }
        value = candidatos.pop() if len(candidatos) == 1 else None
    return UUID(str(value)) if value is not None else None
