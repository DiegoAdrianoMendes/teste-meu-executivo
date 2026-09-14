"""
Middleware que captura exceções HTTP 500 e as registra no error_tracking.
"""
import uuid
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import AsyncSessionLocal


class ErrorTrackingMiddleware(BaseHTTPMiddleware):
    """Intercepta respostas 5xx e persiste no error_logs."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            # Capturar exceção não tratada
            user_id = None
            workspace_id = None
            route = request.scope.get("route")
            endpoint = getattr(route, "path", None) or "unresolved"

            if settings.uses_local_infrastructure:
                logger.error(
                    "error_tracking.local_exception",
                    endpoint=endpoint,
                    method=request.method,
                    request_id=request_id,
                    error_type=safe_error_type(exc),
                )
                raise

            try:
                async with AsyncSessionLocal() as db:
                    from app.services.error_tracking_service import ErrorTrackingService
                    await ErrorTrackingService.capture(
                        db=db,
                        exception=exc,
                        endpoint=endpoint,
                        method=request.method,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        request_id=request_id,
                        status_code=500,
                    )
            except Exception as tracking_exc:
                logger.error(
                    "error_tracking.failed",
                    error_type=safe_error_type(tracking_exc),
                )

            raise
