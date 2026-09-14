from __future__ import annotations

import re
from pathlib import Path

from app.services.whatsapp.media_upload import MIB, resolve_media_policy

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_frontend_proxy_accepts_backend_document_limit_plus_multipart_overhead():
    nginx_source = (PROJECT_ROOT / "docker/frontend/nginx.conf").read_text(
        encoding="utf-8"
    )
    match = re.search(r"client_max_body_size\s+(\d+)m\s*;", nginx_source)
    assert match is not None
    proxy_limit = int(match.group(1)) * MIB
    backend_limit = resolve_media_policy("application/pdf").max_size_bytes
    assert proxy_limit >= backend_limit + 5 * MIB
