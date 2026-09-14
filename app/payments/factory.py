"""Resolve o gateway de pagamentos correto para um workspace.

A credencial vive no `IntegrationConnection` existente (provider `asaas`,
chave cifrada via Fernet em `settings["apiTokenEncrypted"]` — mesmo molde de
`app/services/integration_secrets.py`). Sem credencial configurada, o
factory levanta um erro claro em vez de silenciosamente usar o fake.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.payments.asaas_adapter import AsaasAdapter
from app.payments.port import PaymentGatewayPort
from app.services.integration_secrets import reveal_integration_secret

ASAAS_ACCESS_TOKEN_KEY = "apiToken"


class GatewayCredentialError(RuntimeError):
    """Nenhuma credencial Asaas utilizável foi encontrada para o workspace."""


async def _load_asaas_connection(
    db: AsyncSession, workspace_id: UUID
) -> IntegrationConnection | None:
    result = await db.execute(
        select(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.asaas,
        )
        .order_by(IntegrationConnection.created_at.desc())
    )
    connections = result.scalars().all()
    for connection in connections:
        if connection.status == IntegrationStatus.connected:
            return connection
    return connections[0] if connections else None


async def get_gateway_for_workspace(
    db: AsyncSession, workspace_id: UUID
) -> PaymentGatewayPort:
    """Devolve um `AsaasAdapter` configurado com a credencial do workspace.

    Levanta `GatewayCredentialError` se não houver `IntegrationConnection`
    Asaas cadastrada ou se a chave não puder ser decifrada.
    """

    connection = await _load_asaas_connection(db, workspace_id)
    if connection is None:
        raise GatewayCredentialError(
            f"Workspace {workspace_id} não tem integração Asaas configurada."
        )

    access_token = reveal_integration_secret(connection.settings, ASAAS_ACCESS_TOKEN_KEY)
    if not access_token:
        raise GatewayCredentialError(
            f"Integração Asaas do workspace {workspace_id} está sem chave de acesso válida."
        )

    sandbox = bool(connection.settings.get("sandbox", True)) if connection.settings else True
    return AsaasAdapter(
        access_token=access_token,
        sandbox=sandbox,
        base_url=connection.base_url or None,
    )
