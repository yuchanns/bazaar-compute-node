from __future__ import annotations

from .model import Migration

# every long value a page would put in a link, under a short number of its
# own: the table does not know what a value is, the code that put it there does
REFS_MIGRATION = Migration(
    version=2,
    name="refs",
    statements=(
        """
        CREATE TABLE refs (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL UNIQUE
        )
        """,
    ),
)
