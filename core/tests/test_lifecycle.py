from __future__ import annotations

lazy import asyncio
lazy import json
lazy import sqlite3
lazy from contextlib import AsyncExitStack, asynccontextmanager
lazy from contextvars import ContextVar
lazy from pathlib import Path

import pytest
lazy import aiofiles
lazy import aiofiles.os
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper
lazy from pydantic import BaseModel

from bazaar_core import (
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    Failed,
    Running,
    ServiceKey,
    TaskFailureSignal,
)

from .resources import (
    DB,
    MESSAGE_RECEIVED,
    MESSAGE_RECORDED,
    Database,
    Message,
    line_server,
    observe,
    recorder,
)

pytestmark = pytest.mark.asyncio


async def test_independent_service_makes_progress_during_another_service_startup(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
    lifespans: AsyncExitStack,
):
    line_key = ServiceKey[bytes]("line")
    ready = asyncio.Event()
    enabled = asyncio.Event()
    finish = asyncio.Event()
    lines: dict[str, bytes] = {}
    hub = await lifespans.enter_async_context(ContribHub())

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def fast(ctx: Context):
        database = await Database.open(tmp_path / "fast.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()

    @asynccontextmanager
    async def inspect(ctx: Context):
        connections["observe-db"] = ctx.accept(DB)
        ready.set()
        yield

    async with line_server() as (port, connected, release):

        @asynccontextmanager
        async def slow(ctx: Context):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, limit=4096
            )
            try:
                ctx.offer(line_key, await reader.readline())
                yield
            finally:
                writer.close()
                await writer.wait_closed()

        async def use():
            async with hub.enable(
                Composition(
                    entries=[
                        observe(line_key, lines),
                        ContribEntry(
                            name="inspect",
                            definition=Contrib(accepts=[DB], lifespan=inspect),
                        ),
                        ContribEntry(
                            name="slow",
                            definition=Contrib(lifespan=slow, offers=[line_key]),
                        ),
                        ContribEntry(
                            name="fast", definition=Contrib(lifespan=fast, offers=[DB])
                        ),
                    ]
                )
            ):
                enabled.set()
                await finish.wait()

        task = tasks.create_task(use())
        try:
            async with asyncio.timeout(3):
                await connected.wait()
                await ready.wait()
                database = connections["observe-db"]
                await database.insert("root", "progress while startup waits")
                assert (
                    "root",
                    "progress while startup waits",
                ) in await database.messages()
                release.set()
                await enabled.wait()
                assert b"ready" in lines["observe-line"]
                finish.set()
                await task
        finally:
            task.cancel()
            await task
            await hub.aclose()
    assert database.closed and database.worker.done()


async def test_shutdown_waits_for_sibling_callbacks_before_closing_database(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
    lifespans: AsyncExitStack,
):
    hub = await lifespans.enter_async_context(ContribHub())
    entered = asyncio.Event()
    progressed = asyncio.Event()

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "callbacks.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()

    async def slow(ctx: Context, value: Message):
        entered.set()
        await ctx.accept(DB).insert(ctx.name, value.text)
        await ctx.announce(MESSAGE_RECORDED, value)

    def fast(ctx: Context, value: Message):
        del ctx, value
        progressed.set()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            hub.enable(
                Composition(
                    entries=[
                        observe(DB, connections),
                        ContribEntry(
                            name="db",
                            definition=Contrib(offers=[DB], lifespan=database_lifespan),
                        ),
                        ContribEntry(
                            name="slow",
                            definition=Contrib(
                                accepts=[DB],
                                listen={MESSAGE_RECEIVED: slow},
                            ),
                        ),
                        ContribEntry(
                            name="fast",
                            definition=Contrib(listen={MESSAGE_RECEIVED: fast}),
                        ),
                    ],
                )
            )
        )
        alice = hub
        database = connections["observe-db"]
        database.progress = asyncio.Event()
        announcement = tasks.create_task(
            alice.announce(MESSAGE_RECEIVED, Message("finish the in-flight write"))
        )
        async with asyncio.timeout(3):
            await entered.wait()
            await progressed.wait()
            closing = tasks.create_task(hub.aclose())
            await asyncio.sleep(0)
            database.progress.set()
            await announcement
            await closing
    assert database.closed and database.worker.done()
    database = await Database.open(tmp_path / "callbacks.sqlite")
    try:
        assert any(agent == "slow" for agent, _ in await database.messages())
    finally:
        await database.aclose()
    await hub.aclose()


async def test_event_parse_error_keeps_other_listeners_running_and_can_be_corrected(
    tmp_path: Path,
    lifespans: AsyncExitStack,
):
    parsed: list[object] = []

    def parse(ctx: Context, value: Message):
        del ctx
        parsed.append(json.loads(value.text))

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "events.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()

    hub = await lifespans.enter_async_context(ContribHub())
    async with hub.enable(
        Composition(
            entries=[
                observe(DB, connections),
                ContribEntry(
                    name="db",
                    definition=Contrib(offers=[DB], lifespan=database_lifespan),
                ),
                ContribEntry(
                    name="parser",
                    definition=Contrib(listen={MESSAGE_RECEIVED: parse}),
                ),
                ContribEntry(name="recorder", definition=recorder),
            ],
        )
    ):
        alice = hub
        try:
            await alice.announce(MESSAGE_RECEIVED, Message("incomplete JSON"))
        except json.JSONDecodeError:
            pass
        database = connections["observe-db"]
        assert ("recorder", "incomplete JSON") in await database.messages()
        await alice.announce(MESSAGE_RECEIVED, Message('{"text": "corrected message"}'))
        assert any(
            isinstance(value, dict) and value.get("text") == "corrected message"
            for value in parsed
        )
    assert database.closed
    await hub.aclose()


async def test_startup_cancellation_releases_partial_resource_and_keeps_existing_enablement(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
    lifespans: AsyncExitStack,
):
    file_key = ServiceKey[AsyncTextIOWrapper]("journal")
    hub = await lifespans.enter_async_context(ContribHub())
    handles: list[AsyncTextIOWrapper] = []

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "existing.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()

    async with (
        hub.enable(
            Composition(
                entries=[
                    observe(DB, connections),
                    ContribEntry(
                        name="db",
                        definition=Contrib(offers=[DB], lifespan=database_lifespan),
                    ),
                ]
            )
        ),
        line_server() as (port, connected, release),
    ):

        @asynccontextmanager
        async def journal(ctx: Context):
            handle = await aiofiles.open(tmp_path / "partial.txt", "w")
            try:
                handles.append(handle)
                ctx.offer(file_key, handle)
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", port, limit=4096
                )
                try:
                    await reader.readline()
                    yield
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                await handle.close()

        async def use():
            async with hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="journal",
                            definition=Contrib(
                                lifespan=journal, offers=[file_key], accepts=[DB]
                            ),
                        )
                    ]
                )
            ):
                await asyncio.Event().wait()

        task = tasks.create_task(use())
        async with asyncio.timeout(3):
            await connected.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert task.cancelled()
        assert all(handle.closed for handle in handles)
        database = connections["observe-db"]
        await database.insert("root", "existing service remains usable")
        assert ("root", "existing service remains usable") in await database.messages()
        release.set()
    assert database.closed
    await hub.aclose()


async def test_real_startup_io_failure_rolls_back_and_repaired_configuration_runs(
    tmp_path: Path,
    lifespans: AsyncExitStack,
):
    class Config(BaseModel):
        path: Path

    file_key = ServiceKey[AsyncTextIOWrapper]("journal")
    handles: list[AsyncTextIOWrapper] = []
    databases: list[Database] = []
    directory = tmp_path / "directory"
    await aiofiles.os.mkdir(directory)

    connections: dict[str, Database] = {}

    journals: dict[str, AsyncTextIOWrapper] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "rollback.sqlite")
        databases.append(database)
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()

    @asynccontextmanager
    async def journal(ctx: Context, config: Config):
        async with aiofiles.open(tmp_path / "journal.txt", "w") as handle:
            handles.append(handle)
            ctx.offer(file_key, handle)
            async with aiofiles.open(config.path, "r") as source:
                await handle.write(await source.read())
            yield

    backend = Contrib(lifespan=database_lifespan, offers=[DB])
    sink = Contrib(config=Config, lifespan=journal, accepts=[DB], offers=[file_key])
    hub = await lifespans.enter_async_context(ContribHub())
    try:
        async with hub.enable(
            Composition(
                entries=[
                    observe(DB, connections),
                    ContribEntry(name="db", definition=backend),
                    observe(file_key, journals),
                    ContribEntry(
                        name="journal", definition=sink, config={"path": directory}
                    ),
                ]
            )
        ):
            pass
    except OSError:
        pass
    assert all(handle.closed for handle in handles)
    assert all(database.closed and database.worker.done() for database in databases)
    source = tmp_path / "source.txt"
    await asyncio.to_thread(source.write_text, "restored startup input")
    async with hub.enable(
        Composition(
            entries=[
                observe(file_key, journals),
                ContribEntry(name="journal", definition=sink, config={"path": source}),
                observe(DB, connections),
                ContribEntry(name="db", definition=backend),
            ]
        )
    ):
        await connections["observe-db"].insert("root", "repaired startup")
        await journals["observe-journal"].flush()
        assert "restored" in await asyncio.to_thread(
            (tmp_path / "journal.txt").read_text
        )
    await hub.aclose()


async def test_lifespan_task_owns_contextvars_and_taskgroup_through_exit(
    tmp_path: Path,
    lifespans: AsyncExitStack,
):
    owner: ContextVar[str] = ContextVar("owner")
    stop = asyncio.Event()
    file_key = ServiceKey[AsyncTextIOWrapper]("journal")
    joined = asyncio.Event()
    hub = await lifespans.enter_async_context(ContribHub())

    journals: dict[str, AsyncTextIOWrapper] = {}

    @asynccontextmanager
    async def journal(ctx: Context):
        token = owner.set(str(ctx.id))
        try:
            async with aiofiles.open(tmp_path / "owned.txt", "w") as handle:

                async def write():
                    try:
                        await stop.wait()
                        await handle.write(f"finished in {owner.get()}\n")
                    finally:
                        joined.set()

                async with asyncio.TaskGroup() as group:
                    group.create_task(write())
                    ctx.offer(file_key, handle)
                    try:
                        yield
                    finally:
                        stop.set()
        finally:
            owner.reset(token)

    async with hub.enable(
        Composition(
            entries=[
                observe(file_key, journals),
                ContribEntry(
                    name="journal",
                    definition=Contrib(lifespan=journal, offers=[file_key]),
                ),
            ]
        )
    ):
        handle = journals["observe-journal"]
        await handle.write("started\n")
    assert joined.is_set() and handle.closed
    assert "finished in " in await asyncio.to_thread((tmp_path / "owned.txt").read_text)
    await hub.aclose()


async def test_cleanup_error_releases_remaining_resources_and_allows_new_enablement(
    tmp_path: Path,
    lifespans: AsyncExitStack,
):
    file_key = ServiceKey[AsyncTextIOWrapper]("journal")

    connections: dict[str, Database] = {}

    journals: dict[str, AsyncTextIOWrapper] = {}

    @asynccontextmanager
    async def journal(ctx: Context):
        handle = await aiofiles.open(tmp_path / "journal.txt", "w")
        ctx.offer(file_key, handle)
        try:
            yield
        finally:
            try:
                await handle.write("shutdown complete\n")
            finally:
                await handle.close()

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "cleanup.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()

    entries = [
        observe(DB, connections),
        ContribEntry(
            name="db", definition=Contrib(lifespan=database_lifespan, offers=[DB])
        ),
        observe(file_key, journals),
        ContribEntry(
            name="journal",
            definition=Contrib(lifespan=journal, offers=[file_key], accepts=[DB]),
        ),
    ]
    hub = await lifespans.enter_async_context(ContribHub())
    async with AsyncExitStack() as stack:
        enablement = await stack.enter_async_context(
            hub.enable(Composition(entries=entries))
        )
        database = connections["observe-db"]
        await journals["observe-journal"].close()
        try:
            await enablement.aclose()
        except ExceptionGroup:
            pass
        assert database.closed and database.worker.done()
        async with hub.enable(Composition(entries=entries)):
            await connections["observe-db"].insert(
                "root", "resources recovered after exit failure"
            )
    assert "shutdown complete" in await asyncio.to_thread(
        (tmp_path / "journal.txt").read_text
    )
    await hub.aclose()


async def test_fault_cleanup_preserves_original_failure_and_dependency_status(
    tmp_path: Path,
    lifespans: AsyncExitStack,
):
    stopping = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()
    databases: list[Database] = []
    journals: list[AsyncTextIOWrapper] = []

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "failed-worker.sqlite")
        databases.append(database)
        handle = await aiofiles.open(tmp_path / "shutdown.txt", "w")
        journals.append(handle)
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            try:
                await handle.write("database stopped\n")
            finally:
                try:
                    await database.aclose()
                finally:
                    await handle.close()
                    stopped.set()

    @asynccontextmanager
    async def consumer(ctx: Context):
        await ctx.accept(DB).messages()
        try:
            yield
        finally:
            stopping.set()
            await release.wait()

    hub = await lifespans.enter_async_context(ContribHub())
    errors: list[BaseException] = []
    try:
        async with (
            asyncio.timeout(3),
            hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="db",
                            definition=Contrib(offers=[DB], lifespan=database_lifespan),
                        ),
                        ContribEntry(
                            name="consumer",
                            definition=Contrib(accepts=[DB], lifespan=consumer),
                        ),
                    ]
                )
            ),
        ):
            before = next(item for item in hub.snapshots if item.name == "db")
            database = databases[0]
            await journals[0].close()
            async with database.connection.execute("DROP TABLE messages"):
                pass
            await database.connection.commit()
            try:
                await database.insert("root", "worker failure")
            except sqlite3.OperationalError:
                pass
            await stopping.wait()
            during = {item.name: item for item in hub.snapshots}
            assert isinstance(during["db"].state, Failed)
            assert isinstance(during["consumer"].state, Blocked)
            assert during["consumer"].state.blocked_by == during["db"].id
            release.set()
            await stopped.wait()
            after = {item.name: item for item in hub.snapshots}
            assert isinstance(after["db"].state, Failed)
            assert isinstance(after["consumer"].state, Blocked)
            assert after["db"].state.phase == "stopped"
            assert after["consumer"].state.phase == "stopped"
            assert (
                after["db"].state.error
                and "OperationalError" in after["db"].state.error
            )
            assert (
                after["consumer"].state.error
                and "OperationalError" in after["consumer"].state.error
            )
            assert after["consumer"].state.blocked_by == after["db"].id
            assert (
                after["db"].state.exit_error
                and "ValueError" in after["db"].state.exit_error
            )
            assert isinstance(before.state, Running)
            assert database.closed and database.worker.done() and journals[0].closed
    except ExceptionGroup as error:
        errors.extend(error.exceptions)
    assert any(isinstance(error, ValueError) for error in errors)
    await hub.aclose()


async def test_shutdown_cancellation_unwinds_callbacks_before_releasing_dependencies(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
    lifespans: AsyncExitStack,
):
    entered = asyncio.Event()
    callback_finished = asyncio.Event()
    database_closed = asyncio.Event()
    hub = await lifespans.enter_async_context(ContribHub())

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "canceled-shutdown.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()
            database_closed.set()

    async def write(ctx: Context, value: Message):
        entered.set()
        try:
            await ctx.accept(DB).insert(ctx.name, value.text)
        finally:
            await ctx.accept(DB).insert(ctx.name, "callback cleanup completed")
            callback_finished.set()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            hub.enable(
                Composition(
                    entries=[
                        observe(DB, connections),
                        ContribEntry(
                            name="db",
                            definition=Contrib(offers=[DB], lifespan=database_lifespan),
                        ),
                        ContribEntry(
                            name="writer",
                            definition=Contrib(
                                accepts=[DB],
                                listen={MESSAGE_RECEIVED: write},
                            ),
                        ),
                    ],
                )
            )
        )
        alice = hub
        database = connections["observe-db"]
        database.progress = asyncio.Event()
        announcement = tasks.create_task(
            alice.announce(MESSAGE_RECEIVED, Message("waiting for processing"))
        )
        async with asyncio.timeout(3):
            await entered.wait()
            closing = tasks.create_task(hub.aclose())
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            database.progress.set()
            try:
                await closing
            except asyncio.CancelledError:
                pass
            await announcement
        assert closing.cancelled()
        assert callback_finished.is_set() and database_closed.is_set()
        assert database.closed and database.worker.done()
    database = await Database.open(tmp_path / "canceled-shutdown.sqlite")
    try:
        assert any(
            text == "callback cleanup completed"
            for _, text in await database.messages()
        )
    finally:
        await database.aclose()
    await hub.aclose()


async def test_concurrent_hub_parent_and_child_close_finish_listener_cleanup(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
    lifespans: AsyncExitStack,
):
    entered = asyncio.Event()
    finished = asyncio.Event()
    children = []
    databases: list[Database] = []
    hub = await lifespans.enter_async_context(ContribHub())
    path = tmp_path / "shared-shutdown.sqlite"

    async def write(ctx: Context, value: Message):
        entered.set()
        try:
            await ctx.accept(DB).insert(ctx.name, value.text)
        finally:
            await ctx.accept(DB).insert(ctx.name, "listener cleanup finished")
            finished.set()

    @asynccontextmanager
    async def parent(ctx: Context):
        database = await Database.open(path)
        databases.append(database)
        try:
            await database.start()
            ctx.offer(DB, database)
            async with ctx.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="writer",
                            definition=Contrib(
                                accepts=[DB], listen={MESSAGE_RECEIVED: write}
                            ),
                        )
                    ]
                )
            ) as child:
                children.append(child)
                yield
        finally:
            await database.aclose()

    async with hub.enable(
        Composition(
            entries=[
                ContribEntry(
                    name="parent",
                    definition=Contrib(offers=[DB], lifespan=parent),
                )
            ]
        )
    ) as owner:
        database = databases[0]
        database.progress.clear()
        announcement = tasks.create_task(
            hub.announce(MESSAGE_RECEIVED, Message("processing during shutdown"))
        )
        async with asyncio.timeout(3):
            await entered.wait()
            closing = tasks.create_task(hub.aclose(), eager_start=True)
            child = tasks.create_task(children[0].aclose(), eager_start=True)
            owning = tasks.create_task(owner.aclose(), eager_start=True)
            child.cancel()
            await asyncio.sleep(0)
            child.cancel()
            database.progress.set()
            with pytest.raises(asyncio.CancelledError):
                await child
            await announcement
            await closing
            await owning
        assert finished.is_set() and database.closed and database.worker.done()
        await children[0].aclose()
        await owner.aclose()
        await hub.aclose()
    database = await Database.open(path)
    try:
        assert any(
            text == "listener cleanup finished" for _, text in await database.messages()
        )
    finally:
        await database.aclose()


async def test_failure_signal_retains_disabled_fault_and_resets_for_repaired_file_work(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
):
    signal = TaskFailureSignal()
    source = tmp_path / "input.txt"
    destination = tmp_path / "output.txt"

    async def copy():
        async with (
            aiofiles.open(source, "r") as reader,
            aiofiles.open(destination, "w") as writer,
        ):
            await writer.write(await reader.read())

    signal.reset()
    await signal.observe(copy(), component="file worker")
    signal.disable()
    failures: list[BaseException] = []

    async def receive():
        try:
            await signal.wait()
        except Exception as error:  # noqa: BLE001 - Record the resource's reported failure.
            failures.append(error)

    async with asyncio.timeout(3):
        await receive()
    assert any(isinstance(error, FileNotFoundError) for error in failures)
    async with aiofiles.open(source, "w") as writer:
        await writer.write("repaired file work")
    signal.reset()
    waiting = tasks.create_task(receive(), eager_start=True)
    await signal.observe(copy(), component="repaired file worker")
    signal.disable()
    async with asyncio.timeout(3):
        await waiting
    async with aiofiles.open(destination, "r") as reader:
        assert "repaired file work" in await reader.read()
    assert any(
        isinstance(error, RuntimeError) and "repaired file worker" in str(error)
        for error in failures
    )
