"""
Integração: Slack
Envia alertas e notificações para canais do Slack.
Usado para: prazos críticos, aprovações urgentes, erros de agentes.
"""
from typing import Optional

import httpx

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type


class SlackNotifier:
    """Envia mensagens para o Slack via Incoming Webhooks ou Bot API."""

    BASE_URL = "https://slack.com/api"

    async def send_message(
        self,
        text: str,
        channel: Optional[str] = None,
        blocks: Optional[list] = None,
    ) -> bool:
        """Envia uma mensagem para o canal configurado."""
        if not external_delivery_allowed():
            logger.info("slack_delivery_blocked", code="KLM-EXT-001")
            return False
        if not settings.SLACK_BOT_TOKEN:
            logger.warning("slack_not_configured")
            return False

        target_channel = channel or settings.SLACK_CHANNEL_ALERTS
        payload = {"channel": target_channel, "text": text}
        if blocks:
            payload["blocks"] = blocks

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/chat.postMessage",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
                    timeout=10.0,
                )
                data = response.json()
                if not data.get("ok"):
                    logger.error(
                        "slack_send_error",
                        channel_ref=opaque_ref(target_channel, kind="slack_channel"),
                        status_code=response.status_code,
                    )
                    return False
                return True
        except Exception as e:
            logger.error("slack_request_error", error_type=safe_error_type(e))
            return False

    async def notify_approval_requested(self, matter_id: str, title: str, priority: str) -> bool:
        """Notifica o time sobre uma nova aprovação na fila."""
        emoji = "🚨" if priority == "alta" else "📋"
        text = f"{emoji} *Nova aprovação pendente* [{priority.upper()}]\n*{title}*\nCaso: `{matter_id}`"
        return await self.send_message(text)

    async def notify_deadline_alert(self, matter_id: str, client_name: str, deadline: str) -> bool:
        """Alerta sobre prazo crítico se aproximando."""
        text = f"⏰ *Prazo crítico!*\nCliente: *{client_name}*\nPrazo: `{deadline}`\nCaso: `{matter_id}`"
        return await self.send_message(text)

    async def notify_agent_error(self, agent_name: str, matter_id: str, error: str) -> bool:
        """Notifica sobre falha em um agente de IA."""
        text = f"🤖❌ *Erro no agente {agent_name}*\nCaso: `{matter_id}`\nErro: `{error[:200]}`"
        return await self.send_message(text)


# Instância global
slack = SlackNotifier()
