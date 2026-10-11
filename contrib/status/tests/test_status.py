from __future__ import annotations

lazy import asyncio
lazy import json
lazy from contextlib import asynccontextmanager
lazy from importlib.metadata import entry_points
lazy from pathlib import Path

import pytest
from bazaar_core import (
    CONTRIB_CHANGED,
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    ContribStatus,
    Failed,
    Running,
    ServiceKey,
)
lazy import aiofiles
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

from bazaar_contrib_status import CONTRIB_STATUS, ContribStatusQuery, status

pytestmark = pytest.mark.asyncio


async def test_entry_point_service_snapshots_real_task_failure_and_dependency_states(
    tmp_path: Path,
):
    document_key = ServiceKey[AsyncTextIOWrapper]("status.document")
    trigger = asyncio.Event()
    stopped = asyncio.Event()
    notified = asyncio.Event()
    changes: list[tuple[ContribStatus, ...]] = []
    services: list[ContribStatusQuery] = []
    path = tmp_path / "document.json"
    await asyncio.to_thread(path.write_text, "incomplete JSON")

    async def read():
        await trigger.wait()
        async with aiofiles.open(path, "r") as handle:
            json.loads(await handle.read())

    @asynccontextmanager
    async def reader(ctx: Context):
        try:
            async with (
                aiofiles.open(tmp_path / "journal.txt", "w") as handle,
                asyncio.TaskGroup() as tasks,
            ):
                ctx.offer(document_key, handle)
                tasks.create_task(read(), eager_start=True)
                try:
                    yield
                finally:
                    tasks.cancel()
        finally:
            stopped.set()

    @asynccontextmanager
    async def consumer(ctx: Context):
        handle = ctx.accept(document_key)
        try:
            yield
        finally:
            await handle.write("consumer released before its provider\n")
            await handle.flush()

    @asynccontextmanager
    async def inspect(ctx: Context):
        services.append(ctx.accept(CONTRIB_STATUS))
        yield

    def changed(ctx: Context, change: ContribChange):
        changes.append(ctx.accept(CONTRIB_STATUS).snapshot())
        if any(isinstance(item.state, Failed) for item in change.snapshots):
            notified.set()

    entries = await asyncio.to_thread(
        entry_points, group="bazaar_compute_node.contribs", name="status"
    )
    definition = await asyncio.to_thread(next(iter(entries)).load)
    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[document_key], lifespan=consumer),
                    ),
                    ContribEntry(
                        name="observer",
                        definition=Contrib(
                            accepts=[CONTRIB_STATUS],
                            lifespan=inspect,
                            listen={CONTRIB_CHANGED: changed},
                        ),
                    ),
                    ContribEntry(
                        name="reader",
                        definition=Contrib(offers=[document_key], lifespan=reader),
                    ),
                    ContribEntry(name="status", definition=definition),
                ]
            )
        ),
    ):
        service = services[0]
        before = next(item for item in service.snapshot() if item.name == "reader")
        trigger.set()
        await stopped.wait()
        await notified.wait()
        instances = {item.name: item for item in service.snapshot()}
        failed = instances["reader"]
        blocked = instances["consumer"]
        assert isinstance(failed.state, Failed)
        assert failed.state.error and "ExceptionGroup" in failed.state.error
        assert isinstance(blocked.state, Blocked)
        assert blocked.state.blocked_by == failed.id
        assert isinstance(before.state, Running)
        assert any(
            item.name == "reader" and isinstance(item.state, Failed)
            for snapshot in changes
            for item in snapshot
        )
    async with aiofiles.open(tmp_path / "journal.txt", "r") as handle:
        assert "consumer released before its provider" in await handle.read()


async def test_status_services_belong_to_their_hubs_and_serve_nested_contribs():
    first_services: dict[str, ContribStatusQuery] = {}
    second_services: dict[str, ContribStatusQuery] = {}

    def query(services: dict[str, ContribStatusQuery]) -> Contrib:
        @asynccontextmanager
        async def lifespan(ctx: Context):
            services[ctx.name] = ctx.accept(CONTRIB_STATUS)
            if ctx.name == "parent":
                async with ctx.enable(
                    Composition(
                        entries=[ContribEntry(name="child", definition=query(services))]
                    )
                ):
                    yield
            else:
                yield

        return Contrib(accepts=[CONTRIB_STATUS], lifespan=lifespan)

    async with (
        asyncio.timeout(3),
        ContribHub() as first,
        ContribHub() as second,
        first.enable(
            Composition(
                entries=[
                    ContribEntry(name="status", definition=status),
                    ContribEntry(name="parent", definition=query(first_services)),
                ]
            )
        ),
        second.enable(
            Composition(
                entries=[
                    ContribEntry(name="status", definition=status),
                    ContribEntry(name="parent", definition=query(second_services)),
                ]
            )
        ),
    ):
        assert first_services["parent"] is first_services["child"]
        assert second_services["parent"] is second_services["child"]
        for services in (first_services, second_services):
            records = {item.name: item for item in services["child"].snapshot()}
            assert records["child"].parent == records["parent"].id
        first_ids = {item.id for item in first_services["child"].snapshot()}
        second_ids = {item.id for item in second_services["child"].snapshot()}
        assert first_ids.isdisjoint(second_ids)
