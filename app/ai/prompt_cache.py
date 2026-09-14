"""Provider-safe identifiers for reusable static prompt prefixes."""
from __future__ import annotations

import hashlib

from app.ai.models import ModelRequest, RouteCandidate

PROMPT_CACHE_CONTRACT_VERSION = "klm-prompt-cache-v1"


def provider_prompt_cache_key(request: ModelRequest, route: RouteCandidate) -> str:
    material = ":".join(
        (
            PROMPT_CACHE_CONTRACT_VERSION,
            request.workspace_id,
            request.agent_name,
            route.key,
            str(request.prompt_cache_key or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()
