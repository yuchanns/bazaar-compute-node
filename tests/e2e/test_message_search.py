from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid5, uuid7

import pytest
from bcn_test_support import RecordingAudit, StaticChannelBuilder, TestChannel

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import (
    AdapterRegistry,
    AgentAdapterFactories,
    SharedAdapterFactories,
)
from bazaar_compute_node.app.transport import LocalCommandClient
from bazaar_compute_node.contrib.codex.plugin import builder
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.actor import Mode
from bazaar_compute_node.core.agent import State
from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.core.models import Message, MessageDirection, SenderIdentity
from bazaar_compute_node.core.storage import IStorage

pytestmark = pytest.mark.e2e


async def _cli(
    wrapper: Path,
    environment: Mapping[str, str],
    *arguments: str,
    body: str | None = None,
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        str(wrapper),
        *arguments,
        env=dict(environment),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(20):
            stdout, _ = await process.communicate(
                body.encode() if body is not None else b""
            )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return cast(int, process.returncode), stdout.decode()


class SearchRegistry(AdapterRegistry):
    def __init__(self, channel: TestChannel) -> None:
        self.channel = channel

    def load_agent(
        self, *, channels: Sequence[str], runtimes: Sequence[str]
    ) -> AgentAdapterFactories:
        return AgentAdapterFactories(
            channels={kind: StaticChannelBuilder(self.channel) for kind in channels},
            runtimes={kind: builder.build for kind in runtimes},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", (Mode.SESSION, Mode.DANGEROUS_INDIVIDUAL))
async def test_natural_history_question_uses_original_context(
    mode: Mode, system_temp_dir: Path
) -> None:
    agent_id = str(uuid7())
    channel = TestChannel()
    audit = RecordingAudit()
    database = SqliteDatabase()
    node = NodeApplication(
        configuration=NodeConfiguration(
            version_check=False,
            storage="sqlite",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name="Release history assistant",
                    mode=mode,
                    channels=(ChannelConfiguration(kind="test"),),
                    runtimes=(
                        RuntimeConfiguration(
                            kind="codex",
                            model="gpt-5.6-luna",
                            effort="max",
                            env={"CODEX_HOME": "CODEX_HOME"}
                            if os.environ.get("CODEX_HOME")
                            else {},
                        ),
                    ),
                ),
            ),
        ),
        shared_factories=SharedAdapterFactories(
            storage=lambda: cast(IStorage, database), audit=lambda _: audit
        ),
        registry=SearchRegistry(channel),
        endpoint_path=system_temp_dir / "history.sock",
    )
    reports: list[dict[str, object]] = []
    await node.start()
    try:
        app = node.agents[agent_id]
        questions = 2 if mode is Mode.SESSION else 1
        for number in range(questions):
            history_id = str(uuid7())
            history_channel_id = str(uuid7())
            current_id = history_id if mode is Mode.SESSION else str(uuid7())
            current_channel_id = (
                history_channel_id if mode is Mode.SESSION else str(uuid7())
            )
            machine_count, minutes, owner = (
                (2, 30, "Alice") if number == 0 else (4, 15, "Bob")
            )
            history = (
                "上周 staging 发布讨论：有人提议直接全量。我们担心新配置会影响线上，先考虑灰度。",
                (
                    "最后确认 staging 的发布方案。\n\n"
                    "会上先对照了线上和验证环境的配置，确认这次调整涉及缓存键、连接池及超时设置。"
                    "直接切换全部实例虽然能缩短发布窗口，但一旦出现缓存回源增加，可能同时影响请求耗时和数据库负载。"
                    "平台组要求发布前保存当前配置快照，值班同学准备错误率、延迟、连接数和回源率四个监控面板。"
                    "业务组会提供一组真实请求验证结果；发布期间保持值班沟通，任何异常都先停止扩大流量，再核对配置差异。\n\n"
                    f"最终决定：先灰度 {machine_count} 台，观察 {minutes} 分钟；指标正常后再全量。"
                    f"指标异常立即回滚，回滚责任人 {owner}。"
                ),
            )
            sources: list[Message] = []
            for day, text in zip((21, 22), history, strict=True):
                await channel.inject(
                    Message(
                        direction=MessageDirection.INBOUND,
                        seq=0,
                        message_id=str(uuid7()),
                        thread_id=history_id,
                        channel_session_id=history_channel_id,
                        channel="test",
                        provider_thread_id=history_id,
                        provider_message_id=str(uuid7()),
                        target=f"dm:{history_channel_id}",
                        sender=SenderIdentity(id="release-owner", name=owner),
                        body=text,
                        received_at_ms=int(
                            datetime(2026, 9, day, 12, tzinfo=UTC).timestamp() * 1000
                        ),
                        notifies_runtime=False,
                    )
                )
                async with asyncio.timeout(20):
                    while True:
                        saved = await app.storage.list_messages(
                            str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"bcn:{agent_id}:bcn-session:{history_id}",
                                )
                            )
                        )
                        if any(message.body == text for message in saved):
                            sources.append(
                                next(
                                    message for message in saved if message.body == text
                                )
                            )
                            break
                        await asyncio.sleep(0.02)
            question = Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=str(uuid7()),
                thread_id=current_id,
                channel_session_id=current_channel_id,
                channel="test",
                provider_thread_id=current_id,
                provider_message_id=str(uuid7()),
                target=f"dm:{current_channel_id}",
                sender=SenderIdentity(name="Hanchin"),
                body="我要整理本周发布安排。上周关于 staging 的发布方案最后怎么定的？当时的争议和回滚责任人是谁？",
                received_at_ms=1790800000000,
            )
            actor = app.actors.for_thread(
                str(uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{current_id}"))
            )
            sent_after = len(channel.sent_messages)
            events_after = len(audit.events)
            await channel.inject(question)
            async with asyncio.timeout(600):
                while True:
                    replies = channel.sent_messages[sent_after:]
                    if (
                        replies
                        and app.orchestrator.session_runtime_state(actor) is State.IDLE
                    ):
                        break
                    failures = [
                        event
                        for event in audit.events[events_after:]
                        if event.event_name == "runtime.turn.failed"
                    ]
                    assert not failures, failures
                    await asyncio.sleep(0.1)
            text = "\n".join(reply.body for reply in replies)
            tools = [
                event
                for event in audit.events[events_after:]
                if event.event_name.startswith("tool.bcc.message.")
            ]
            operations = {event.event_name for event in tools}
            report = {
                "mode": mode.value,
                "conversation": number,
                "sources": [message.message_id for message in sources],
                "reply": text,
                "operations": sorted(operations),
                "search_arguments": [
                    event.metadata.get("arguments")
                    for event in tools
                    if event.event_name == "tool.bcc.message.search.completed"
                ],
            }
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False))
            assert "tool.bcc.message.search.completed" in operations
            assert "tool.bcc.message.read.completed" in operations
            assert "灰度" in text and "回滚" in text and owner in text
            assert any(message.message_id in text for message in sources)
            session = app.orchestrator.runtime_session(actor)
            assert session is not None and app._wrapper_path is not None
            environment = app._build_command_environment(
                actor.id, session.id, runtime_index=0
            )
            request: dict[str, object] = {
                "kind": "command",
                "resource": "message",
                "command": "search",
                "agent_id": agent_id,
                "actor_id": actor.id,
                "runtime_session_id": session.id,
                "session_capability": environment["BCN_COMMAND_CAPABILITY"],
                "query": "staging",
                "sender": f"@{owner}",
                "limit": 1,
            }
            cursor = await app.storage.get_consumer_cursor(sources[0].thread_id)
            freshness = dict(app.orchestrator.command_service._freshness_snapshots)
            searched = await LocalCommandClient.request(node.endpoint, request)
            assert searched["ok"], searched
            result = cast(dict[str, object], searched["result"])
            hit = cast(list[dict[str, object]], result["messages"])[0]
            assert result["has_more"] and result["next_offset"]
            assert await app.storage.get_consumer_cursor(sources[0].thread_id) == cursor
            assert app.orchestrator.command_service._freshness_snapshots == freshness
            reading = ("--target", cast(str, hit["target"]))
            code, page = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "search",
                "--query",
                "staging",
                "--sender",
                f"@{owner}",
                "--limit",
                "1",
                "--offset",
                "1",
            )
            assert code == 0 and sources[0].message_id in page, page
            code, history = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "read",
                *reading,
                "--around",
                cast(str, hit["message_id"]),
                "--limit",
                "50",
            )
            assert (
                code == 0
                and owner in history
                and all(source.message_id in history for source in sources)
            ), history
            code, dated = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "search",
                "--sender",
                f"@{owner.upper()}",
                "--after",
                "2026-09-21",
                "--before",
                "2026-09-21",
                "--sort",
                "relevance",
            )
            assert (
                code == 0 and sources[0].message_id in dated and "sort=time" in dated
            ), dated
            code, inclusive = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "search",
                "--after",
                "2026-09-21T12:00:00+00:00",
                "--before",
                "2026-09-21T12:00:00Z",
            )
            assert code == 0 and sources[0].message_id in inclusive, inclusive
            code, unfiltered = await _cli(
                app._wrapper_path, environment, "message", "search"
            )
            assert (code == 0) is (mode is Mode.SESSION), unfiltered
            code, no_hits = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "search",
                "--query",
                str(uuid7()),
            )
            assert code == 0 and "shown=0" in no_hits, no_hits
            sent_after = len(channel.sent_messages)
            code, sent = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "send",
                *reading,
                body="发布安排已核对。",
            )
            assert code == 0 and len(channel.sent_messages) > sent_after, sent
            assert channel.sent_messages[-1].session_id == history_id
            code, unfollow = await _cli(
                app._wrapper_path, environment, "thread", "unfollow", *reading
            )
            assert code == 0, unfollow
            code, draft_error = await _cli(
                app._wrapper_path,
                environment,
                "message",
                "send",
                "--send-draft",
                *reading,
            )
            assert code and "draft" in draft_error.lower(), draft_error
            scheduled = await LocalCommandClient.request(
                node.endpoint,
                {
                    **request,
                    "resource": "reminder",
                    "command": "schedule",
                    "title": "复核发布安排",
                    "message_id": sources[0].message_id,
                    "delay_seconds": 3600,
                },
            )
            assert scheduled["ok"], scheduled
            reminder = cast(
                dict[str, object],
                cast(dict[str, object], scheduled["result"])["reminder"],
            )
            code, canceled = await _cli(
                app._wrapper_path,
                environment,
                "reminder",
                "cancel",
                "--id",
                cast(str, reminder["reminder_id"]),
            )
            assert code == 0, canceled
            instructions = DeveloperInstructionContext(
                agent_name=app.name,
                bot_names=(),
                agent_id=agent_id,
                runtime_session_id=session.id,
                runtime="codex",
                workspace=str(app.workspace_path()),
                mode=mode,
            ).render()
            assert (
                "bcc message search" in instructions
                and "original message ID" in instructions
            )
            report.update(
                {
                    "cli_page": page,
                    "cli_read": history,
                    "cli_dated": dated,
                    "cli_send": sent,
                    "cli_draft_error": draft_error,
                    "unread_and_freshness_preserved": True,
                }
            )
            print(json.dumps({"cli_acceptance": report}, ensure_ascii=False))
            if mode is Mode.SESSION:
                session = app.orchestrator.runtime_session(actor)
                assert session is not None
                environment = app._build_command_environment(
                    actor.id,
                    session.id,
                    runtime_index=0,
                )
                process = await asyncio.create_subprocess_exec(
                    str(app._wrapper_path),
                    "message",
                    "search",
                    "--query",
                    "staging",
                    "--target",
                    cast(str, hit["target"]),
                    env=environment,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await process.communicate()
                rendered = stdout.decode()
                assert process.returncode == 0 and owner in rendered, rendered
                print(
                    json.dumps(
                        {"mode": mode.value, "bound_search": rendered},
                        ensure_ascii=False,
                    )
                )
    finally:
        await node.stop()
