"""Primitivas pequenas para fan-out dirigido por trabalho pendente."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

from app.core.logging import logger

DEFAULT_WORKSPACE_BATCH_SIZE = 64
MAX_WORKSPACE_BATCH_SIZE = 256


def bounded_batch_size(
    value: int | None,
    *,
    default: int = DEFAULT_WORKSPACE_BATCH_SIZE,
    maximum: int = MAX_WORKSPACE_BATCH_SIZE,
) -> int:
    """Normaliza limites vindos de tasks sem permitir fan-out ilimitado."""
    requested = default if value is None else int(value)
    return max(1, min(requested, maximum))


def unique_workspace_ids(values: Iterable[UUID | str], *, limit: int) -> list[UUID]:
    """Deduplica IDs mantendo a ordem e para assim que o lote estiver cheio."""
    workspace_ids: list[UUID] = []
    seen: set[UUID] = set()
    for value in values:
        workspace_id = UUID(str(value))
        if workspace_id in seen:
            continue
        seen.add(workspace_id)
        workspace_ids.append(workspace_id)
        if len(workspace_ids) >= limit:
            break
    return workspace_ids


@dataclass
class FanoutMetrics:
    """Métricas estruturadas comuns a cada ciclo de descoberta/execução."""

    event: str
    batch_limit: int
    discovery_rows: int = 0
    discovered_workspaces: int = 0
    enqueued_workspaces: int = 0
    processed_items: int = 0
    skipped_leased: int = 0
    publish_failures: int = 0
    _started_at: float = field(default_factory=monotonic, repr=False)

    def emit(self) -> None:
        duration_ms = round((monotonic() - self._started_at) * 1000, 2)
        logger.info(
            "worker.fanout",
            fanout_event=self.event,
            fanout_discovery_rows=self.discovery_rows,
            fanout_workspace_count=self.discovered_workspaces,
            fanout_enqueued_count=self.enqueued_workspaces,
            fanout_processed_count=self.processed_items,
            fanout_skipped_leased=self.skipped_leased,
            fanout_publish_failures=self.publish_failures,
            fanout_batch_limit=self.batch_limit,
            fanout_batch_saturated=(
                self.discovered_workspaces >= self.batch_limit
                or self.processed_items >= self.batch_limit
            ),
            fanout_amplification=round(
                self.enqueued_workspaces / max(self.discovery_rows, 1),
                4,
            ),
            fanout_duration_ms=duration_ms,
        )
