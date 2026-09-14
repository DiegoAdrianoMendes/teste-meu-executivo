"""OAB canônica `N/UF`: entrada suja canoniza, letra recusa, UF confere."""
from __future__ import annotations

import pytest

from app.services.oab_numero import InvalidOabError, normalizar_oab


@pytest.mark.parametrize(
    ("entrada", "canonica"),
    [
        ("68812/DF", "68812/DF"),
        ("OAB/DF 123.456", "123456/DF"),
        ("123456-df", "123456/DF"),
        ("123456 DF", "123456/DF"),
        ("DF 123456", "123456/DF"),
        ("SP123456", "123456/SP"),
        ("123456SP", "123456/SP"),
        ("OAB/DF nº 123.456", "123456/DF"),
        ("OAB/DF n. 123456", "123456/DF"),
        ("068812/DF", "68812/DF"),  # a API tolera o zero, a canônica não o guarda
        ("OAB/SP 123456 SP", "123456/SP"),
        ("OAB/DF n.º 123.456", "123456/DF"),  # "nº" só vale como prefixo do número
        ("OAB/DF n 123456", "123456/DF"),
        ("n123456 DF", "123456/DF"),
        # espaço colapsa antes do corte de 40 caracteres
        ("OAB/DF" + " " * 20 + "nº" + " " * 10 + "123.456", "123456/DF"),
    ],
)
def test_canoniza_entrada_suja(entrada: str, canonica: str) -> None:
    assert normalizar_oab(entrada) == canonica


@pytest.mark.parametrize(
    ("entrada", "trecho"),
    [
        ("123456A", "letra"),  # o DJEN devolve vazio em silêncio para inscrição com letra
        ("123456/DF-A", "letra"),
        ("123456 A/DF", "letra"),
        ("OAB/SP 123456 RJ", "uma só"),
        ("0000000/DF", "inválido"),
        ("12345 6/DF", "único número"),
        ("12345678/DF", "7 dígitos"),
        ("", "formato"),
        ("123456", "UF"),
        ("123456/XX", "UF desconhecida"),
        ("123456N/DF", "letra"),  # N fora da posição de prefixo é letra de inscrição
        ("123456 N DF", "letra"),
        ("123456NO/DF", "letra"),
        ("123456/D", "incompleta"),
        ("123456/DFF", "incompleta"),
        ("１２３４５６/DF", "não reconhecidos"),  # dígito fullwidth não é inscrição
        ("OAB/DF " + "x" * 40, "longa demais"),
    ],
)
def test_recusa_com_mensagem_para_o_usuario(entrada: str, trecho: str) -> None:
    with pytest.raises(InvalidOabError) as capturado:
        normalizar_oab(entrada)
    assert trecho in str(capturado.value)
