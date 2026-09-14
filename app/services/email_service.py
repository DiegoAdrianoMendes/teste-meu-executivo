"""
Serviço de envio de email via Resend.
Todos os templates em português brasileiro.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape as _html_escape
from typing import Optional
from urllib.parse import urljoin
from uuid import UUID

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type


def _get_resend_client():
    """Lazy import para não quebrar se RESEND_API_KEY não estiver configurado."""
    try:
        import resend
        if settings.RESEND_API_KEY:
            resend.api_key = settings.RESEND_API_KEY
        return resend
    except ImportError:
        return None


@dataclass(frozen=True)
class EmailDeliveryResult:
    sent: bool
    blocked: bool
    provider: str
    message_ref: str | None = None
    error_code: str | None = None


# ── Templates HTML ─────────────────────────────────────────────────────────────

def _base_template(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5;padding:32px 16px">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
        <!-- Header -->
        <tr>
          <td style="background:#050505;padding:32px 40px;text-align:center;border-bottom:3px solid #d4af37">
            <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700">Meu Executivo</h1>
            <p style="margin:4px 0 0;color:#d4af37;font-size:13px">Operação jurídica inteligente</p>
          </td>
        </tr>
        <!-- Content -->
        <tr>
          <td style="padding:40px">
            {content}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;padding:24px 40px;text-align:center;border-top:1px solid #e2e8f0">
            <p style="margin:0;color:#94a3b8;font-size:12px">
              © 2026 Meu Executivo. Todos os direitos reservados.<br>
              Este email foi enviado automaticamente — não responda diretamente.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _password_reset_template(name: str, reset_url: str) -> str:
    content = f"""
    <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">Redefinir sua senha</h2>
    <p style="margin:0 0 24px;color:#64748b;font-size:15px">Olá, {name}! Recebemos uma solicitação para redefinir a senha da sua conta.</p>

    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:20px;margin:0 0 24px">
      <p style="margin:0 0 4px;color:#1e40af;font-size:13px;font-weight:600">⏰ Este link expira em 1 hora</p>
      <p style="margin:0;color:#3b82f6;font-size:13px">Por segurança, o link só pode ser usado uma vez.</p>
    </div>

    <div style="text-align:center;margin:32px 0">
      <a href="{reset_url}" style="display:inline-block;background:#1e40af;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:8px;font-size:15px;font-weight:600">
        Redefinir Senha
      </a>
    </div>

    <p style="margin:0 0 8px;color:#94a3b8;font-size:13px">Ou acesse este link diretamente:</p>
    <p style="margin:0;word-break:break-all"><a href="{reset_url}" style="color:#3b82f6;font-size:12px">{reset_url}</a></p>

    <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0">
    <p style="margin:0;color:#94a3b8;font-size:13px">Se você não solicitou a redefinição, ignore este email. Sua senha permanece a mesma.</p>
    """
    return _base_template("Redefinir senha — Meu Executivo", content)


def _welcome_template(name: str, workspace_name: str, login_url: str) -> str:
    content = f"""
    <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">Bem-vindo ao Meu Executivo</h2>
    <p style="margin:0 0 24px;color:#64748b;font-size:15px">Olá, {name}! Sua conta foi criada com sucesso no workspace <strong>{workspace_name}</strong>.</p>

    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:20px;margin:0 0 24px">
      <p style="margin:0 0 12px;color:#15803d;font-size:14px;font-weight:600">O que você pode fazer agora:</p>
      <ul style="margin:0;padding-left:20px;color:#374151;font-size:14px;line-height:1.8">
        <li>Gerenciar casos jurídicos no Kanban</li>
        <li>Configurar agentes de IA para qualificação de leads</li>
        <li>Automatizar fluxos com o Flow Builder</li>
        <li>Integrar WhatsApp para atendimento automático</li>
      </ul>
    </div>

    <div style="text-align:center;margin:32px 0">
      <a href="{login_url}" style="display:inline-block;background:#1e40af;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:8px;font-size:15px;font-weight:600">
        Acessar Plataforma
      </a>
    </div>
    """
    return _base_template("Bem-vindo ao Meu Executivo", content)


def _invitation_template(inviter_name: str, workspace_name: str, invite_url: str, role_label: str) -> str:
    content = f"""
    <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">Convite para workspace jurídico</h2>
    <p style="margin:0 0 24px;color:#64748b;font-size:15px">
      <strong>{inviter_name}</strong> convidou você para colaborar no workspace <strong>{workspace_name}</strong> como <strong>{role_label}</strong>.
    </p>

    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:20px;margin:0 0 24px">
      <p style="margin:0;color:#1e40af;font-size:13px">⏰ Este convite expira em 7 dias.</p>
    </div>

    <div style="text-align:center;margin:32px 0">
      <a href="{invite_url}" style="display:inline-block;background:#1e40af;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:8px;font-size:15px;font-weight:600">
        Aceitar Convite
      </a>
    </div>

    <p style="margin:0;color:#94a3b8;font-size:13px">Se você não esperava este convite, pode ignorar este email com segurança.</p>
    """
    return _base_template(f"Convite — {workspace_name}", content)


def _operational_alert_template(
    *,
    title: str,
    workspace_name: str,
    body: str,
    cta_label: str,
    cta_url: str,
    meta_lines: list[str] | None = None,
) -> str:
    meta_html = ""
    if meta_lines:
        meta_html = "".join(
            f"<li style='margin:0 0 8px;color:#475569;font-size:14px'>{line}</li>"
            for line in meta_lines
        )
        meta_html = f"<ul style='margin:0 0 24px;padding-left:18px'>{meta_html}</ul>"

    content = f"""
    <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">{title}</h2>
    <p style="margin:0 0 8px;color:#64748b;font-size:15px">{body}</p>
    <p style="margin:0 0 24px;color:#94a3b8;font-size:13px">Workspace: <strong>{workspace_name}</strong></p>
    {meta_html}
    <div style="text-align:center;margin:32px 0">
      <a href="{cta_url}" style="display:inline-block;background:#0f88c7;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:10px;font-size:15px;font-weight:600">
        {cta_label}
      </a>
    </div>
    """
    return _base_template(title, content)


def _daily_digest_template(
    *,
    workspace_name: str,
    digest_date: str,
    highlights: list[str],
    metrics: list[tuple[str, str]],
    agenda: list[str],
    risks: list[str],
    app_url: str,
) -> str:
    def _list_html(items: list[str], empty: str) -> str:
        if not items:
            return f"<p style='margin:0;color:#94a3b8;font-size:13px'>{empty}</p>"
        rows = "".join(
            f"<li style='margin:0 0 8px;color:#475569;font-size:14px'>{item}</li>"
            for item in items
        )
        return f"<ul style='margin:0;padding-left:18px'>{rows}</ul>"

    metric_rows = "".join(
        f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:13px">{label}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;color:#0f172a;font-size:13px;font-weight:600;text-align:right">{value}</td>
        </tr>
        """
        for label, value in metrics
    )
    content = f"""
    <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">Resumo diário do workspace</h2>
    <p style="margin:0 0 8px;color:#64748b;font-size:15px">Aqui está o panorama operacional de <strong>{workspace_name}</strong> em {digest_date}.</p>

    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:20px;margin:24px 0">
      <p style="margin:0 0 12px;color:#0f88c7;font-size:14px;font-weight:700">Principais sinais do dia</p>
      {_list_html(highlights, "Sem destaques críticos no período.")}
    </div>

    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin:0 0 24px">
      {metric_rows}
    </table>

    <div style="margin:0 0 24px">
      <h3 style="margin:0 0 12px;color:#1e293b;font-size:16px">Agenda do dia</h3>
      {_list_html(agenda, "Nenhuma reunião agendada para hoje.")}
    </div>

    <div style="margin:0 0 24px">
      <h3 style="margin:0 0 12px;color:#1e293b;font-size:16px">Pendências e riscos</h3>
      {_list_html(risks, "Nenhum risco operacional destacado agora.")}
    </div>

    <div style="text-align:center;margin:32px 0">
      <a href="{app_url}" style="display:inline-block;background:#0f88c7;color:#ffffff;text-decoration:none;padding:14px 36px;border-radius:10px;font-size:15px;font-weight:600">
        Abrir workspace
      </a>
    </div>
    """
    return _base_template(f"Resumo diário — {workspace_name}", content)


def _support_transcript_message_row(message: dict[str, str]) -> str:
    raw_created_at = str(message.get("createdAt") or "")
    try:
        parsed = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        stamp = parsed.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        stamp = raw_created_at
    author_label = _html_escape(str(message.get("authorLabel") or ""))
    author_kind = _html_escape(str(message.get("authorKind") or ""))
    body = _html_escape(str(message.get("body") or "")).replace("\n", "<br>")
    return f"""
    <tr>
      <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;color:#94a3b8;font-size:12px;white-space:nowrap;vertical-align:top">{stamp}</td>
      <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;color:#0f172a;font-size:13px;font-weight:600;vertical-align:top">{author_label}<br><span style="color:#94a3b8;font-weight:400">{author_kind}</span></td>
      <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;color:#334155;font-size:13px">{body}</td>
    </tr>
    """


def _support_transcript_template(
    *,
    to_name: str,
    ticket_number: str,
    final_status_label: str,
    messages: list[dict[str, str]],
    escalation: dict[str, str] | None,
) -> str:
    rows = "".join(_support_transcript_message_row(m) for m in messages) or (
        "<tr><td colspan='3' style='padding:14px;color:#94a3b8;font-size:13px'>"
        "Sem mensagens públicas registradas neste chamado.</td></tr>"
    )

    escalation_html = ""
    if escalation:
        items: list[str] = []
        scheduling_url = escalation.get("schedulingUrl")
        whatsapp_number = escalation.get("whatsappNumber")
        business_hours = escalation.get("businessHours")
        if scheduling_url:
            safe_url = _html_escape(scheduling_url)
            items.append(
                f"<li style='margin:0 0 8px'><a href=\"{safe_url}\" "
                f"style=\"color:#1e40af\">Agendar com um atendente humano</a></li>"
            )
        if whatsapp_number:
            items.append(
                "<li style='margin:0 0 8px'>WhatsApp/telefone: "
                f"<strong>{_html_escape(whatsapp_number)}</strong></li>"
            )
        if business_hours:
            items.append(
                "<li style='margin:0 0 8px'>Horário de atendimento: "
                f"{_html_escape(business_hours)}</li>"
            )
        items.append(
            "<li style='margin:0'>Ou simplesmente responda este email — "
            "sua mensagem chega direto à nossa equipe.</li>"
        )
        escalation_html = f"""
        <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:20px;margin:24px 0">
          <p style="margin:0 0 12px;color:#9a3412;font-size:14px;font-weight:700">Ainda precisa de ajuda?</p>
          <ul style="margin:0;padding-left:18px;color:#7c2d12;font-size:14px;line-height:1.8">{''.join(items)}</ul>
        </div>
        """

    safe_name = _html_escape(to_name)
    safe_ticket_number = _html_escape(ticket_number)
    safe_status_label = _html_escape(final_status_label)
    content = f"""
    <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">Cópia do atendimento — Chamado {safe_ticket_number}</h2>
    <p style="margin:0 0 8px;color:#64748b;font-size:15px">Olá, {safe_name}! Segue a íntegra da conversa do seu chamado, encerrado com status <strong>{safe_status_label}</strong>.</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin:24px 0">
      {rows}
    </table>
    {escalation_html}
    """
    return _base_template(f"Cópia do atendimento — Chamado {ticket_number}", content)


# ── Email sending functions ─────────────────────────────────────────────────────

async def send_password_reset_email(
    to_email: str,
    to_name: str,
    reset_token: str,
) -> bool:
    """Envia email de redefinição de senha."""
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"
    html = _password_reset_template(to_name, reset_url)

    return await _send_email(
        to=to_email,
        subject="Redefinição de senha — Meu Executivo",
        html=html,
    )


async def send_welcome_email(
    to_email: str,
    to_name: str,
    workspace_name: str,
) -> bool:
    """Envia email de boas-vindas após registro."""
    login_url = f"{settings.FRONTEND_URL}/login"
    html = _welcome_template(to_name, workspace_name, login_url)

    return await _send_email(
        to=to_email,
        subject=f"Bem-vindo ao Meu Executivo — {workspace_name}",
        html=html,
    )


async def send_invitation_email(
    to_email: str,
    inviter_name: str,
    workspace_name: str,
    invite_token: str,
    role_label: str = "Membro",
) -> bool:
    """Envia convite para novos membros do workspace."""
    invite_url = f"{settings.FRONTEND_URL}/invite?token={invite_token}"
    html = _invitation_template(inviter_name, workspace_name, invite_url, role_label)

    return await _send_email(
        to=to_email,
        subject=f"Convite para {workspace_name} — Meu Executivo",
        html=html,
    )


async def send_operational_alert_email(
    *,
    to_email: str,
    subject: str,
    workspace_name: str,
    body: str,
    cta_label: str,
    cta_url: str,
    meta_lines: list[str] | None = None,
) -> bool:
    html = _operational_alert_template(
        title=subject,
        workspace_name=workspace_name,
        body=body,
        cta_label=cta_label,
        cta_url=cta_url,
        meta_lines=meta_lines,
    )
    return await _send_email(to=to_email, subject=subject, html=html)


async def send_daily_digest_email(
    *,
    to_email: str,
    workspace_name: str,
    digest_date: str,
    highlights: list[str],
    metrics: list[tuple[str, str]],
    agenda: list[str],
    risks: list[str],
) -> bool:
    html = _daily_digest_template(
        workspace_name=workspace_name,
        digest_date=digest_date,
        highlights=highlights,
        metrics=metrics,
        agenda=agenda,
        risks=risks,
        app_url=f"{settings.FRONTEND_URL}/app",
    )
    return await _send_email(
        to=to_email,
        subject=f"Resumo diário — {workspace_name}",
        html=html,
    )


async def send_support_transcript_email(
    *,
    to_email: str,
    to_name: str,
    ticket_number: str,
    final_status_label: str,
    messages: list[dict[str, str]],
    escalation: dict[str, str] | None = None,
) -> bool:
    """Envia a cópia integral da conversa ao encerrar um chamado de suporte (F15).

    Mensagens internas (visíveis só à equipe) nunca devem chegar aqui — o
    chamador é responsável por filtrar `messages` para apenas as públicas.
    """
    html = _support_transcript_template(
        to_name=to_name,
        ticket_number=ticket_number,
        final_status_label=final_status_label,
        messages=messages,
        escalation=escalation,
    )
    return await _send_email(
        to=to_email,
        subject=f"Cópia do atendimento — Chamado {ticket_number}",
        html=html,
    )


async def send_identity_outbox_email(
    *,
    to_email: str,
    purpose: str,
    link: str,
) -> EmailDeliveryResult:
    """Deliver one encrypted identity lifecycle message.

    The caller supplies only the decrypted recipient and one-time link. No
    token or address is logged by this service.
    """
    labels = {
        "workspace_invitation": (
            "Convite para o Meu Executivo",
            "Aceitar convite",
            "Você recebeu um convite para colaborar em um workspace do Meu Executivo.",
        ),
        "password_reset": (
            "Redefinição de senha — Meu Executivo",
            "Redefinir senha",
            "Foi solicitada uma redefinição de senha para sua conta.",
        ),
        "signup_verification": (
            "Confirme sua conta — Meu Executivo",
            "Confirmar conta",
            "Confirme seu endereço de email para ativar sua conta.",
        ),
    }
    subject, cta_label, body = labels.get(
        purpose,
        ("Ação necessária — Meu Executivo", "Continuar", "Há uma ação pendente na sua conta."),
    )
    absolute_link = urljoin(f"{settings.FRONTEND_URL.rstrip('/')}/", link.lstrip("/"))
    safe_link = _html_escape(absolute_link, quote=True)
    html = _base_template(
        subject,
        f"""
        <h2 style="margin:0 0 8px;color:#1e293b;font-size:22px;font-weight:600">
          {_html_escape(subject)}
        </h2>
        <p style="margin:0 0 24px;color:#64748b;font-size:15px">
          {_html_escape(body)}
        </p>
        <div style="text-align:center;margin:32px 0">
          <a href="{safe_link}"
             style="display:inline-block;background:#1e40af;color:#ffffff;
                    text-decoration:none;padding:14px 36px;border-radius:8px;
                    font-size:15px;font-weight:600">
            {_html_escape(cta_label)}
          </a>
        </div>
        <p style="margin:0;color:#94a3b8;font-size:13px">
          O link é pessoal, temporário e de uso único.
        </p>
        """,
    )
    return await _send_email_result(to=to_email, subject=subject, html=html)


async def _send_email(to: str, subject: str, html: str) -> bool:
    """Compatibility wrapper returning only success."""
    return (await _send_email_result(to=to, subject=subject, html=html)).sent


async def _send_email_result(to: str, subject: str, html: str) -> EmailDeliveryResult:
    """Send through the configured transport and return an observable result."""
    recipient_ref = opaque_ref(to, kind="email")
    if not external_delivery_allowed():
        logger.info("email.delivery_blocked_by_klm_gate", recipient_ref=recipient_ref)
        return EmailDeliveryResult(
            sent=False,
            blocked=True,
            provider=settings.EMAIL_TRANSPORT,
            error_code="EMAIL_EXTERNAL_GATE_CLOSED",
        )

    if settings.EMAIL_TRANSPORT == "ses":
        try:
            import boto3

            client = boto3.client("sesv2", region_name=settings.AWS_REGION)
            response = await asyncio.to_thread(
                client.send_email,
                FromEmailAddress=f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
                Destination={"ToAddresses": [to]},
                ReplyToAddresses=[settings.EMAIL_REPLY_TO]
                if settings.EMAIL_REPLY_TO
                else [],
                Content={
                    "Simple": {
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Html": {"Data": html, "Charset": "UTF-8"}},
                    }
                },
            )
            message_ref = str(response.get("MessageId") or "") or None
            logger.info("email.sent", recipient_ref=recipient_ref, provider="ses")
            return EmailDeliveryResult(
                sent=True,
                blocked=False,
                provider="ses",
                message_ref=message_ref,
            )
        except Exception as error:
            error_code = safe_error_type(error)
            logger.error(
                "email.send_failed",
                recipient_ref=recipient_ref,
                provider="ses",
                error_type=error_code,
            )
            return EmailDeliveryResult(
                sent=False,
                blocked=False,
                provider="ses",
                error_code=error_code,
            )

    if not settings.RESEND_API_KEY:
        logger.warning("email.not_configured", recipient_ref=recipient_ref, provider="resend")
        return EmailDeliveryResult(
            sent=False,
            blocked=True,
            provider="resend",
            error_code="EMAIL_PROVIDER_NOT_CONFIGURED",
        )

    resend = _get_resend_client()
    if not resend:
        logger.error("email.resend_not_installed")
        return EmailDeliveryResult(
            sent=False,
            blocked=False,
            provider="resend",
            error_code="EMAIL_PROVIDER_UNAVAILABLE",
        )

    try:
        params = {
            "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if settings.EMAIL_REPLY_TO:
            params["reply_to"] = settings.EMAIL_REPLY_TO
        response = resend.Emails.send(params)
        message_ref = str((response or {}).get("id") or "") or None
        logger.info("email.sent", recipient_ref=recipient_ref, provider="resend")
        return EmailDeliveryResult(
            sent=True,
            blocked=False,
            provider="resend",
            message_ref=message_ref,
        )
    except Exception as e:
        logger.error(
            "email.send_failed",
            recipient_ref=recipient_ref,
            error_type=safe_error_type(e),
        )
        return EmailDeliveryResult(
            sent=False,
            blocked=False,
            provider="resend",
            error_code=safe_error_type(e),
        )
