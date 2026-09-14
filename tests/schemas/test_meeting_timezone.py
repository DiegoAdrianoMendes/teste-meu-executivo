from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.control_plane import MeetingCreate, MeetingUpdate


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            MeetingCreate,
            {"title": "Reunião sem fuso", "startsAt": "2026-07-31T10:00:00"},
        ),
        (
            MeetingUpdate,
            {"expectedVersion": 1, "startsAt": datetime(2026, 7, 31, 10, 0)},
        ),
        (
            MeetingUpdate,
            {"expectedVersion": 1, "endsAt": "2026-07-31T11:00:00"},
        ),
    ],
)
def test_meeting_writes_reject_naive_datetimes(schema, payload) -> None:
    with pytest.raises(ValidationError, match="timezone explícito"):
        schema.model_validate(payload)


def test_meeting_writes_accept_explicit_timezone() -> None:
    created = MeetingCreate.model_validate(
        {
            "title": "Reunião com fuso",
            "startsAt": "2026-07-31T10:00:00-03:00",
            "endsAt": "2026-07-31T11:00:00-03:00",
        }
    )
    assert created.starts_at.utcoffset() is not None
    assert created.ends_at is not None and created.ends_at.utcoffset() is not None
