from __future__ import annotations

lazy import asyncio
lazy import json
lazy import sqlite3
lazy from contextlib import asynccontextmanager
lazy from pathlib import Path

import pytest
lazy import aiofiles
lazy import aiosqlite
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

from bazaar_core import (
    CONTRIB_CHANGED,
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    Failed,
    Running,
    ServiceKey,
)

from .resources import (
    DB,
    MESSAGE_RECEIVED,
    Database,
    Message,
    observe,
    recorder,
)

pytestmark = pytest.mark.asyncio


async def test_startup_failure_blocks_dependencies_and_allows_independent_child_startup(
    tmp_path: Path,
):
    document_key = ServiceKey[AsyncTextIOWrapper]("document")

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def document(ctx: Context):
        async with aiofiles.open(tmp_path, "r") as handle:
            ctx.offer(document_key, handle)
            yield

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        resource = await Database.open(tmp_path / "independent.sqlite")
        try:
            await resource.start()
            ctx.offer(DB, resource)
            yield
        finally:
            await resource.aclose()

    async def page(ctx: Context, value: Message):
        del value
        database = ctx.accept(DB)
        for status in ctx.snapshots:
            if isinstance(status.state, Failed):
                await database.insert(ctx.name, f"{status.name}: failed")

    hub = ContribHub()
    candidate = hub.validate(
        Composition(
            entries=[
                ContribEntry(
                    name="document",
                    definition=Contrib(offers=[document_key], lifespan=document),
                ),
                ContribEntry(
                    name="document-consumer", definition=Contrib(accepts=[document_key])
                ),
                observe(DB, connections),
                ContribEntry(
                    name="agent-db",
                    definition=Contrib(offers=[DB], lifespan=database_lifespan),
                ),
                ContribEntry(name="recorder", definition=recorder),
                ContribEntry(
                    name="page",
                    definition=Contrib(
                        accepts=[DB],
                        listen={MESSAGE_RECEIVED: page},
                    ),
                ),
            ],
        )
    )
    async with asyncio.timeout(3), hub:
        async with hub.enable(candidate):
            instances = {instance.name: instance for instance in hub.snapshots}
            failed = instances["document"]
            assert isinstance(failed.state, Failed)
            assert failed.state.error and "Error" in failed.state.error
            blocked = instances["document-consumer"]
            assert isinstance(blocked.state, Blocked)
            assert blocked.state.blocked_by == failed.id
            alice = hub
            await alice.announce(MESSAGE_RECEIVED, Message("startup continues"))
            database = connections["observe-db"]
            assert ("recorder", "startup continues") in await database.messages()
            assert ("page", "document: failed") in await database.messages()
        assert database.closed


async def test_cross_enablement_dependencies_stop_and_notify_plugins(
    tmp_path: Path,
):
    connection_key = ServiceKey[aiosqlite.Connection]("database.connection")
    journal_key = ServiceKey[AsyncTextIOWrapper]("status.journal")
    stopped = asyncio.Event()
    dependent_stopped = asyncio.Event()
    notified = asyncio.Event()
    invalid = tmp_path / "observer.json"
    await asyncio.to_thread(invalid.write_text, "incomplete JSON")

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        resource = await Database.open(tmp_path / "fault.sqlite")
        try:
            await resource.start()
            ctx.offer(DB, resource)
            yield
        finally:
            await resource.aclose()
            stopped.set()

    @asynccontextmanager
    async def connection(ctx: Context):
        ctx.offer(connection_key, ctx.accept(DB).connection)
        yield

    @asynccontextmanager
    async def consumer(ctx: Context):
        resource = ctx.accept(connection_key)
        try:
            yield
        finally:
            async with resource.execute("SELECT 1") as cursor:
                await cursor.fetchone()
            async with aiofiles.open(tmp_path / "consumer.txt", "w") as handle:
                await handle.write("consumer released before connection\n")
            dependent_stopped.set()

    @asynccontextmanager
    async def page(ctx: Context):
        async with aiofiles.open(tmp_path / "status.jsonl", "w") as handle:
            ctx.offer(journal_key, handle)
            yield

    async def changed(ctx: Context, change: ContribChange):
        value = [
            {
                "name": item.name,
                "id": str(item.id),
                "state": type(item.state).__name__.lower(),
            }
            for item in change.snapshots
        ]
        handle = ctx.accept(journal_key)
        await handle.write(json.dumps(value) + "\n")
        await handle.flush()
        if any(isinstance(item.state, Failed) for item in change.snapshots):
            notified.set()

    async def broken_observer(ctx: Context, change: ContribChange):
        del ctx, change
        async with aiofiles.open(invalid, "r") as handle:
            json.loads(await handle.read())

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
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
    ):
        async with (
            hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="connection",
                            definition=Contrib(
                                accepts=[DB],
                                offers=[connection_key],
                                lifespan=connection,
                            ),
                        )
                    ]
                )
            ),
            hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="consumer",
                            definition=Contrib(
                                accepts=[connection_key], lifespan=consumer
                            ),
                        ),
                        ContribEntry(
                            name="page",
                            definition=Contrib(
                                offers=[journal_key],
                                lifespan=page,
                                listen={CONTRIB_CHANGED: changed},
                            ),
                        ),
                        ContribEntry(
                            name="broken-observer",
                            definition=Contrib(
                                listen={CONTRIB_CHANGED: broken_observer}
                            ),
                        ),
                    ]
                )
            ),
        ):
            database = connections["observe-db"]
            async with database.connection.execute("DROP TABLE messages"):
                pass
            await database.connection.commit()
            with pytest.raises(sqlite3.OperationalError):
                await database.insert("root", "broken schema")
            await dependent_stopped.wait()
            await stopped.wait()
            await notified.wait()
            instances = {instance.name: instance for instance in hub.snapshots}
            failed = instances["db"]
            for name in ("connection", "consumer"):
                blocked = instances[name]
                assert isinstance(blocked.state, Blocked)
                assert blocked.state.blocked_by == failed.id
                assert blocked.state.error and "OperationalError" in blocked.state.error
            assert isinstance(instances["page"].state, Running)
        async with aiofiles.open(tmp_path / "status.jsonl", "r") as handle:
            rows = [json.loads(line) for line in (await handle.read()).splitlines()]
        assert any(
            item["name"] == "db" and item["state"] == "failed"
            for row in rows
            for item in row
        )


async def test_same_definition_fault_stops_only_its_local_database_and_consumers(
    tmp_path: Path,
):

    from pydantic import BaseModel

    stopped = asyncio.Event()
    connections: dict[str, Database] = {}
    contexts: dict[str, Context] = {}

    class Config(BaseModel):
        name: str

    @asynccontextmanager
    async def database_lifespan(ctx: Context, config: Config):
        resource = await Database.open(tmp_path / f"{config.name}.sqlite")
        connections[config.name] = resource
        try:
            await resource.start()
            ctx.offer(DB, resource)
            yield
        finally:
            await resource.aclose()
            if config.name == "alice":
                stopped.set()

    @asynccontextmanager
    async def recording(ctx: Context):
        contexts[ctx.name] = ctx
        yield

    from .resources import record

    provider = Contrib(config=Config, offers=[DB], lifespan=database_lifespan)
    consumer = Contrib(
        accepts=[DB], lifespan=recording, listen={MESSAGE_RECEIVED: record}
    )
    entries = []
    for name in ("alice", "bob"):
        database = ContribEntry(
            name=f"{name}-db",
            definition=provider,
            config={"name": name},
            local=(DB, MESSAGE_RECEIVED),
        )
        entries.extend(
            [
                database,
                ContribEntry(
                    name=name,
                    definition=consumer,
                    bindings={
                        DB: database.ref(DB),
                        MESSAGE_RECEIVED: database.ref(MESSAGE_RECEIVED),
                    },
                ),
            ]
        )
    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(Composition(entries=entries)),
    ):
        database = connections["alice"]
        async with database.connection.execute("DROP TABLE messages"):
            pass
        await database.connection.commit()
        with pytest.raises(sqlite3.OperationalError):
            await database.insert("alice", "broken local database")
        await stopped.wait()
        instances = {item.name: item for item in hub.snapshots}
        assert isinstance(instances["alice-db"].state, Failed)
        assert isinstance(instances["alice"].state, Blocked)
        assert instances["alice"].state.blocked_by == instances["alice-db"].id
        await contexts["bob"].announce(MESSAGE_RECEIVED, Message("bob continues"))
        assert ("bob", "bob continues") in await connections["bob"].messages()


async def test_lifespan_task_group_failure_does_not_escape_to_the_hub(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
):
    trigger = asyncio.Event()
    stopped = asyncio.Event()
    active = asyncio.Event()
    document_key = ServiceKey[AsyncTextIOWrapper]("reader.journal")
    path = tmp_path / "document.json"
    await asyncio.to_thread(path.write_text, "incomplete JSON")

    async def read():
        await trigger.wait()
        async with aiofiles.open(path, "r") as handle:
            json.loads(await handle.read())

    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def reader(ctx: Context):
        try:
            async with (
                aiofiles.open(tmp_path / "reader.txt", "w") as handle,
                asyncio.TaskGroup() as group,
            ):
                ctx.offer(document_key, handle)
                group.create_task(read(), name="document reader", eager_start=True)
                try:
                    yield
                finally:
                    group.cancel()
        finally:
            stopped.set()

    @asynccontextmanager
    async def consumer(ctx: Context):
        handle = ctx.accept(document_key)
        try:
            yield
        finally:
            await handle.write("consumer cleanup\n")
            await handle.flush()

    async def listener(ctx: Context, value: Message):
        del value
        handle = ctx.accept(document_key)
        try:
            active.set()
            await asyncio.Event().wait()
        finally:
            await handle.write("listener cleanup\n")
            await handle.flush()

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        resource = await Database.open(tmp_path / "healthy.sqlite")
        try:
            await resource.start()
            ctx.offer(DB, resource)
            yield
        finally:
            await resource.aclose()

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="reader",
                        definition=Contrib(offers=[document_key], lifespan=reader),
                    ),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(
                            accepts=[document_key],
                            lifespan=consumer,
                            listen={MESSAGE_RECEIVED: listener},
                        ),
                    ),
                    observe(DB, connections),
                    ContribEntry(
                        name="db",
                        definition=Contrib(offers=[DB], lifespan=database_lifespan),
                    ),
                ]
            )
        ),
    ):
        publisher = tasks.create_task(
            hub.announce(MESSAGE_RECEIVED, Message("waiting"))
        )
        await active.wait()
        trigger.set()
        await stopped.wait()
        await publisher
        failed = next(
            instance for instance in hub.snapshots if instance.name == "reader"
        )
        assert isinstance(failed.state, Failed)
        assert failed.state.error and "ExceptionGroup" in failed.state.error
        blocked = next(
            instance for instance in hub.snapshots if instance.name == "consumer"
        )
        assert isinstance(blocked.state, Blocked)
        assert blocked.state.error and "ExceptionGroup" in blocked.state.error
        database = connections["observe-db"]
        await database.insert("root", "task failure stayed local")
        assert ("root", "task failure stayed local") in await database.messages()
        async with aiofiles.open(tmp_path / "reader.txt", "r") as handle:
            value = await handle.read()
        assert "listener cleanup" in value and "consumer cleanup" in value


async def test_simultaneous_faults_and_explicit_close_wait_for_shared_consumer(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
):
    first_key = ServiceKey[Database]("first.db")
    second_key = ServiceKey[Database]("second.db")
    cleaning = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()
    first_connections: dict[str, Database] = {}
    second_connections: dict[str, Database] = {}

    @asynccontextmanager
    async def first(ctx: Context):
        resource = await Database.open(tmp_path / "first.sqlite")
        try:
            await resource.start()
            ctx.offer(first_key, resource)
            yield
        finally:
            await resource.aclose()

    @asynccontextmanager
    async def second(ctx: Context):
        resource = await Database.open(tmp_path / "second.sqlite")
        try:
            await resource.start()
            ctx.offer(second_key, resource)
            yield
        finally:
            await resource.aclose()
            stopped.set()

    @asynccontextmanager
    async def consumer(ctx: Context):
        resources = (ctx.accept(first_key), ctx.accept(second_key))
        try:
            yield
        finally:
            cleaning.set()
            await release.wait()
            for resource in resources:
                async with resource.connection.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            async with aiofiles.open(tmp_path / "cleanup.txt", "w") as handle:
                await handle.write("both providers remained open during cleanup\n")

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(Composition()),
        hub.enable(
            Composition(
                entries=[
                    observe(first_key, first_connections),
                    ContribEntry(
                        name="first",
                        definition=Contrib(offers=[first_key], lifespan=first),
                    ),
                ]
            )
        ) as first_enablement,
        hub.enable(
            Composition(
                entries=[
                    observe(second_key, second_connections),
                    ContribEntry(
                        name="second",
                        definition=Contrib(offers=[second_key], lifespan=second),
                    ),
                ]
            )
        ),
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(
                            accepts=[first_key, second_key], lifespan=consumer
                        ),
                    )
                ]
            )
        ),
    ):
        resources = (
            first_connections["observe-first.db"],
            second_connections["observe-second.db"],
        )
        for resource in resources:
            async with resource.connection.execute("DROP TABLE messages"):
                pass
            await resource.connection.commit()
        errors = await asyncio.gather(
            *(resource.insert("root", "broken schema") for resource in resources),
            return_exceptions=True,
        )
        assert all(isinstance(error, sqlite3.OperationalError) for error in errors)
        try:
            await cleaning.wait()
            close = tasks.create_task(first_enablement.aclose(), eager_start=True)
            blocked = next(
                instance for instance in hub.snapshots if instance.name == "consumer"
            )
            assert isinstance(blocked.state, Blocked)
        finally:
            release.set()
        await close
        await stopped.wait()
        async with aiofiles.open(tmp_path / "cleanup.txt", "r") as handle:
            assert "both providers remained open" in await handle.read()
        assert all(resource.closed for resource in resources)


async def test_repeated_publisher_cancellation_waits_for_listener_cleanup_before_disable(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
):
    active = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    path = tmp_path / "cancellation.sqlite"

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        resource = await Database.open(path)
        try:
            await resource.start()
            ctx.offer(DB, resource)
            yield
        finally:
            await resource.aclose()

    async def listener(ctx: Context, value: Message):
        del value
        resource = ctx.accept(DB)
        try:
            active.set()
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            await resource.insert("root", "listener cleanup committed")

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="db",
                        definition=Contrib(offers=[DB], lifespan=database_lifespan),
                    ),
                    ContribEntry(
                        name="listener",
                        definition=Contrib(
                            accepts=[DB], listen={MESSAGE_RECEIVED: listener}
                        ),
                    ),
                ]
            )
        ) as enablement,
    ):
        publisher = tasks.create_task(
            hub.announce(MESSAGE_RECEIVED, Message("waiting"))
        )
        try:
            await active.wait()
            publisher.cancel()
            await cleaning.wait()
            publisher.cancel()
            close = tasks.create_task(enablement.aclose(), eager_start=True)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await publisher
        await close
    database = await Database.open(path)
    try:
        assert ("root", "listener cleanup committed") in await database.messages()
    finally:
        await database.aclose()
