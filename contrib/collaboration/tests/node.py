"""Run the real application composition until the controlling pipe is closed."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy import sys
lazy from pathlib import Path

from bazaar_compute_node.boot import node
from bazaar_core import Composition, Context, Contrib, ContribEntry, Stopping

from bazaar_contrib_collaboration import CONTACT_REVIEWED, ContactReviewed


async def main():
    path = Path(sys.argv[1]).parent / "review-events.jsonl"

    async def reviewed(ctx: Context, event: ContactReviewed):
        def record():
            with path.open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {
                            "agent": event.agent.id,
                            "thread": event.thread.id,
                            "review": event.review.value,
                        }
                    )
                    + "\n"
                )

        await asyncio.to_thread(record)

    async with node(configer_options=(sys.argv[1],)) as hub:
        errors = [
            f"{record.name}: {record.state.error}"
            for record in hub.snapshots
            if isinstance(record.state, Stopping) and record.state.error
        ]
        if errors:
            raise RuntimeError("; ".join(errors))
        async with hub.enable(
            Composition(
                entries=(
                    ContribEntry(
                        name="review-observer",
                        definition=Contrib(listen={CONTACT_REVIEWED: reviewed}),
                    ),
                )
            )
        ):
            print("ready", flush=True)
            await asyncio.to_thread(sys.stdin.readline)


if __name__ == "__main__":
    asyncio.run(main())
