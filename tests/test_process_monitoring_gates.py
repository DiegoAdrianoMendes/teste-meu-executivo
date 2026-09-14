"""Gates separados do monitoramento processual: DJEN e DataJud.

São fontes independentes, com credencial e disponibilidade próprias. Abrir uma
nunca pode abrir a outra, e a chave do DataJud pertence ao gate, não ao lado
dele — mesmo formato de aws_private_storage_allowed(), que exige S3_KMS_KEY_ID.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "KLM_RUNTIME_MODE": "aws_private",
        "KLM_ONLINE_APPROVED": True,
        "KLM_EXTERNAL_ACTIONS_ENABLED": True,
    }
    base.update(overrides)
    return Settings(**base)


def test_gates_nascem_fechados() -> None:
    configured = _settings()
    assert configured.KLM_DJEN_LIVE_ENABLED is False
    assert configured.KLM_DATAJUD_LIVE_ENABLED is False


def test_boot_recusa_datajud_ligado_sem_chave() -> None:
    with pytest.raises(ValueError, match="DATAJUD_API_KEY"):
        _settings(KLM_DATAJUD_LIVE_ENABLED=True, DATAJUD_API_KEY="")


def test_boot_recusa_datajud_ligado_com_chave_em_branco() -> None:
    with pytest.raises(ValueError, match="DATAJUD_API_KEY"):
        _settings(KLM_DATAJUD_LIVE_ENABLED=True, DATAJUD_API_KEY="   ")


def test_djen_ligado_sozinho_nao_exige_chave_do_datajud() -> None:
    configured = _settings(KLM_DJEN_LIVE_ENABLED=True)
    assert configured.KLM_DJEN_LIVE_ENABLED is True
    assert configured.KLM_DATAJUD_LIVE_ENABLED is False


def test_gate_do_datajud_exige_chave_alem_da_flag(monkeypatch) -> None:
    from app.core import klm_runtime

    monkeypatch.setattr(klm_runtime, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        klm_runtime.settings, "KLM_DATAJUD_LIVE_ENABLED", True, raising=False
    )
    monkeypatch.setattr(klm_runtime.settings, "DATAJUD_API_KEY", "", raising=False)
    assert klm_runtime.datajud_live_delivery_allowed() is False

    monkeypatch.setattr(klm_runtime.settings, "DATAJUD_API_KEY", "chave-real", raising=False)
    assert klm_runtime.datajud_live_delivery_allowed() is True


def test_gates_sao_independentes(monkeypatch) -> None:
    from app.core import klm_runtime

    monkeypatch.setattr(klm_runtime, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(klm_runtime.settings, "KLM_DJEN_LIVE_ENABLED", True, raising=False)
    monkeypatch.setattr(
        klm_runtime.settings, "KLM_DATAJUD_LIVE_ENABLED", False, raising=False
    )
    monkeypatch.setattr(klm_runtime.settings, "DATAJUD_API_KEY", "chave-real", raising=False)

    assert klm_runtime.djen_live_delivery_allowed() is True
    assert klm_runtime.datajud_live_delivery_allowed() is False


def test_flag_aposentada_vira_aviso_e_nao_silencio(monkeypatch, capsys) -> None:
    # O logger do projeto é structlog e escreve em stdout, não no handler que
    # caplog intercepta. Por isso a asserção é sobre capsys.
    from app.core import klm_runtime

    monkeypatch.setenv("KLM_PROCESS_MONITORING_LIVE_ENABLED", "true")
    klm_runtime.warn_retired_flags()
    saida = capsys.readouterr().out
    assert "retired_flag_ignored" in saida
    assert "KLM_DJEN_LIVE_ENABLED" in saida
    assert "KLM_DATAJUD_LIVE_ENABLED" in saida


def test_sem_flag_aposentada_nao_avisa(monkeypatch, capsys) -> None:
    from app.core import klm_runtime

    monkeypatch.delenv("KLM_PROCESS_MONITORING_LIVE_ENABLED", raising=False)
    klm_runtime.warn_retired_flags()
    assert "retired_flag_ignored" not in capsys.readouterr().out


# ── relay do CNJ em sa-east-1 (S71): configuração, não gate ──


def test_relay_nasce_vazio_e_nao_abre_gate_nenhum() -> None:
    configured = _settings(
        KLM_CNJ_RELAY_FUNCTION_ARN=(
            "arn:aws:lambda:sa-east-1:123456789012:function:meu-executivo-production-cnj-relay"
        )
    )
    assert _settings().KLM_CNJ_RELAY_FUNCTION_ARN == ""
    assert configured.KLM_DJEN_LIVE_ENABLED is False
    assert configured.KLM_DATAJUD_LIVE_ENABLED is False


def test_boot_recusa_arn_de_relay_torto() -> None:
    with pytest.raises(ValueError, match="KLM_CNJ_RELAY_FUNCTION_ARN"):
        _settings(KLM_CNJ_RELAY_FUNCTION_ARN="meu-executivo-production-cnj-relay")
    with pytest.raises(ValueError, match="KLM_CNJ_RELAY_FUNCTION_ARN"):
        _settings(KLM_CNJ_RELAY_FUNCTION_ARN="arn:aws:iam::123456789012:role/x")
