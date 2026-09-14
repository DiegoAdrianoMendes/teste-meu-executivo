from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.client import ClientCreate, ClientUpdate
from app.schemas.flow import ScheduledMessageCreate
from app.schemas.omnichannel_v6 import LeadCaptureIngest
from app.schemas.public_api import PublicApiLeadCreate, PublicApiTemplateSendRequest
from app.schemas.sdr import SDRQualification


@pytest.mark.parametrize(
    ("factory", "attribute"),
    [
        (lambda: ClientCreate(name="Maria", phone="(61) 99999-9999"), "phone"),
        (lambda: ClientUpdate(phone="+55 61 99999-9999"), "phone"),
        (
            lambda: PublicApiLeadCreate(
                name="Maria",
                phone="61 99999-9999",
                demand_type="Atendimento",
            ),
            "phone",
        ),
        (
            lambda: PublicApiTemplateSendRequest(
                meta_template_id="11111111-1111-4111-8111-111111111111",
                phone="0055 61 99999-9999",
            ),
            "phone",
        ),
        (
            lambda: ScheduledMessageCreate(
                contact_phone="(61) 99999-9999",
                message="Mensagem de teste",
                scheduled_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            "contact_phone",
        ),
        (
            lambda: LeadCaptureIngest(
                provider="site",
                connection_key="site-main",
                external_lead_id="lead-1",
                name="Maria",
                phone="(61) 99999-9999",
            ),
            "phone",
        ),
        (lambda: SDRQualification(phone="61 99999-9999"), "phone"),
    ],
)
def test_meta_phone_entrypoints_store_canonical_number(factory, attribute):
    assert getattr(factory(), attribute) == "5561999999999"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ClientCreate(name="Maria", phone="123"),
        lambda: PublicApiLeadCreate(name="Maria", phone="123", demand_type="Atendimento"),
        lambda: ScheduledMessageCreate(
            contact_phone="123",
            message="Mensagem de teste",
            scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        lambda: LeadCaptureIngest(
            provider="site",
            connection_key="site-main",
            external_lead_id="lead-1",
            name="Maria",
            phone="123",
        ),
        lambda: SDRQualification(phone="123"),
    ],
)
def test_meta_phone_entrypoints_reject_invalid_number(factory):
    with pytest.raises(ValidationError):
        factory()
