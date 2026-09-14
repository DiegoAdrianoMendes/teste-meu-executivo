"""Convivência telefone / BSUID / username nos webhooks do WhatsApp (2026).

Os payloads destes testes seguem os formatos publicados pela Meta em
`developers.facebook.com` para business-scoped user IDs. O que está sendo
protegido, em ordem de dor:

1. mensagem de usuário SEM telefone não pode ser perdida — é o cenário novo,
   e hoje o processamento assume `from` sempre presente;
2. username nunca vira chave nem destino de envio;
3. BSUID inválido ou truncado não entra, porque a documentação avisa que valor
   alterado gera falha de identificação — falhar cedo é melhor que falhar no
   envio.
"""
from __future__ import annotations

from app.services.whatsapp.identity_resolution import (
    SendTarget,
    WhatsAppIdentityRef,
    extract_inbound_identity,
    extract_status_identity,
    is_valid_bsuid,
    normalize_username,
    resolve_send_target,
)

BSUID = "BR.13491208655302741918"
PARENT_BSUID = "BR.ENT.11815799212886844830"
PHONE = "5561999990000"


# ── Forma do BSUID ─────────────────────────────────────────────────────────


def test_bsuid_valido_e_invalido():
    assert is_valid_bsuid(BSUID)
    assert is_valid_bsuid("US.13491208655302741918")
    assert is_valid_bsuid(PARENT_BSUID)

    # Sem país, sem ponto, vazio ou truncado: não entra.
    assert not is_valid_bsuid("13491208655302741918")
    assert not is_valid_bsuid("BR")
    assert not is_valid_bsuid("")
    assert not is_valid_bsuid(None)
    assert not is_valid_bsuid("BR.abc-def")


def test_username_normalizado_sem_arroba():
    """Guardar ora com `@` ora sem é como o mesmo usuário vira dois registros."""
    assert normalize_username("@escritorio") == "escritorio"
    assert normalize_username("escritorio") == "escritorio"
    assert normalize_username("  @escritorio  ") == "escritorio"
    assert normalize_username("@") is None
    assert normalize_username(None) is None


# ── Mensagem recebida ──────────────────────────────────────────────────────


def test_mensagem_com_telefone_e_bsuid():
    identity = extract_inbound_identity(
        {"id": "wamid.x", "from": PHONE, "user_id": BSUID},
        [
            {
                "profile": {"name": "Cliente Teste", "username": "@cliente"},
                "wa_id": PHONE,
                "user_id": BSUID,
                "parent_user_id": PARENT_BSUID,
            }
        ],
    )

    assert identity.phone == PHONE
    assert identity.bsuid == BSUID
    assert identity.parent_bsuid == PARENT_BSUID
    assert identity.username == "cliente"
    assert identity.display_name == "Cliente Teste"
    assert not identity.is_empty


def test_mensagem_sem_telefone_continua_identificavel():
    """O cenário novo de 2026: usuário adotou username, `wa_id` some.

    Hoje isto seria uma mensagem sem identidade nenhuma.
    """
    identity = extract_inbound_identity(
        {"id": "wamid.x", "user_id": BSUID},
        [{"profile": {"name": "Cliente", "username": "@cliente"}, "user_id": BSUID}],
    )

    assert identity.phone is None
    assert identity.bsuid == BSUID
    assert not identity.is_empty
    # E a busca começa pelo identificador estável.
    assert identity.lookup_keys[0] == ("bsuid", BSUID)


def test_mensagem_so_com_telefone_continua_funcionando():
    """Retrocompatibilidade: durante a convivência, muitos eventos virão assim."""
    identity = extract_inbound_identity({"id": "wamid.x", "from": PHONE}, [{"wa_id": PHONE}])

    assert identity.phone == PHONE
    assert identity.bsuid is None
    assert not identity.is_empty
    assert identity.lookup_keys == (("phone", PHONE),)


def test_bsuid_prevalece_como_primeira_chave_de_busca():
    identity = extract_inbound_identity(
        {"id": "wamid.x", "from": PHONE, "user_id": BSUID}, [{"wa_id": PHONE, "user_id": BSUID}]
    )

    # BSUID antes de telefone: o telefone pode ser ocultado depois, o BSUID não.
    assert identity.lookup_keys == (("bsuid", BSUID), ("phone", PHONE))


def test_contato_casado_por_bsuid_quando_nao_ha_telefone():
    identity = extract_inbound_identity(
        {"id": "wamid.x", "user_id": BSUID},
        [
            {"wa_id": "5561888880000", "user_id": "BR.99999999999999999999"},
            {"profile": {"name": "Certo"}, "user_id": BSUID},
        ],
    )

    assert identity.display_name == "Certo"
    assert identity.bsuid == BSUID


def test_bsuid_malformado_e_descartado_em_vez_de_propagado():
    """Valor alterado gera falha de identificação na Meta. Falhar aqui é melhor."""
    identity = extract_inbound_identity(
        {"id": "wamid.x", "from": PHONE, "user_id": "13491208655302741918"},
        [{"wa_id": PHONE, "user_id": "13491208655302741918"}],
    )

    assert identity.bsuid is None
    assert identity.phone == PHONE


def test_evento_sem_telefone_e_sem_bsuid_e_declarado_vazio():
    identity = extract_inbound_identity({"id": "wamid.x"}, [])

    assert identity.is_empty
    assert identity.lookup_keys == ()


def test_username_sozinho_nao_torna_o_contato_identificavel():
    """Username não é chave: ele muda e o vínculo se perde."""
    identity = extract_inbound_identity(
        {"id": "wamid.x"}, [{"profile": {"username": "@cliente"}}]
    )

    assert identity.username == "cliente"
    assert identity.is_empty


# ── Webhook de status ──────────────────────────────────────────────────────


def test_status_extrai_destinatario_com_nomes_proprios():
    identity = extract_status_identity(
        {
            "id": "wamid.x",
            "status": "delivered",
            "recipient_id": PHONE,
            "recipient_user_id": BSUID,
            "recipient_parent_user_id": PARENT_BSUID,
        }
    )

    assert identity.phone == PHONE
    assert identity.bsuid == BSUID
    assert identity.parent_bsuid == PARENT_BSUID


def test_status_de_grupo_nao_grava_id_de_grupo_como_telefone():
    """`recipient_id` pode ser um ID de grupo. Gravá-lo como telefone é sujeira
    que só aparece meses depois, numa campanha disparando para o lugar errado."""
    identity = extract_status_identity(
        {"id": "wamid.x", "status": "sent", "recipient_id": "120363042... @g.us"}
    )

    assert identity.phone is None
    assert identity.is_empty


def test_status_sem_telefone_usa_bsuid():
    identity = extract_status_identity(
        {"id": "wamid.x", "status": "read", "recipient_user_id": BSUID}
    )

    assert identity.phone is None
    assert identity.bsuid == BSUID
    assert not identity.is_empty


# ── Resolução de destino de envio ──────────────────────────────────────────


def test_telefone_tem_precedencia_sobre_bsuid():
    """Regra da própria Meta quando os dois são informados."""
    alvo = resolve_send_target(WhatsAppIdentityRef(phone=PHONE, bsuid=BSUID))

    assert alvo == SendTarget(kind="phone", value=PHONE)
    assert alvo.cloud_api_field == "to"


def test_sem_telefone_envia_por_bsuid_no_campo_recipient():
    alvo = resolve_send_target(WhatsAppIdentityRef(bsuid=BSUID))

    assert alvo == SendTarget(kind="bsuid", value=BSUID)
    # A documentação manda omitir `to` e usar `recipient` com o valor completo.
    assert alvo.cloud_api_field == "recipient"


def test_username_nunca_e_destino_de_envio():
    """Pelo `@` apenas o cliente encontra a empresa, nunca o contrário.

    É regra de plataforma. Campo de 'enviar para @' na interface seria um campo
    que nunca funciona.
    """
    alvo = resolve_send_target(WhatsAppIdentityRef(username="cliente"))

    assert alvo is None


def test_sem_destino_devolve_none_para_a_operacao_ver():
    """Mensagem que some em silêncio é cliente perdido sem ninguém saber."""
    assert resolve_send_target(WhatsAppIdentityRef()) is None
    assert resolve_send_target(WhatsAppIdentityRef(display_name="Fulano")) is None
