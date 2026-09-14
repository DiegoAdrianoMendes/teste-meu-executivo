"""Meta Policy Gate — porta única, canal-agnóstica, fail-closed, ANTES de qualquer dispatcher.

Origem: `docs/PLANO_META_MAXIMO_2026-07-23.md` seção 1 item 1 e seção 4 (I1),
`docs/AUDITORIA_META_CANAIS_2026-07-23.md` achado C3 e os ALTOS de Instagram/Messenger.

Este módulo é PURO (sem IO, sem sessão de banco, sem chamada externa): recebe os
fatos já carregados pelo chamador (worker/orquestração) e devolve uma decisão.
Isso o torna testável sem fixtures assíncronas e reutilizável nos dois workers
(`omnichannel_action_tasks.py` e `omnichannel_scheduled_tasks.py`).

Regras implementadas nesta versão (ver PLANO seção 4-I1 e seção 5):
    a) Canais Meta (whatsapp/instagram/messenger/facebook): janela de 24h ESTRITA
       a partir de `last_inbound_at`. Sem `last_inbound_at` => nega (gatilho-first:
       sem prova de DM real do contato, não existe janela aberta).
    b) WhatsApp fora da janela: nega com `requires="template"`. O dispatcher oficial
       do WhatsApp (`app/services/whatsapp/**`, fora do escopo deste sprint) já
       sabe resolver/lançar template; o gate apenas padroniza a razão e a
       intenção de deixar passar para esse fluxo (ver workers).
    c) Instagram/Messenger fora da janela: nega (razão legível). HUMAN_AGENT
       (janela estendida restrita a `author_type=human`, <=7 dias, com App Review
       própria da Meta) fica para sprint futuro — NÃO implementado aqui.
    d) `private_reply` (comentário -> DM no Instagram): o gatilho é o comentário,
       não o DM; a janela é de 7 dias a partir de `occurred_at_of_source`
       (o `occurred_at` do comentário-fonte, não de `last_inbound_at`). Fora da
       janela: nega, não-retryable (o comentário expirou, retry nunca resolve).
    e) Instagram: cap de 200 mensagens automatizadas/hora POR CONEXÃO. Quando
       `automated_hourly_count` é informado e >= 200, nega com
       `reason_code="ig_hourly_cap"`. O cômputo do contador (por `connection_id`,
       só `author_type=agent`) é responsabilidade do chamador
       (`MessagingCadenceService.count_recent_agent_messages_for_connection`).

Fora de escopo desta versão (documentado, não implementado aqui):
    - HUMAN_AGENT (Instagram/Messenger): requer App Review própria da Meta;
      tratado como reprovação padrão ("fora da janela") até esse trabalho existir.
    - Mapa de erros por `provider_status_code` (achado C2 do plano, módulo distinto).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

META_CHANNELS: frozenset[str] = frozenset(
    {"whatsapp", "instagram", "messenger", "facebook"}
)

_STRICT_WINDOW = timedelta(hours=24)
_PRIVATE_REPLY_WINDOW = timedelta(days=7)
IG_HOURLY_CAP = 200


@dataclass(frozen=True)
class PolicyDecision:
    """Veredito do gate para uma tentativa de envio outbound."""

    allowed: bool
    reason_code: str
    reason: str
    requires: str | None = None

    @classmethod
    def allow(cls, reason_code: str = "ok", reason: str = "Dentro da política.") -> "PolicyDecision":
        return cls(allowed=True, reason_code=reason_code, reason=reason, requires=None)

    @classmethod
    def deny(
        cls,
        reason_code: str,
        reason: str,
        *,
        requires: str | None = None,
    ) -> "PolicyDecision":
        return cls(allowed=False, reason_code=reason_code, reason=reason, requires=requires)


def _aware(value: datetime) -> datetime:
    """Normaliza para um datetime comparável (assume UTC quando naive)."""
    if value.tzinfo is None:
        from datetime import UTC

        return value.replace(tzinfo=UTC)
    return value


def clamp_service_window_hours(channel: str, configured_hours: int) -> int:
    """Clampa `serviceWindowHours` (schema aceita 1-168) para 24h em canais Meta.

    Usado em `omnichannel_orchestration_service.py` e `omnichannel_scheduled_tasks.py`
    (PLANO seção 4-I1 item 5: "clamp fixo para canais Meta, matar 168h"). Canais
    não-Meta seguem o range configurável de 1-168h sem alteração.
    """
    channel = (channel or "").strip().lower()
    try:
        hours = int(configured_hours)
    except (TypeError, ValueError):
        hours = 24
    bounded = max(1, min(hours, 168))
    if channel in META_CHANNELS:
        return min(bounded, 24)
    return bounded


class MetaPolicyGate:
    """Avalia se um envio outbound pode prosseguir, canal a canal, sem IO."""

    @staticmethod
    def evaluate(
        channel: str,
        *,
        last_inbound_at: datetime | None,
        now: datetime,
        author_type: str,
        action_type: str,
        occurred_at_of_source: datetime | None = None,
        automated_hourly_count: int | None = None,
    ) -> PolicyDecision:
        channel = (channel or "").strip().lower()
        action_type = (action_type or "").strip().lower()
        now = _aware(now)

        if channel not in META_CHANNELS:
            # Canais não-Meta (e-mail, webchat, form, etc.) não passam por este gate.
            return PolicyDecision.allow(
                "not_meta_channel", "Canal não sujeito ao Meta Policy Gate."
            )

        # Regra (e): cap de 200 msgs automatizadas/h por conexão, só Instagram.
        if (
            channel == "instagram"
            and automated_hourly_count is not None
            and automated_hourly_count >= IG_HOURLY_CAP
        ):
            return PolicyDecision.deny(
                "ig_hourly_cap",
                (
                    f"Cap de {IG_HOURLY_CAP} mensagens automatizadas/hora por conexão do "
                    "Instagram atingido. Aguarde a janela deslizante liberar capacidade."
                ),
            )

        # Regra (d): private_reply usa a janela de 7 dias do COMENTÁRIO-fonte, não
        # a janela de 24h de last_inbound_at (que rege o DM, não o comentário).
        if action_type == "private_reply":
            if occurred_at_of_source is None:
                return PolicyDecision.deny(
                    "private_reply_source_missing",
                    "Comentário-fonte do private reply não foi informado; recusa fail-closed.",
                )
            source = _aware(occurred_at_of_source)
            if now - source > _PRIVATE_REPLY_WINDOW:
                return PolicyDecision.deny(
                    "private_reply_expired",
                    "O comentário-fonte tem mais de 7 dias; a resposta privada não é mais elegível.",
                )
            return PolicyDecision.allow(
                "private_reply_within_window",
                "Comentário-fonte dentro da janela de 7 dias.",
            )

        # Regras (a)-(c): janela de 24h estrita a partir de last_inbound_at, para
        # todo envio outbound que não seja private_reply.
        if last_inbound_at is None:
            return PolicyDecision.deny(
                "no_inbound_yet",
                (
                    "Nenhuma mensagem inbound real registrada para este contato; "
                    "envio livre negado (gatilho-first)."
                ),
                requires="template" if channel == "whatsapp" else None,
            )

        last_inbound = _aware(last_inbound_at)
        if now - last_inbound > _STRICT_WINDOW:
            if channel == "whatsapp":
                return PolicyDecision.deny(
                    "window_expired",
                    "Janela de 24h do WhatsApp expirou; envio livre requer template aprovado.",
                    requires="template",
                )
            canal_label = "Instagram" if channel == "instagram" else (
                "Messenger" if channel == "messenger" else "Facebook"
            )
            return PolicyDecision.deny(
                "window_expired",
                (
                    f"Janela de 24h do {canal_label} expirou; HUMAN_AGENT não está "
                    "implementado neste sprint (ver docstring do módulo) — envio negado."
                ),
            )

        return PolicyDecision.allow(
            "within_window", "Dentro da janela de 24h a partir do último inbound."
        )


__all__ = [
    "MetaPolicyGate",
    "PolicyDecision",
    "META_CHANNELS",
    "IG_HOURLY_CAP",
    "clamp_service_window_hours",
]
