#!/usr/bin/env python3
"""Backfill CTWA histórico (T1.2 do Plano Meta Máximo).

Varre `MessageEvidence` já persistido e recupera `AcquisitionEvent`s de
`conversation_started` para mensagens WhatsApp antigas que já tinham vindo de
um clique em anúncio (CTWA) mas foram recebidas antes da captura ao vivo
(sprint F2.1) existir. Read-only sobre a evidência; a única escrita é
`AcquisitionEvent` (idempotente) + `thread_metadata.acquisition`.

Uso:
    .venv/bin/python scripts/backfill_ctwa.py                      # dry-run, todos os workspaces
    .venv/bin/python scripts/backfill_ctwa.py --workspace-id <id>  # dry-run, 1 workspace
    .venv/bin/python scripts/backfill_ctwa.py --execute            # roda de fato

Dry-run é o padrão por segurança: só imprime o relatório. É preciso passar
`--execute` explicitamente para persistir os eventos.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.whatsapp.ctwa_backfill_service import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    CtwaBackfillService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace-id",
        type=UUID,
        default=None,
        help="Restringe o backfill a um único workspace. Sem isso, varre todos.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Persiste os eventos de fato. Sem esta flag o comando roda em dry-run.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Tamanho do lote de leitura de evidência (padrão {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Limite de lotes processados nesta execução (padrão: sem limite).",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    dry_run = not args.execute
    async with AsyncSessionLocal() as db:
        service = CtwaBackfillService(db)
        report = await service.run(
            workspace_id=args.workspace_id,
            dry_run=dry_run,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = _parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
