"""Versioned deterministic token estimates for local AI preflight."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeterministicTokenEstimator:
    """Conservative UTF-8 estimator whose behavior is stable across processes."""

    version: str = "klm-utf8-bytes-per-3-v1"
    bytes_per_token: int = 3

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Token estimator version is required.")
        if self.bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be positive.")

    def count_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("Token estimates require text input.")
        if not text:
            return 0
        return math.ceil(len(text.encode("utf-8")) / self.bytes_per_token)
