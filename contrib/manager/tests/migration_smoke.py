"""Run the migrated configuration using only its restored installed dependencies."""

from __future__ import annotations

import asyncio
import sys

from bazaar_compute_node.boot import node
from bazaar_core import Running


async def exercise() -> None:
    async with node(configer_options=(sys.argv[1],)) as hub:
        assert any(
            record.name == "example-file" and isinstance(record.state, Running)
            for record in hub.snapshots
        )


asyncio.run(exercise())
