from __future__ import annotations

from sqlalchemy import MetaData

import app.models  # noqa: F401
from app.db.alembic_metadata import (
    LEGACY_INDEX_METADATA_ALIGNMENT,
    align_published_index_names,
)
from app.db.base import Base


def test_published_index_alignment_is_complete_strict_and_idempotent() -> None:
    assert len(LEGACY_INDEX_METADATA_ALIGNMENT) == 84

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)

    align_published_index_names(metadata)
    align_published_index_names(metadata)

    for (table_name, metadata_name), (
        published_name,
        expected_columns,
        expected_unique,
    ) in LEGACY_INDEX_METADATA_ALIGNMENT.items():
        table = metadata.tables[table_name]
        by_name = {index.name: index for index in table.indexes}
        assert metadata_name not in by_name
        assert published_name in by_name
        published = by_name[published_name]
        assert tuple(column.name for column in published.columns) == expected_columns
        assert bool(published.unique) is expected_unique
