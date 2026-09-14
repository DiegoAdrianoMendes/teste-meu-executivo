"""Testes puros do Meta Policy Gate (sem IO), ver docs/PLANO_META_MAXIMO_2026-07-23.md
seção 1 item 1 e seção 4-I1; achados da auditoria de 23/07/2026."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.meta_policy_gate import (
    IG_HOURLY_CAP,
    META_CHANNELS,
    MetaPolicyGate,
    clamp_service_window_hours,
)

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def test_meta_channels_frozenset() -> None:
    assert META_CHANNELS == {"whatsapp", "instagram", "messenger", "facebook"}


def test_non_meta_channel_always_allowed() -> None:
    decision = MetaPolicyGate.evaluate(
        "email",
        last_inbound_at=None,
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert decision.allowed
    assert decision.reason_code == "not_meta_channel"


@pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger", "facebook"])
def test_no_inbound_yet_denies_gatilho_first(channel: str) -> None:
    decision = MetaPolicyGate.evaluate(
        channel,
        last_inbound_at=None,
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert not decision.allowed
    assert decision.reason_code == "no_inbound_yet"
    if channel == "whatsapp":
        assert decision.requires == "template"
    else:
        assert decision.requires is None


def test_whatsapp_within_24h_window_allowed() -> None:
    decision = MetaPolicyGate.evaluate(
        "whatsapp",
        last_inbound_at=NOW - timedelta(hours=23, minutes=59),
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert decision.allowed


def test_whatsapp_exactly_at_24h_boundary_still_allowed() -> None:
    decision = MetaPolicyGate.evaluate(
        "whatsapp",
        last_inbound_at=NOW - timedelta(hours=24),
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert decision.allowed


def test_whatsapp_one_second_past_24h_requires_template() -> None:
    decision = MetaPolicyGate.evaluate(
        "whatsapp",
        last_inbound_at=NOW - timedelta(hours=24, seconds=1),
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert not decision.allowed
    assert decision.reason_code == "window_expired"
    assert decision.requires == "template"


@pytest.mark.parametrize("channel", ["instagram", "messenger", "facebook"])
def test_social_channel_past_24h_denies_without_requires(channel: str) -> None:
    decision = MetaPolicyGate.evaluate(
        channel,
        last_inbound_at=NOW - timedelta(hours=25),
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert not decision.allowed
    assert decision.reason_code == "window_expired"
    assert decision.requires is None


def test_168h_clamp_matata_no_gate_level() -> None:
    """A janela de 168h configurável no schema não abre exceção no gate: o gate
    sempre trata canais Meta com 24h estritas, independente de configuração."""
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=NOW - timedelta(hours=48),
        now=NOW,
        author_type="agent",
        action_type="send_message",
    )
    assert not decision.allowed


def test_private_reply_within_7_days_allowed() -> None:
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=None,
        now=NOW,
        author_type="agent",
        action_type="private_reply",
        occurred_at_of_source=NOW - timedelta(days=6, hours=23),
    )
    assert decision.allowed


def test_private_reply_at_exactly_7_days_allowed() -> None:
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=None,
        now=NOW,
        author_type="agent",
        action_type="private_reply",
        occurred_at_of_source=NOW - timedelta(days=7),
    )
    assert decision.allowed


def test_private_reply_past_7_days_denies_not_retryable() -> None:
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=None,
        now=NOW,
        author_type="agent",
        action_type="private_reply",
        occurred_at_of_source=NOW - timedelta(days=7, seconds=1),
    )
    assert not decision.allowed
    assert decision.reason_code == "private_reply_expired"


def test_private_reply_without_source_denies_fail_closed() -> None:
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=None,
        now=NOW,
        author_type="agent",
        action_type="private_reply",
        occurred_at_of_source=None,
    )
    assert not decision.allowed
    assert decision.reason_code == "private_reply_source_missing"


def test_private_reply_ignores_24h_window_of_last_inbound() -> None:
    """A janela do DM (24h) não deveria bloquear o private_reply: ele usa a
    janela de 7 dias do comentário-fonte."""
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=NOW - timedelta(days=2),  # DM window bem expirada
        now=NOW,
        author_type="agent",
        action_type="private_reply",
        occurred_at_of_source=NOW - timedelta(hours=1),
    )
    assert decision.allowed


def test_instagram_hourly_cap_denies_at_threshold() -> None:
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=NOW - timedelta(hours=1),
        now=NOW,
        author_type="agent",
        action_type="send_message",
        automated_hourly_count=IG_HOURLY_CAP,
    )
    assert not decision.allowed
    assert decision.reason_code == "ig_hourly_cap"


def test_instagram_hourly_cap_allows_below_threshold() -> None:
    decision = MetaPolicyGate.evaluate(
        "instagram",
        last_inbound_at=NOW - timedelta(hours=1),
        now=NOW,
        author_type="agent",
        action_type="send_message",
        automated_hourly_count=IG_HOURLY_CAP - 1,
    )
    assert decision.allowed


def test_hourly_cap_only_applies_to_instagram() -> None:
    decision = MetaPolicyGate.evaluate(
        "messenger",
        last_inbound_at=NOW - timedelta(hours=1),
        now=NOW,
        author_type="agent",
        action_type="send_message",
        automated_hourly_count=999,
    )
    assert decision.allowed


class TestClampServiceWindowHours:
    def test_meta_channel_clamped_to_24(self) -> None:
        assert clamp_service_window_hours("whatsapp", 168) == 24
        assert clamp_service_window_hours("instagram", 100) == 24
        assert clamp_service_window_hours("messenger", 25) == 24
        assert clamp_service_window_hours("facebook", 50) == 24

    def test_meta_channel_below_24_kept(self) -> None:
        assert clamp_service_window_hours("whatsapp", 12) == 12

    def test_non_meta_channel_not_clamped(self) -> None:
        assert clamp_service_window_hours("email", 168) == 168
        assert clamp_service_window_hours("webchat", 100) == 100

    def test_invalid_configuration_defaults_to_24(self) -> None:
        assert clamp_service_window_hours("whatsapp", "not-a-number") == 24
        assert clamp_service_window_hours("email", None) == 24

    def test_lower_bound_enforced(self) -> None:
        assert clamp_service_window_hours("email", 0) == 1
        assert clamp_service_window_hours("email", -5) == 1
