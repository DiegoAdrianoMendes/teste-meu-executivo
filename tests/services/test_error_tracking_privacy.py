from __future__ import annotations

import pytest

from app.services.error_tracking_service import ErrorTrackingService


@pytest.mark.asyncio
async def test_error_tracking_persists_only_redacted_exception_metadata(db_session):
    sensitive_text = "CPF 123.456.789-00 email ana@example.test token sk-sensitive"
    try:
        raise ValueError(sensitive_text)
    except ValueError as error:
        tracked = await ErrorTrackingService.capture(
            db=db_session,
            exception=error,
            endpoint="/clients/{client_id}",
            method="POST",
            request_id="request-123",
            extra={"payload": sensitive_text},
            status_code=500,
        )

    assert tracked.exception_type == "ValueError"
    assert tracked.message == "ValueError: details redacted"
    assert tracked.traceback is None
    assert tracked.extra == {"provided": True}
    assert sensitive_text not in "|".join(
        str(value)
        for value in (
            tracked.exception_type,
            tracked.message,
            tracked.traceback,
            tracked.location,
            tracked.extra,
        )
    )
