from __future__ import annotations

lazy import asyncio
lazy import json
lazy import logging as stdlib
lazy import os
lazy from contextlib import asynccontextmanager
lazy from datetime import date, datetime, time, timedelta
lazy from logging.handlers import QueueHandler
lazy from pathlib import Path

import pytest
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    EventKey,
    Failed,
)
from pydantic import BaseModel
lazy import aiofiles
lazy from concurrent_log_handler import (
    ConcurrentRotatingFileHandler,
    ConcurrentTimedRotatingFileHandler,
)

from bazaar_contrib_logging import logging

pytestmark = pytest.mark.asyncio


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
                ],
            )
        ),
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="scheduler", definition=Contrib(lifespan=scheduler)
                    )
                ]
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


@pytest.mark.parametrize("format", ("text", "json"))
async def test_resource_startup_failure_keeps_its_error_and_traceback(
    tmp_path: Path, format
):
    path = tmp_path / "failures.log"
    missing = tmp_path / "missing" / "report.txt"

    @asynccontextmanager
    async def reader(ctx: Context):
        async with aiofiles.open(missing) as stream:
            await stream.read()
            yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging",
                        definition=logging,
                        config={"path": path, "format": format},
                    )
                ]
            )
        ),
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="report-reader",
                        definition=Contrib(lifespan=reader),
                        location="reports.input",
                    )
                ]
            )
        ),
    ):
        assert any(
            record.name == "report-reader" and isinstance(record.state, Failed)
            for record in hub.snapshots
        )
        stdlib.getLogger(__name__).info("independent work completed")
    content = await asyncio.to_thread(path.read_text)
    assert "report-reader" in content and "reports.input" in content
    assert "FileNotFoundError" in content and "Traceback" in content
    assert "report.txt" in content and "independent work completed" in content


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
        await ctx.announce(saved, request)
        logger.info("request finished for %s", request)

    async def record(ctx: Context, request: str):
        await asyncio.to_thread(logger.info, "handoff written for %s", request)

    class Config(BaseModel):
        request: str

    @asynccontextmanager
    async def capture(ctx: Context, config: Config):
        contexts[config.request, ctx.name] = ctx
        yield

    scheduler = Contrib(
        config=Config,
        lifespan=capture,
        listen={requested: schedule},
    )
    recorder = Contrib(config=Config, lifespan=capture, listen={saved: record})
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
        logger = stdlib.getLogger(__name__)
        await asyncio.to_thread(logger.info, "node started")
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
        await asyncio.to_thread(logger.info, "node work completed")
    assert all(stream is None or stream.closed for stream in streams)
    async with aiofiles.open(second_path, encoding="utf-8") as handle:
        assert any(
            json.loads(line)["message"] == "node work completed"
            for line in await handle.readlines()
        )


@pytest.mark.parametrize("format", (None, "text", "json"))
async def test_stream_output_accepts_configured_levels_and_formats(capsys, format):
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
        stdlib.getLogger(__name__).warning("node resource warning")
    lines = capsys.readouterr().err.splitlines()
    if format == "json":
        records = [json.loads(line) for line in lines]
        assert any(
            record["message"] == "node resource warning"
            and record["level"] == "warning"
            and datetime.fromisoformat(record["timestamp"]).tzinfo is not None
            for record in records
        )
    else:
        assert any(
            "[WARN ]" in line and "node resource warning" in line for line in lines
        )


async def test_file_output_rotates_a_previous_day_and_retains_recent_days(
    tmp_path: Path,
):
    today = date.today()
    previous = today - timedelta(days=1)
    path = tmp_path / "node.jsonl"
    await asyncio.to_thread(
        path.write_text,
        json.dumps({"message": "node previous day"}) + "\n",
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
            json.dumps({"message": "node archived", "level": "info"}) + "\n",
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
        stdlib.getLogger(__name__).info("node started")
    async with aiofiles.open(path) as stream:
        assert any(
            json.loads(line)["message"] == "node started"
            for line in await stream.readlines()
        )
    async with aiofiles.open(path.with_name(f"{path.name}.{previous}")) as stream:
        assert any(
            json.loads(line)["message"] == "node previous day"
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


async def test_optional_level_locations_and_size_rotation_preserve_logs(
    tmp_path: Path,
):
    path = tmp_path / "shared" / "node.jsonl"
    locations = {
        "info": tmp_path / "info" / "node.jsonl",
        "error": tmp_path / "errors" / "node.jsonl",
    }
    today = date.today()
    await asyncio.to_thread(locations["info"].parent.mkdir)
    for days in (2, 5):
        archive = locations["info"].with_name(
            f"node.jsonl.{today - timedelta(days=days)}.1"
        )
        await asyncio.to_thread(
            archive.write_text,
            json.dumps({"message": "node archived", "level": "info"}) + "\n",
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
        logger = stdlib.getLogger(__name__)
        for step in range(20):
            for level in locations:
                logger.log(
                    stdlib.getLevelNamesMapping()[level.upper()],
                    "saved progress %s: 进度已保存 🌙",
                    step,
                )
        logger.warning("node resource warning")
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
            records.extend(json.loads(line) for line in lines)
        assert all(record["level"] == level for record in records)
        assert all(
            any(f"saved progress {step}:" in record["message"] for record in records)
            for step in range(20)
        )
    async with aiofiles.open(path) as stream:
        assert any(
            json.loads(line)["message"] == "node resource warning"
            for line in await stream.readlines()
        )


async def test_audit_events_are_written_as_single_json_lines(capsys):
    from bazaar_contrib_audit import AUDIT, AuditContext, AuditError, audit

    @asynccontextmanager
    async def publisher(ctx: Context):
        await ctx.accept(AUDIT).publish(
            "harness.tool.completed",
            context=AuditContext(agent_id="Cloud", thread_id="weekend-edition"),
            payload={"call": {"name": "file.read", "output": "第一章"}},
        )
        await ctx.accept(AUDIT).publish(
            "harness.tool.failed",
            context=AuditContext(agent_id="Cloud", thread_id="weekend-edition"),
            payload={"call": {"name": "file.write"}},
            error=AuditError(kind="tool_call_failed", message="磁盘已满"),
        )
        yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="logging", definition=logging, config={"format": "json"}
                    ),
                    ContribEntry(name="audit", definition=audit),
                    ContribEntry(
                        name="publisher",
                        definition=Contrib(accepts=[AUDIT], lifespan=publisher),
                    ),
                ]
            )
        ),
    ):
        pass
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    events = [
        (record["level"], json.loads(record["message"]))
        for record in records
        if record["logger"] == "bazaar_compute_node.audit"
    ]
    assert any(
        level == "info"
        and event["event_name"] == "harness.tool.completed"
        and event["context"]["agent_id"] == "Cloud"
        and event["payload"]["call"]["output"] == "第一章"
        for level, event in events
    )
    assert any(
        level == "error"
        and event["event_name"] == "harness.tool.failed"
        and event["error"] == {"kind": "tool_call_failed", "message": "磁盘已满"}
        for level, event in events
    )
