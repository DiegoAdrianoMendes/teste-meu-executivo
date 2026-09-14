#!/usr/bin/env python3
"""Rotaciona a chave de cifra dos segredos de conexão WhatsApp (A4/W1.9 do
Plano Meta Máximo).

Re-cifra `access_token_encrypted`, `business_token_encrypted`,
`instance_token_encrypted` e `registration_pin_encrypted` de toda
`WhatsAppConnection` com a chave ATIVA (primeira de
`WHATSAPP_SECRET_ENCRYPTION_KEYS`, ou a legada `WHATSAPP_SECRET_ENCRYPTION_KEY`
quando a lista não estiver configurada). Segredos já cifrados com a chave
ativa são pulados; segredos que falharem a decifragem com TODAS as chaves
configuradas são reportados em `failed` e não são tocados.

Uso:
    .venv/bin/python scripts/rotate_whatsapp_secrets.py                # dry-run
    .venv/bin/python scripts/rotate_whatsapp_secrets.py --execute      # roda de fato

Dry-run é o padrão por segurança: só imprime o relatório. É preciso passar
`--execute` explicitamente para persistir a re-cifragem.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.whatsapp.secrets import re_encrypt_all  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Persiste a re-cifragem. Sem esta flag, roda em dry-run (padrão).",
    )
    return parser


async def _run(execute: bool) -> dict:
    async with AsyncSessionLocal() as db:
        report = await re_encrypt_all(db, dry_run=not execute)
    return report


def main() -> None:
    args = _parser().parse_args()
    report = asyncio.run(_run(args.execute))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["failed"]:
        print(
            f"\nATENÇÃO: {len(report['failed'])} segredo(s) não puderam ser decifrados "
            "com nenhuma chave configurada. Eles NÃO foram tocados; veja `failed` acima.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not args.execute and report["rotated"]:
        print(
            f"\nDry-run: {report['rotated']} conexão(ões) seriam re-cifradas. "
            "Rode com --execute para persistir.",
        )


if __name__ == "__main__":
    main()
