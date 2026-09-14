"""F16 — 'Falar com um humano' (escalonamento humano configurável no Blackbox)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

from app.main import app
from app.models.workspace_membership import WorkspaceRole
from app.schemas.support_ticket import SupportEscalationSettings
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.support_ticket_service import SupportTicketService

_SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def test_platform_support_mutations_publish_canonical_snapshot_contracts():
    operations = app.openapi()["paths"]
    mutation_paths = {
        "/api/v1/platform/support/availability-settings": "required",
        "/api/v1/platform/support/escalation-settings": "required",
        "/api/v1/platform/support/ws-ticket": "system_allowed",
        "/api/v1/platform/support/tickets": "required",
        "/api/v1/platform/support/saved-filters": "system_allowed",
        "/api/v1/platform/support/migrate-technical-requests": "required",
    }
    for path, reason_policy in mutation_paths.items():
        descriptor = operations[path]["put" if path.endswith("settings") else "post"][
            "x-platform-mutation"
        ]
        assert descriptor["snapshotAdapter"] != "none", path
        assert descriptor["reasonPolicy"] == reason_policy, path


@pytest.mark.asyncio
async def test_support_availability_update_rolls_back_when_audit_fails(
    support_factory,
    monkeypatch,
):
    platform_root, _ = await support_factory.user(
        name="Platform Root",
        is_platform_admin=True,
    )
    context = support_factory.context(platform_root)
    async with support_factory.client(context) as api:
        current = await api.get("/api/v1/platform/support/availability-settings")
        payload = {
            "enabled": current.json()["enabled"],
            "timezone": current.json()["timezone"],
            "days": current.json()["days"],
            "offlineMessage": "Mensagem operacional preservada.",
            "expectedEtag": current.json()["etag"],
        }
        initial = await api.put(
            "/api/v1/platform/support/availability-settings",
            headers={
                "X-Operation-Reason": quote("Configuração inicial auditável do SAC.")
            },
            json=payload,
        )
    assert initial.status_code == 200, initial.text

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        async with support_factory.client(context) as api:
            await api.put(
                "/api/v1/platform/support/availability-settings",
                headers={
                    "X-Operation-Reason": quote(
                        "Alteração que precisa ser revertida integralmente."
                    )
                },
                json={
                    **payload,
                    "offlineMessage": "Mensagem que não pode persistir.",
                    "expectedEtag": initial.json()["etag"],
                },
            )

    support_factory.db.expire_all()
    persisted = await SupportTicketService(support_factory.db).availability_settings()
    assert persisted["offline_message"] == "Mensagem operacional preservada."


@pytest.mark.asyncio
async def test_escalation_status_defaults_to_disabled_and_hides_everything(
    support_factory,
):
    workspace = await support_factory.workspace()
    workspace_user, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    async with support_factory.client(
        support_factory.context(workspace_user, workspace=workspace, membership=membership)
    ) as api:
        response = await api.get("/api/v1/support/escalation-status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "availableNow": False,
        "schedulingUrl": None,
        "whatsappLink": None,
        "nextWindow": None,
    }


@pytest.mark.asyncio
async def test_escalation_admin_settings_require_platform_root(support_factory):
    workspace = await support_factory.workspace()
    workspace_user, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    support_agent, _ = await support_factory.user(
        name="Support Agent",
        platform_role_package="support",
    )
    platform_root, _ = await support_factory.user(
        name="Platform Root",
        is_platform_admin=True,
    )

    async with support_factory.client(
        support_factory.context(workspace_user, workspace=workspace, membership=membership)
    ) as api:
        workspace_denied = await api.get("/api/v1/platform/support/escalation-settings")

    async with support_factory.client(support_factory.context(support_agent)) as api:
        staff_denied = await api.get("/api/v1/platform/support/escalation-settings")

    async with support_factory.client(support_factory.context(platform_root)) as api:
        root_get = await api.get("/api/v1/platform/support/escalation-settings")
        root_put = await api.put(
            "/api/v1/platform/support/escalation-settings",
            headers={"X-Operation-Reason": "Atualizar canais de atendimento humano."},
            json={
                "enabled": True,
                "schedulingUrl": "https://cal.com/mv-advocacia/consulta",
                "whatsappNumber": "+55 61 99999-0000",
                "businessDays": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                "businessHoursStart": "09:00",
                "businessHoursEnd": "18:00",
                "expectedEtag": root_get.json()["etag"],
            },
        )

    assert workspace_denied.status_code == 403
    assert staff_denied.status_code == 403
    assert root_get.status_code == 200
    assert root_put.status_code == 200
    assert root_put.json()["enabled"] is True
    assert root_put.json()["schedulingUrl"] == "https://cal.com/mv-advocacia/consulta"
    assert root_put.json()["version"] == 1


@pytest.mark.asyncio
async def test_escalation_settings_update_validates_url_and_hours(support_factory):
    platform_root, _ = await support_factory.user(
        name="Platform Root",
        is_platform_admin=True,
    )
    async with support_factory.client(support_factory.context(platform_root)) as api:
        current = await api.get("/api/v1/platform/support/escalation-settings")
        bad_url = await api.put(
            "/api/v1/platform/support/escalation-settings",
            headers={"X-Operation-Reason": "Validar URL insegura de agendamento."},
            json={
                "enabled": True,
                "schedulingUrl": "javascript:alert(1)",
                "whatsappNumber": None,
                "businessDays": ["monday"],
                "businessHoursStart": "09:00",
                "businessHoursEnd": "18:00",
                "expectedEtag": current.json()["etag"],
            },
        )
        bad_hours = await api.put(
            "/api/v1/platform/support/escalation-settings",
            headers={
                "X-Operation-Reason": quote("Validar horário comercial inválido.")
            },
            json={
                "enabled": True,
                "schedulingUrl": None,
                "whatsappNumber": None,
                "businessDays": ["monday"],
                "businessHoursStart": "25:99",
                "businessHoursEnd": "18:00",
                "expectedEtag": current.json()["etag"],
            },
        )
        inverted_window = await api.put(
            "/api/v1/platform/support/escalation-settings",
            headers={"X-Operation-Reason": "Validar janela comercial invertida."},
            json={
                "enabled": True,
                "schedulingUrl": None,
                "whatsappNumber": None,
                "businessDays": ["monday"],
                "businessHoursStart": "18:00",
                "businessHoursEnd": "09:00",
                "expectedEtag": current.json()["etag"],
            },
        )

    assert bad_url.status_code == 422
    assert bad_hours.status_code == 422
    assert inverted_window.status_code == 422


def test_escalation_window_computes_available_now_and_next_window_with_timezone():
    settings = SupportEscalationSettings.model_validate(
        {
            "enabled": True,
            "businessDays": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "businessHoursStart": "09:00",
            "businessHoursEnd": "18:00",
        }
    )

    # Segunda-feira 10:00 America/Sao_Paulo -> dentro do horário útil.
    inside_hours = datetime(2026, 7, 27, 13, 0, tzinfo=UTC)  # 10:00 -03:00
    available_now, next_window = SupportTicketService._escalation_window(
        settings, now=inside_hours
    )
    assert available_now is True
    assert next_window is None

    # Sábado -> fora dos dias úteis; próxima janela deve ser segunda 09:00 -03:00.
    saturday = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
    available_now, next_window = SupportTicketService._escalation_window(
        settings, now=saturday
    )
    assert available_now is False
    assert next_window is not None
    localized = next_window.astimezone(_SAO_PAULO)
    assert localized.weekday() == 0  # segunda-feira
    assert localized.strftime("%H:%M") == "09:00"

    # Disabled -> nunca disponível, nunca calcula próxima janela.
    disabled = settings.model_copy(update={"enabled": False})
    available_now, next_window = SupportTicketService._escalation_window(
        disabled, now=inside_hours
    )
    assert available_now is False
    assert next_window is None
