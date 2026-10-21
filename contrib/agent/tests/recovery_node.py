"""Run an isolated installed node until its parent requests a normal shutdown."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load
from blockbuster import blockbuster_ctx

from bazaar_contrib_agent import AGENTS, AgentInstances


async def run():
    path = Path(sys.argv[1])
    payload = json.loads(await asyncio.to_thread(path.read_text))
    os.environ["BCN_ENDPOINT"] = payload["endpoint"]
    composition = await asyncio.to_thread(
        load, [ContribSpec.model_validate(item) for item in payload["contribs"]]
    )
    services: list[AgentInstances] = []

    @asynccontextmanager
    async def observe(ctx: Context):
        services.append(ctx.accept(AGENTS))
        yield

    async def recorded(ctx: Context, event: AuditEvent):
        if event.event_name == "channel.inbound.persisted":
            await asyncio.to_thread(print, json.dumps({"persisted": True}), flush=True)

    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="application",
                    definition=Contrib(accepts=[AGENTS], lifespan=observe),
                ),
                ContribEntry(
                    name="persisted", definition=Contrib(listen={AUDIT_EVENT: recorded})
                ),
            )
        }
    )
    async with ContribHub() as hub, hub.enable(composition):
        await services[0].wait_applied()
        await asyncio.to_thread(print, json.dumps({"ready": True}), flush=True)
        await asyncio.to_thread(sys.stdin.readline)


if __name__ == "__main__":
    with blockbuster_ctx():
        asyncio.run(run())
