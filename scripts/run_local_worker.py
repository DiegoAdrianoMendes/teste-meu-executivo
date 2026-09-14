"""Executa um worker Celery local com o mesmo isolamento do backend local."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from run_local import configure_local_environment


def main() -> None:
    parser = argparse.ArgumentParser(description="Executa o worker local do Meu Executivo.")
    parser.add_argument(
        "--database-path",
        type=Path,
        help="SQLite local isolado; deve ser o mesmo arquivo usado pelo backend local.",
    )
    parser.add_argument("--loglevel", default="INFO")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    configure_local_environment(
        root=root,
        host="127.0.0.1",
        port=8010,
        database_path=args.database_path,
    )

    from app.workers.celery_app import celery_app

    celery_app.worker_main(
        [
            "worker",
            "--pool=solo",
            "--concurrency=1",
            f"--loglevel={args.loglevel}",
        ]
    )


if __name__ == "__main__":
    main()
