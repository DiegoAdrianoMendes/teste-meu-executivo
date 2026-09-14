"""Provider-specific OSS census extractors."""

from pathlib import Path

from scripts.oss_census.extractors import (
    bottlecrm,
    chattermate,
    chatwoot,
    embedded_chat,
    rocketchat,
)
from scripts.oss_census.model import ExtractionResult

EXTRACTORS = {
    "chatwoot": chatwoot.extract,
    "chattermate": chattermate.extract,
    "bottlecrm": bottlecrm.extract,
    "rocketchat": rocketchat.extract,
    "rocket-embedded-chat": embedded_chat.extract,
}

SOURCE_PATTERNS = {
    "chatwoot": chatwoot.SOURCE_PATTERNS,
    "chattermate": chattermate.SOURCE_PATTERNS,
    "bottlecrm": bottlecrm.SOURCE_PATTERNS,
    "rocketchat": rocketchat.SOURCE_PATTERNS,
    "rocket-embedded-chat": embedded_chat.SOURCE_PATTERNS,
}


def extract_upstream(name: str, root: Path) -> ExtractionResult:
    try:
        extractor = EXTRACTORS[name]
    except KeyError as exc:
        raise ValueError(f"No OSS census extractor registered for {name!r}") from exc
    return extractor(root)


__all__ = ["EXTRACTORS", "SOURCE_PATTERNS", "extract_upstream"]
