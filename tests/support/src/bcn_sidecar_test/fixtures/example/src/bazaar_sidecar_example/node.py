"""Run the real application composition until the controlling pipe is closed."""

from __future__ import annotations

lazy import asyncio
lazy import sys

from bazaar_compute_node.boot import node
from bazaar_core import Stopping


async def main():
    async with node(configer_options=(sys.argv[1],)) as hub:
        errors = [
            f"{record.name}: {record.state.error}"
            for record in hub.snapshots
            if isinstance(record.state, Stopping) and record.state.error
        ]
        if errors:
            raise RuntimeError("; ".join(errors))
        print("ready", flush=True)
        await asyncio.to_thread(sys.stdin.readline)


if __name__ == "__main__":
    asyncio.run(main())
