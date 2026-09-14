"""Canonical email type shared by public identity schemas."""
from typing import Annotated

from pydantic import BeforeValidator, EmailStr

from app.models.user import normalize_email

CanonicalEmail = Annotated[EmailStr, BeforeValidator(normalize_email)]
