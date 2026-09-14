"""Identidade do WhatsApp em 2026 — telefone, BSUID e username convivendo.

Contexto e urgência
-------------------
Até 2026 o telefone era a identidade dentro do WhatsApp. Deixou de ser. A Meta
introduziu o **BSUID** (`business-scoped user ID`), que identifica o usuário
dentro do escopo de um portfólio de negócios, e os **usernames**, que permitem
ao usuário final ocultar o número.

Fatos verificados na documentação oficial da Meta (não no material do BSP):

- suportar BSUID é **obrigatório para parceiros E para negócios integrados
  diretamente** — não é exclusividade de BSP, como afirma material comercial;
- BSUIDs **passaram a aparecer nos webhooks no início de abril de 2026** e vêm
  em toda mensagem, tenha o usuário adotado username ou não;
- o `wa_id` (telefone) **passa a ser opcional**: some quando o usuário adota
  username e não há interação recente (30 dias) nem entrada no livro de
  contatos.

Ou seja: o `user_id` já está chegando há meses e sendo descartado, e o telefone
pode faltar a qualquer momento.

Formato dos campos (documentação oficial)
-----------------------------------------
Bloco `contacts`::

    {"profile": {"name": "...", "username": "..."},
     "wa_id": "<TELEFONE>",            # OPCIONAL a partir de 2026
     "user_id": "<BSUID>",
     "parent_user_id": "<PARENT_BSUID>"}

Bloco `statuses`::

    {"id": "...", "status": "...",
     "recipient_id": "<TELEFONE ou GROUP_ID>",
     "recipient_user_id": "<BSUID>",
     "recipient_parent_user_id": "<PARENT_BSUID>"}

BSUID tem a forma ``PAIS.alfanumerico`` (ex.: ``US.13491208655302741918``); o
parent inclui ``ENT`` (ex.: ``US.ENT.11815799212886844830``).

Invariantes que este módulo existe para garantir
------------------------------------------------
1. **Username nunca é chave.** Ele é visível, opcional e editável; o BSUID
   permanece quando o username muda. Chavear por username perde o vínculo.
2. **A empresa nunca inicia conversa pelo `@` do cliente.** O caminho do `@` só
   existe do cliente para a marca. Por isso `resolve_send_target` jamais
   devolve username.
3. **Telefone tem precedência sobre BSUID no envio** (regra da Meta: se os dois
   forem enviados, o telefone vence).
4. **Ausência de destino é dito, não escondido.** Sem telefone e sem BSUID, o
   retorno é explícito para que a operação apareça na tela em vez de a mensagem
   sumir.

Este módulo é PURO: não toca banco nem rede, para poder ser testado contra os
formatos reais de payload antes de qualquer mudança de esquema.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# País + ponto + alfanumérico. O parent traz o marcador ENT no meio.
_BSUID_RE = re.compile(r"^[A-Z]{2}\.(?:ENT\.)?[A-Za-z0-9]+$")

MAX_BSUID_LEN = 64
MAX_USERNAME_LEN = 64


def _clean(value: Any, *, maximum: int) -> str | None:
    """Normaliza texto vindo do provedor — dado não confiável por definição."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > maximum:
        return None
    return stripped


def is_valid_bsuid(value: Any) -> bool:
    """Valida a forma do BSUID.

    Vale ser rígido aqui: a documentação é explícita em que o valor deve ser
    usado COMPLETO nas chamadas de API, e que alterá-lo gera falha de
    identificação. Aceitar um valor truncado só adia o erro para o envio.
    """
    cleaned = _clean(value, maximum=MAX_BSUID_LEN)
    return bool(cleaned and _BSUID_RE.match(cleaned))


def normalize_username(value: Any) -> str | None:
    """Devolve o username sem o `@` inicial, ou None.

    Guardar com ou sem `@` de forma inconsistente é como o mesmo usuário vira
    dois registros. Aqui a forma canônica é SEM o arroba.
    """
    cleaned = _clean(value, maximum=MAX_USERNAME_LEN)
    if not cleaned:
        return None
    return cleaned.lstrip("@") or None


@dataclass(frozen=True)
class WhatsAppIdentityRef:
    """Identificadores de um usuário num evento do WhatsApp.

    Nenhum campo é obrigatório: é exatamente essa tolerância que falta hoje no
    processamento, que assume telefone sempre presente.
    """

    phone: str | None = None
    bsuid: str | None = None
    parent_bsuid: str | None = None
    username: str | None = None
    display_name: str | None = None

    @property
    def is_empty(self) -> bool:
        """Sem telefone e sem BSUID não há como reconhecer nem responder.

        Username NÃO conta: ele não endereça e pode mudar.
        """
        return not self.phone and not self.bsuid

    @property
    def lookup_keys(self) -> tuple[tuple[str, str], ...]:
        """Chaves de busca, do mais estável para o menos.

        BSUID primeiro de propósito: o username muda e o telefone pode ser
        ocultado, mas o BSUID permanece dentro do portfólio.
        """
        keys: list[tuple[str, str]] = []
        if self.bsuid:
            keys.append(("bsuid", self.bsuid))
        if self.phone:
            keys.append(("phone", self.phone))
        return tuple(keys)


def _identity_from_contact_entry(entry: dict[str, Any]) -> WhatsAppIdentityRef:
    profile = entry.get("profile") if isinstance(entry.get("profile"), dict) else {}
    bsuid = _clean(entry.get("user_id"), maximum=MAX_BSUID_LEN)
    parent = _clean(entry.get("parent_user_id"), maximum=MAX_BSUID_LEN)
    return WhatsAppIdentityRef(
        phone=_clean(entry.get("wa_id"), maximum=30),
        bsuid=bsuid if is_valid_bsuid(bsuid) else None,
        parent_bsuid=parent if is_valid_bsuid(parent) else None,
        username=normalize_username(profile.get("username")),
        display_name=_clean(profile.get("name"), maximum=200),
    )


def extract_inbound_identity(
    message: dict[str, Any], contacts: list[dict[str, Any]] | None
) -> WhatsAppIdentityRef:
    """Extrai a identidade do remetente de uma mensagem recebida.

    O casamento entre a mensagem e o bloco `contacts` era feito por
    ``wa_id == from``. Isso quebra quando o telefone não vem: passa a casar
    também por ``user_id``, e cai para a única entrada de `contacts` quando há
    apenas uma — que é o caso normal de mensagem individual.
    """
    from_phone = _clean(message.get("from"), maximum=30)
    from_bsuid = _clean(message.get("user_id"), maximum=MAX_BSUID_LEN)
    entries = [item for item in (contacts or []) if isinstance(item, dict)]

    matched: dict[str, Any] | None = None
    for entry in entries:
        entry_phone = _clean(entry.get("wa_id"), maximum=30)
        entry_bsuid = _clean(entry.get("user_id"), maximum=MAX_BSUID_LEN)
        if from_phone and entry_phone == from_phone:
            matched = entry
            break
        if from_bsuid and entry_bsuid == from_bsuid:
            matched = entry
            break
    if matched is None and len(entries) == 1:
        matched = entries[0]

    base = _identity_from_contact_entry(matched) if matched else WhatsAppIdentityRef()

    # O `from`/`user_id` da própria mensagem prevalece sobre o bloco de
    # contatos: é o remetente daquele evento, não um contato correlato.
    return WhatsAppIdentityRef(
        phone=from_phone or base.phone,
        bsuid=(from_bsuid if is_valid_bsuid(from_bsuid) else None) or base.bsuid,
        parent_bsuid=base.parent_bsuid,
        username=base.username,
        display_name=base.display_name,
    )


def extract_status_identity(
    status: dict[str, Any], contacts: list[dict[str, Any]] | None = None
) -> WhatsAppIdentityRef:
    """Extrai a identidade do destinatário num webhook de status.

    Aqui os nomes mudam: `recipient_id`, `recipient_user_id` e
    `recipient_parent_user_id`. A documentação é explícita em que o BSUID vem
    nos dois blocos independentemente de a mensagem ter sido enviada para o
    telefone ou para o BSUID.

    `recipient_id` pode ser um ID de GRUPO, e não um telefone. Só é tratado como
    telefone quando é numérico; caso contrário é ignorado, porque gravar um id
    de grupo no campo de telefone é o tipo de sujeira que só aparece meses
    depois, numa campanha que dispara para o lugar errado.
    """
    recipient = _clean(status.get("recipient_id"), maximum=64)
    phone = recipient if recipient and recipient.isdigit() else None
    bsuid = _clean(status.get("recipient_user_id"), maximum=MAX_BSUID_LEN)
    parent = _clean(status.get("recipient_parent_user_id"), maximum=MAX_BSUID_LEN)

    base = WhatsAppIdentityRef()
    entries = [item for item in (contacts or []) if isinstance(item, dict)]
    for entry in entries:
        entry_bsuid = _clean(entry.get("user_id"), maximum=MAX_BSUID_LEN)
        entry_phone = _clean(entry.get("wa_id"), maximum=30)
        if (bsuid and entry_bsuid == bsuid) or (phone and entry_phone == phone):
            base = _identity_from_contact_entry(entry)
            break

    return WhatsAppIdentityRef(
        phone=phone or base.phone,
        bsuid=(bsuid if is_valid_bsuid(bsuid) else None) or base.bsuid,
        parent_bsuid=(parent if is_valid_bsuid(parent) else None) or base.parent_bsuid,
        username=base.username,
        display_name=base.display_name,
    )


NOT_ADDRESSABLE_REASON = (
    "Contato sem telefone e sem BSUID conhecidos: não é possível iniciar "
    "conversa. O username não endereça — pelo `@` apenas o cliente encontra a "
    "empresa, nunca o contrário."
)


@dataclass(frozen=True)
class SendTarget:
    """Destino resolvido para envio.

    `kind` é `phone` ou `bsuid`. Nunca `username`, e isso é regra de plataforma,
    não preferência de implementação.
    """

    kind: str
    value: str

    @property
    def cloud_api_field(self) -> str:
        """Campo da Cloud API que carrega este destino.

        Para BSUID a documentação manda omitir `to` e usar `recipient` com o
        valor completo, incluindo o país e o ponto.
        """
        return "to" if self.kind == "phone" else "recipient"


# Campos que conhecemos hoje no bloco `contacts`. Serve de sentinela: quando a
# Meta acrescentar algo, a chave nova aparece como desconhecida e o time fica
# sabendo pelo log, não pela quebra.
KNOWN_CONTACT_FIELDS = frozenset(
    {"profile", "wa_id", "user_id", "parent_user_id", "identity_key_hash"}
)
KNOWN_PROFILE_FIELDS = frozenset({"name", "username"})
KNOWN_STATUS_IDENTITY_FIELDS = frozenset(
    {"recipient_id", "recipient_user_id", "recipient_parent_user_id"}
)


def unknown_identity_fields(
    contacts: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
) -> tuple[str, ...]:
    """Lista campos de identidade que a Meta enviou e nós não conhecemos.

    A plataforma do WhatsApp muda sem avisar cada integrador, e a forma de
    descobrir tarde é esperar a quebra. Esta função existe para descobrir cedo:
    campo novo no payload vira sinal observável em vez de ser ignorado em
    silêncio.

    Não é validação e não bloqueia nada — payload com campo desconhecido segue
    sendo processado normalmente. É instrumentação.
    """
    achados: set[str] = set()

    for entry in contacts or []:
        if not isinstance(entry, dict):
            continue
        achados.update(f"contacts.{k}" for k in entry.keys() - KNOWN_CONTACT_FIELDS)
        profile = entry.get("profile")
        if isinstance(profile, dict):
            achados.update(
                f"contacts.profile.{k}" for k in profile.keys() - KNOWN_PROFILE_FIELDS
            )

    for entry in statuses or []:
        if not isinstance(entry, dict):
            continue
        # Só os campos de identidade interessam aqui; o resto do bloco de status
        # tem contrato próprio e evolui por outro caminho.
        achados.update(
            f"statuses.{k}"
            for k in entry.keys()
            if k.startswith("recipient") and k not in KNOWN_STATUS_IDENTITY_FIELDS
        )

    return tuple(sorted(achados))


def resolve_send_target(identity: WhatsAppIdentityRef) -> SendTarget | None:
    """Escolhe por onde enviar, ou None quando não há destino.

    Telefone primeiro porque a própria Meta define que ele tem precedência
    quando os dois são informados. BSUID como alternativa real — o envio por
    BSUID passou a ser permitido em junho de 2026.

    Devolver None é resposta legítima e precisa chegar à tela: mensagem que
    desaparece em silêncio é cliente perdido sem ninguém saber que perdeu.
    """
    if identity.phone:
        return SendTarget(kind="phone", value=identity.phone)
    if identity.bsuid:
        return SendTarget(kind="bsuid", value=identity.bsuid)
    return None
