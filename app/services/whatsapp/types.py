from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class WhatsAppSendResult:
    wamid: Optional[str]
    raw_status: str = "sent"
