"""Shared runtime-safe vocabulary for the pinned OSS FunctionUnit ledger."""

DISPOSITION_STATES = (
    "discovered",
    "mapped",
    "native_shadow",
    "native_primary",
    "native_verified",
    "blocked_proprietary",
    "blocked_license",
)

REQUIRED_UPSTREAMS = (
    "chatwoot",
    "chattermate",
    "bottlecrm",
    "rocketchat",
    "rocket-embedded-chat",
)

__all__ = ["DISPOSITION_STATES", "REQUIRED_UPSTREAMS"]
