from __future__ import annotations

lazy import asyncio
lazy import json
lazy import logging as stdlib
lazy import os
lazy import re
lazy from contextlib import asynccontextmanager
lazy from datetime import date, datetime, time, timedelta
lazy from logging.handlers import QueueHandler
lazy from pathlib import Path

import pytest
from aiofiles.threadpool.text import AsyncTextIOWrapper
from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent, LogLevel
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    EventKey,
    ServiceKey,
)
from pydantic import BaseModel
lazy import aiofiles
lazy from concurrent_log_handler import (
    ConcurrentRotatingFileHandler,
    ConcurrentTimedRotatingFileHandler,
)

from bazaar_contrib_logging import LOG_OUTPUT, ILogOutput, logging

pytestmark = pytest.mark.asyncio

JOURNAL = ServiceKey[AsyncTextIOWrapper]("audit.journal")


@pytest.mark.parametrize("format", ("text", "json"))
async def test_standard_logger_keeps_threaded_output_and_exception_details(
    tmp_path: Path, format
):
    path = tmp_path / "ordinary.log"
    threads = []
    ids = []

    @asynccontextmanager
    async def scheduler(ctx: Context):
        ids.append(str(ctx.id))
        logger = stdlib.getLogger(__name__)

        def produce():
            for step in range(40):
                logger.info("saved item %s 🌙", step)

        await asyncio.gather(*(asyncio.to_thread(produce) for _ in range(3)))
        try:
            raise ValueError("cannot read the saved report")
        except ValueError:
            logger.exception("report import failed")
        try:
            yield
        finally:
            logger.critical("last record before shutdown")

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging",
                        definition=logging,
                        config={"path": path, "format": format},
                    ),
                    ContribEntry(
                        name="scheduler",
                        definition=Contrib(accepts=[LOG_OUTPUT], lifespan=scheduler),
                    ),
                ],
            )
        ),
    ):
        for handler in stdlib.getLogger().handlers:
            if isinstance(handler, QueueHandler) and handler.listener is not None:
                threads.append(handler.listener._thread)
    assert threads and all(
        thread is not None and not thread.is_alive() for thread in threads
    )
    text = await asyncio.to_thread(path.read_text)
    if format == "json":
        records = [json.loads(line) for line in text.splitlines()]
        saved = [
            record
            for record in records
            if record.get("message", "").startswith("saved item")
        ]
        assert len(saved) >= 120
        assert all(
            record["source"]["contrib"] == "scheduler"
            and record["source"]["id"] == ids[0]
            for record in saved
        )
        assert any(
            "ValueError" in record.get("exception", "")
            and "report import failed" in record.get("message", "")
            for record in records
        )
        assert any(
            record.get("level") == "critical"
            and "last record" in record.get("message", "")
            for record in records
        )
        for record in saved:
            source = record["source"]
            source_text = await asyncio.to_thread(Path(source["pathname"]).read_text)
            assert "logger.info(" in source_text.splitlines()[source["lineno"] - 1]
    else:
        assert "[INFO ][scheduler]" in text
        assert "saved item 39" in text
        assert "Traceback" in text and "ValueError: cannot read" in text
        assert "[CRIT ][scheduler]" in text and "last record before shutdown" in text


async def test_nested_callbacks_keep_concrete_sources_with_private_events(
    tmp_path: Path,
):
    from contextlib import AsyncExitStack

    path = tmp_path / "workflow.jsonl"
    requested, saved = EventKey[str]("work.requested"), EventKey[str]("work.saved")
    logger = stdlib.getLogger(__name__)
    contexts: dict[tuple[str, str], Context] = {}

    async def schedule(ctx: Context, request: str):
        logger.info("request started for %s", request)
        await ctx.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="agent.work.started", metadata={"request": request}
            ),
        )
        await ctx.announce(saved, request)
        logger.info("request finished for %s", request)
        await ctx.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="agent.work.completed", metadata={"request": request}
            ),
        )

    async def record(ctx: Context, request: str):
        await asyncio.to_thread(logger.info, "handoff written for %s", request)
        await ctx.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="agent.record.saved", metadata={"request": request}
            ),
        )

    class Config(BaseModel):
        request: str

    @asynccontextmanager
    async def capture(ctx: Context, config: Config):
        contexts[config.request, ctx.name] = ctx
        yield

    scheduler = Contrib(
        config=Config,
        accepts=[LOG_OUTPUT],
        lifespan=capture,
        listen={requested: schedule},
    )
    recorder = Contrib(
        config=Config, accepts=[LOG_OUTPUT], lifespan=capture, listen={saved: record}
    )
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging", definition=logging, config={"path": path}
                    )
                ]
            )
        ),
        AsyncExitStack() as stack,
    ):
        for name in ("alice", "bob"):
            await stack.enter_async_context(
                hub.enable(
                    Composition(
                        entries=[
                            ContribEntry(
                                name="scheduler",
                                definition=scheduler,
                                config={"request": name},
                            ),
                            ContribEntry(
                                name="recorder",
                                definition=recorder,
                                config={"request": name},
                            ),
                        ]
                    ),
                    local=(requested, saved),
                )
            )
        await asyncio.gather(
            *(
                contexts[name, "scheduler"].announce(requested, name)
                for name in ("alice", "bob")
            )
        )
        logger.info("node workflow completed")
    records = [
        json.loads(line)
        for line in (await asyncio.to_thread(path.read_text)).splitlines()
    ]
    for request in ("alice", "bob"):
        for message, contrib in (
            ("request started", "scheduler"),
            ("request finished", "scheduler"),
            ("handoff written", "recorder"),
        ):
            assert any(
                message in item.get("message", "")
                and request in item["message"]
                and item["source"]["contrib"] == contrib
                and item["source"]["id"] == str(contexts[request, contrib].id)
                for item in records
            )
        for event, contrib in (
            ("agent.work.started", "scheduler"),
            ("agent.work.completed", "scheduler"),
            ("agent.record.saved", "recorder"),
        ):
            assert any(
                item.get("event_name") == event
                and item["metadata"].get("request") == request
                and item["source"]["contrib"] == contrib
                and item["source"]["id"] == str(contexts[request, contrib].id)
                for item in records
            )
    assert any(
        "node workflow completed" in item.get("message", "")
        and item["logger"] == __name__
        for item in records
    )


async def test_root_logger_levels_and_existing_handler_survive_independent_hubs(
    tmp_path: Path,
):
    root = stdlib.getLogger()
    level = root.level
    external = await asyncio.to_thread(stdlib.FileHandler, tmp_path / "external.log")
    root.addHandler(external)
    first_path, second_path = tmp_path / "first.log", tmp_path / "second.log"
    try:
        async with (
            ContribHub() as first,
            first.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="logging",
                            definition=logging,
                            config={"path": first_path, "level": "debug"},
                        )
                    ]
                )
            ) as owner,
            ContribHub() as second,
            second.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="logging",
                            definition=logging,
                            config={"path": second_path, "level": "error"},
                        )
                    ]
                )
            ),
        ):
            logger = stdlib.getLogger(__name__)
            await asyncio.to_thread(logger.debug, "first hub received a debug record")
            await owner.aclose()
            await asyncio.to_thread(logger.error, "second hub continues")
        assert root.level == level
        await asyncio.to_thread(logger.error, "external handler remains available")
        text = await asyncio.to_thread((tmp_path / "external.log").read_text)
        assert "external handler remains available" in text
        text = await asyncio.to_thread(first_path.read_text)
        assert "first hub received" in text
        text = await asyncio.to_thread(second_path.read_text)
        assert "second hub continues" in text
    finally:
        root.removeHandler(external)
        await asyncio.to_thread(external.close)


async def test_shared_output_and_audit_consumers_keep_running_after_local_owner_closes(
    tmp_path: Path,
):
    from contextlib import AsyncExitStack

    path = tmp_path / "日志 🌙" / "node.jsonl"
    journal_path = tmp_path / "journal.jsonl"
    outputs: dict[str, ILogOutput] = {}
    contexts: dict[str, Context] = {}
    work = EventKey[bool]("agent.work")

    @asynccontextmanager
    async def journal(ctx: Context):
        async with aiofiles.open(journal_path, "w", encoding="utf-8") as stream:
            ctx.offer(JOURNAL, stream)
            yield

    async def append(ctx: Context, event: AuditEvent):
        stream = ctx.accept(JOURNAL)
        await stream.write(event.model_dump_json() + "\n")
        await stream.flush()

    @asynccontextmanager
    async def agent(ctx: Context):
        outputs[ctx.name] = ctx.accept(LOG_OUTPUT)
        contexts[ctx.name] = ctx
        await ctx.announce(AUDIT_EVENT, AuditEvent.capture(event_name="agent.started"))
        yield

    async def complete(ctx: Context, value: bool):
        del value
        await ctx.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="agent.work.completed",
                metadata={"result": "报告已保存", "items": [1, True, None]},
            ),
        )
        await ctx.accept(LOG_OUTPUT).append(
            AuditEvent.capture(event_name="output.service.used")
        )

    definition = Contrib(
        accepts=[LOG_OUTPUT, JOURNAL], lifespan=agent, listen={work: complete}
    )
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging", definition=logging, config={"path": path}
                    ),
                    ContribEntry(
                        name="journal",
                        definition=Contrib(
                            offers=[JOURNAL],
                            lifespan=journal,
                            listen={AUDIT_EVENT: append},
                        ),
                    ),
                ]
            )
        ),
        AsyncExitStack() as stack,
    ):
        alice = await stack.enter_async_context(
            hub.enable(
                Composition(
                    entries=[ContribEntry(name="alice", definition=definition)]
                ),
                local=(work,),
            )
        )
        await stack.enter_async_context(
            hub.enable(
                Composition(entries=[ContribEntry(name="bob", definition=definition)]),
                local=(work,),
            )
        )
        handler = next(
            handler
            for name in stdlib.getHandlerNames()
            if isinstance(
                handler := stdlib.getHandlerByName(name),
                ConcurrentTimedRotatingFileHandler,
            )
            and Path(handler.baseFilename) == path
        )
        stream, lock = handler.clh.stream, handler.clh.stream_lock
        await alice.aclose()
        await contexts["bob"].announce(work, True)
    assert all(stream is None or stream.closed for stream in (stream, lock))
    async with aiofiles.open(path, encoding="utf-8") as handle:
        records = [json.loads(line) for line in await handle.readlines()]
    async with aiofiles.open(journal_path, encoding="utf-8") as handle:
        peer = [json.loads(line) for line in await handle.readlines()]
    for name in ("alice", "bob"):
        for collection in (records, peer):
            assert any(
                record["source"]["id"] == str(contexts[name].id)
                and record["event_name"] == "agent.started"
                for record in collection
            )
    assert any(record["metadata"].get("result") == "报告已保存" for record in records)
    assert any(record["event_name"] == "output.service.used" for record in records)


async def test_independent_hubs_release_their_own_output_handlers(tmp_path: Path):
    first_path, second_path = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    async with (
        ContribHub() as first,
        first.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging", definition=logging, config={"path": first_path}
                    )
                ]
            )
        ) as owner,
        ContribHub() as second,
        second.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging",
                        definition=logging,
                        config={"path": second_path, "level": "INFO"},
                    )
                ]
            )
        ),
    ):
        for hub in (first, second):
            await hub.announce(
                AUDIT_EVENT,
                AuditEvent.capture(event_name="node.started"),
            )
        streams = [
            stream
            for name in stdlib.getHandlerNames()
            if isinstance(
                handler := stdlib.getHandlerByName(name),
                ConcurrentTimedRotatingFileHandler,
            )
            and Path(handler.baseFilename) in (first_path, second_path)
            for stream in (handler.clh.stream, handler.clh.stream_lock)
        ]
        await owner.aclose()
        await second.announce(
            AUDIT_EVENT,
            AuditEvent.capture(event_name="node.work.completed"),
        )
    assert all(stream is None or stream.closed for stream in streams)
    async with aiofiles.open(second_path, encoding="utf-8") as handle:
        assert any(
            json.loads(line)["event_name"] == "node.work.completed"
            for line in await handle.readlines()
        )


@pytest.mark.parametrize("format", (None, "text", "json"))
async def test_stream_output_accepts_public_audit_levels_and_formats(capsys, format):
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging",
                        definition=logging,
                        config={"level": "WARNING", "format": format},
                    )
                ]
            )
        ),
    ):
        await hub.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="node.resource.warning",
                level=LogLevel.WARNING,
            ),
        )
    lines = capsys.readouterr().err.splitlines()
    if format == "json":
        records = [json.loads(line) for line in lines]
        assert any(
            record["event_name"] == "node.resource.warning"
            and record["level"] == "warning"
            and datetime.fromisoformat(record["timestamp"]).tzinfo is not None
            for record in records
        )
    else:
        assert any(
            "[WARN ]" in line and "node.resource.warning" in line for line in lines
        )


@pytest.mark.parametrize("format", (None, "text", "json"))
async def test_formats_retain_contrib_and_call_site_across_delayed_delivery(
    tmp_path: Path, capsys, format
):
    path = tmp_path / "node.log"
    events: list[AuditEvent] = []
    publishers = ("scheduler", "recorder")

    @asynccontextmanager
    async def publisher(ctx: Context):
        for level in (LogLevel.INFO, LogLevel.WARNING):
            events.append(
                AuditEvent.capture(
                    event_name="agent.work.completed",
                    level=level,
                    metadata={"result": "报告已保存 🌙\n下一项"},
                )
            )
        yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging",
                        definition=logging,
                        config={"paths": {"info": path}, "format": format},
                    ),
                    *[
                        ContribEntry(name=name, definition=Contrib(lifespan=publisher))
                        for name in publishers
                    ],
                ]
            )
        ),
    ):
        await asyncio.sleep(0.02)
        delivered_at = datetime.now().timestamp()
        for event in events:
            event = AuditEvent.model_validate_json(event.model_dump_json())
            await hub.announce(AUDIT_EVENT, event)
    file = await asyncio.to_thread(path.read_text)
    stderr = capsys.readouterr().err
    for content, output in (
        (file, format or "json"),
        (stderr, format or "text"),
    ):
        if output == "json":
            records = [json.loads(line) for line in content.splitlines()]
            assert all(
                any(record["source"]["contrib"] == name for record in records)
                for name in publishers
            )
            for record in records:
                source = record["source"]
                assert source["module"] == __name__
                assert source["function"] == "publisher"
                lines = await asyncio.to_thread(Path(source["pathname"]).read_text)
                assert "AuditEvent.capture(" in lines.splitlines()[source["lineno"] - 1]
                timestamp = datetime.fromisoformat(record["timestamp"])
                assert timestamp.tzinfo is not None
                assert timestamp.timestamp() < delivered_at
                assert abs(timestamp.timestamp() * 1000 - record["created_at_ms"]) < 1
                assert "报告已保存" in record["metadata"]["result"]
        else:
            assert all(f"[{name}]" in content for name in publishers)
            for line in content.splitlines():
                fields = re.match(
                    r"\[(?P<time>[^]]+)\]\[(?P<level>[^]]+)\]"
                    r"\[[^]]+\]\[(?P<file>[^]]+):(?P<line>\d+)\] "
                    r"agent.work.completed (?P<metadata>.*)",
                    line,
                )
                assert fields is not None
                timestamp = datetime.fromisoformat(fields["time"])
                assert timestamp.tzinfo is not None
                assert timestamp.timestamp() < delivered_at
                assert len(fields["level"]) == 5
                assert fields["file"] == Path(__file__).name
                lines = await asyncio.to_thread(Path(__file__).read_text)
                assert (
                    "AuditEvent.capture(" in lines.splitlines()[int(fields["line"]) - 1]
                )
                assert "报告已保存" in json.loads(fields["metadata"])["result"]


async def test_file_output_rotates_a_previous_day_and_retains_recent_days(
    tmp_path: Path,
):
    today = date.today()
    previous = today - timedelta(days=1)
    path = tmp_path / "node.jsonl"
    await asyncio.to_thread(
        path.write_text,
        AuditEvent.capture(event_name="node.previous-day").model_dump_json() + "\n",
    )
    timestamp = datetime.combine(previous, time(12)).timestamp()
    await asyncio.to_thread(os.utime, path, (timestamp, timestamp))
    directory, filename = ConcurrentRotatingFileHandler.baseLockFilename(str(path))
    await asyncio.to_thread(
        (Path(directory) / filename).write_text,
        str(int(datetime.combine(today, time()).timestamp())),
    )
    for days in (2, 6, 9, 30):
        archive = path.with_name(f"{path.name}.{today - timedelta(days=days)}")
        await asyncio.to_thread(
            archive.write_text,
            AuditEvent.capture(event_name="node.archived").model_dump_json() + "\n",
        )
    notes = path.with_name(path.name + ".notes")
    await asyncio.to_thread(notes.write_text, "operator notes")
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging", definition=logging, config={"path": path}
                    )
                ]
            )
        ),
    ):
        await hub.announce(
            AUDIT_EVENT,
            AuditEvent.capture(event_name="node.started"),
        )
    async with aiofiles.open(path) as stream:
        assert any(
            json.loads(line)["event_name"] == "node.started"
            for line in await stream.readlines()
        )
    async with aiofiles.open(path.with_name(f"{path.name}.{previous}")) as stream:
        assert any(
            json.loads(line)["event_name"] == "node.previous-day"
            for line in await stream.readlines()
        )
    archives = await asyncio.to_thread(
        lambda: list(tmp_path.glob(path.name + ".????-??-??"))
    )
    assert archives
    assert all(
        today - timedelta(days=6) <= date.fromisoformat(archive.name[-10:]) <= today
        for archive in archives
    )
    async with aiofiles.open(notes) as stream:
        assert "operator notes" in await stream.read()


async def test_optional_level_locations_and_size_rotation_preserve_events(
    tmp_path: Path,
):
    path = tmp_path / "shared" / "node.jsonl"
    locations = {
        LogLevel.INFO: tmp_path / "info" / "node.jsonl",
        LogLevel.ERROR: tmp_path / "errors" / "node.jsonl",
    }
    today = date.today()
    await asyncio.to_thread(locations[LogLevel.INFO].parent.mkdir)
    for days in (2, 5):
        archive = locations[LogLevel.INFO].with_name(
            f"node.jsonl.{today - timedelta(days=days)}.1"
        )
        await asyncio.to_thread(
            archive.write_text,
            AuditEvent.capture(event_name="node.archived").model_dump_json() + "\n",
        )
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging",
                        definition=logging,
                        config={
                            "path": path,
                            "paths": locations,
                            "retention_days": 3,
                            "max_size": "1 KiB",
                        },
                    )
                ]
            )
        ),
    ):
        for step in range(20):
            for level in locations:
                await hub.announce(
                    AUDIT_EVENT,
                    AuditEvent.capture(
                        event_name="agent.work.progress",
                        level=level,
                        metadata={"step": step, "result": "进度已保存 🌙"},
                    ),
                )
        await hub.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="node.resource.warning",
                level=LogLevel.WARNING,
            ),
        )
    for level, location in locations.items():
        files = await asyncio.to_thread(
            lambda location=location: list(location.parent.glob("node.jsonl*"))
        )
        assert any(file.name != location.name for file in files)
        records = []
        for file in files:
            if file != location:
                assert date.fromisoformat(
                    file.name[len("node.jsonl.") :][:10]
                ) >= today - timedelta(days=2)
            async with aiofiles.open(file) as stream:
                lines = await stream.readlines()
            assert (await asyncio.to_thread(file.stat)).st_size <= 1024 + max(
                len(line.replace("\n", os.linesep).encode("utf-8")) for line in lines
            )
            records.extend(json.loads(line) for line in lines)
        assert all(record["level"] == level.value for record in records)
        assert all(
            any(record["metadata"].get("step") == step for record in records)
            for step in range(20)
        )
    async with aiofiles.open(path) as stream:
        assert any(
            json.loads(line)["event_name"] == "node.resource.warning"
            for line in await stream.readlines()
        )
