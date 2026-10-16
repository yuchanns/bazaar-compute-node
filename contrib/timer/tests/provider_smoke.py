"""Exercise an installed third-party provider using only public contracts."""

import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import entry_points

from bazaar_core import Composition, Context, Contrib, ContribEntry, ContribHub

from bazaar_contrib_timer import TIMER, Timer, TimerCancelledError, TimerClosedError


async def main():
    services: list[Timer] = []

    @asynccontextmanager
    async def consumer(ctx: Context):
        services.append(ctx.accept(TIMER))
        yield

    entries = entry_points(group="bazaar_compute_node.contribs", name="asyncio-timer")
    definition = next(iter(entries)).load()
    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="asyncio", definition=definition),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[TIMER], lifespan=consumer),
                    ),
                ],
                roots=("consumer",),
            )
        ),
    ):
        service = services[0]
        handle = service.create(60_000)
        waiter = asyncio.create_task(handle.wait(), eager_start=True)
        handle.reset(20)
        await waiter
        handle = service.create(60_000)
        handle.cancel()
        try:
            await handle.wait()
        except TimerCancelledError:
            pass
        else:
            raise RuntimeError("third-party provider did not cancel its timer")
        handle = service.create(60_000)
        waiter = asyncio.create_task(handle.wait(), eager_start=True)
    try:
        await waiter
    except TimerClosedError:
        return
    raise RuntimeError("third-party provider did not close its pending timer")


if __name__ == "__main__":
    asyncio.run(main())
