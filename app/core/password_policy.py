"""Politica unica de senha para identidades humanas locais."""
from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator

PASSWORD_MIN_BYTES = 10
PASSWORD_MAX_BYTES = 72


def validate_password(value: str) -> str:
    """Valida o limite real aceito pelo bcrypt em bytes UTF-8."""
    if not isinstance(value, str):
        raise TypeError("A senha deve ser texto.")
    byte_length = len(value.encode("utf-8"))
    if byte_length < PASSWORD_MIN_BYTES or byte_length > PASSWORD_MAX_BYTES:
        raise ValueError(
            f"A senha deve ter entre {PASSWORD_MIN_BYTES} e "
            f"{PASSWORD_MAX_BYTES} bytes em UTF-8."
        )
    return value


PasswordValue = Annotated[str, AfterValidator(validate_password)]
