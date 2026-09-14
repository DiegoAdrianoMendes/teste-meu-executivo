"""Política central dos escopos da API externa."""

ALL_PUBLIC_API_SCOPES = (
    "contacts:read",
    "conversations:read",
    "leads:write",
    "matters:write",
    "notes:write",
    "templates:send",
    "campaigns:write",
)

MUTABLE_PUBLIC_API_SCOPES = frozenset(
    {
        "leads:write",
        "matters:write",
        "notes:write",
        "templates:send",
        "campaigns:write",
    }
)

# Estes escopos leem ou operam registros de leads já existentes.
SENSITIVE_PUBLIC_API_SCOPES = frozenset(
    {
        "contacts:read",
        "conversations:read",
        "matters:write",
        "notes:write",
        "templates:send",
        "campaigns:write",
    }
)


def has_sensitive_public_api_scope(scopes: list[str] | tuple[str, ...] | None) -> bool:
    return bool(set(scopes or ()) & SENSITIVE_PUBLIC_API_SCOPES)
