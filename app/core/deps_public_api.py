"""
Dependências de autenticação da API externa oficial.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    SUBSCRIPTION_ACCESS_DENIED_CODE,
    SUBSCRIPTION_ACCESS_DENIED_MESSAGE,
    get_db,
    subscription_access_operation_for_method,
)
from app.core.public_api_policy import MUTABLE_PUBLIC_API_SCOPES
from app.models.public_api import WorkspacePublicApiToken
from app.services.public_api_service import PublicApiService
from app.services.subscription_access_policy import (
    SubscriptionAccessDeniedError,
    SubscriptionAccessPolicy,
)

public_api_bearer = HTTPBearer(auto_error=False)


@dataclass
class PublicApiContext:
    workspace_id: UUID
    token: WorkspacePublicApiToken
    scope: str
    ip_address: str | None
    user_agent: str | None
    request_path: str
    request_method: str
    idempotency_key: str | None = None


async def _require_public_subscription_access(
    request: Request,
    workspace_id: UUID,
    db: AsyncSession,
) -> None:
    operation = subscription_access_operation_for_method(request.method)
    try:
        await SubscriptionAccessPolicy(db).require(workspace_id, operation)
    except SubscriptionAccessDeniedError as error:
        decision = error.decision
        if decision.reason_code == "subscription_missing" and settings.is_development:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": SUBSCRIPTION_ACCESS_DENIED_CODE,
                "message": SUBSCRIPTION_ACCESS_DENIED_MESSAGE,
                "operation": operation.value,
                "state": decision.state.value if decision.state is not None else None,
                "reasonCode": decision.reason_code,
            },
        ) from error


def require_public_api_scope(scope: str) -> Callable[..., PublicApiContext]:
    async def dependency(
        request: Request,
        x_klm_api_key: str | None = Header(default=None, alias="X-KLM-API-Key"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        credentials: HTTPAuthorizationCredentials | None = Depends(public_api_bearer),
        db: AsyncSession = Depends(get_db),
    ) -> PublicApiContext:
        normalized_idempotency_key = (idempotency_key or "").strip()
        if request.method.upper() == "POST" and scope in MUTABLE_PUBLIC_API_SCOPES:
            if not normalized_idempotency_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "PUBAPI-IDEMPOTENCY-REQUIRED",
                        "message": "Informe o header Idempotency-Key para esta mutação.",
                    },
                )
            if len(normalized_idempotency_key) > 160:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "PUBAPI-IDEMPOTENCY-INVALID",
                        "message": "Idempotency-Key deve ter no máximo 160 caracteres.",
                    },
                )
        raw_token = x_klm_api_key or (credentials.credentials if credentials else None)
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "PUBAPI-400",
                    "message": (
                        "Informe X-KLM-API-Key ou Authorization Bearer para usar a API "
                        "externa."
                    ),
                },
            )
        service = PublicApiService(db)
        auth = await service.authenticate(raw_token=raw_token.strip(), required_scope=scope)
        await _require_public_subscription_access(request, auth.token.workspace_id, db)
        return PublicApiContext(
            workspace_id=auth.token.workspace_id,
            token=auth.token,
            scope=auth.scope,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_path=request.url.path,
            request_method=request.method,
            idempotency_key=normalized_idempotency_key or None,
        )

    return dependency
