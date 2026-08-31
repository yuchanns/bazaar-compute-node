from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import bazaar_compute_node.contrib.telegram.api as telegram_api_module
from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.api import TelegramBotApi
from bazaar_compute_node.contrib.telegram.approval import TelegramApprovalChannel
from bazaar_compute_node.contrib.telegram.identity import TelegramThreadIdentity
from bazaar_compute_node.core.channel import ChannelApprovalRequest, ChannelContext
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ChannelTargetKind,
)
from bazaar_compute_node.i18n import SIMPLIFIED_CHINESE, create_translator

TEST_BOT_ID = 1_000_000_001
TEST_CHAT_ID = 1_000_000_002
TEST_ORIGINAL_SENDER_ID = 1_000_000_003
TEST_OTHER_SENDER_ID = 1_000_000_004


class _FakeApprovalApi:
    def __init__(self) -> None:
        self.sent: list[Mapping[str, object]] = []
        self.edits: list[Mapping[str, object]] = []
        self.answers: list[tuple[str, str | None]] = []

    async def send_rich_message(
        self,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> Mapping[str, object]:
        assert timeout > 0
        self.sent.append(payload)
        return {"message_id": 42}

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
        timeout: float,
    ) -> None:
        assert not show_alert
        assert timeout > 0
        self.answers.append((callback_query_id, text))

    async def edit_message_text(
        self,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> Mapping[str, object]:
        assert timeout > 0
        self.edits.append(payload)
        return {"message_id": 42}


def _request(
    sender_id: str | None,
    *,
    description: str | None = None,
) -> ChannelApprovalRequest:
    identity = TelegramThreadIdentity(
        bot_id=TEST_BOT_ID,
        chat_id=TEST_CHAT_ID,
        topic_id=0,
    )
    return ChannelApprovalRequest(
        approval=ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            runtime_session_id="runtime-1",
            action="command_execution",
            created_at_ms=1,
            description=description,
        ),
        target_kind=ChannelTargetKind.DM,
        provider_thread_id=identity.provider_thread_id,
        provider_sender_id=sender_id,
    )


def _callback(*, query_id: str, token: str, sender_id: int) -> dict[str, object]:
    return {
        "id": query_id,
        "data": f"bcn:approve:{token}",
        "from": {"id": sender_id},
        "message": {
            "message_id": 42,
            "chat": {"id": TEST_CHAT_ID},
        },
    }


def test_telegram_approval_markdown_renders_optional_description(
    tmp_path: Path,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = TelegramApprovalChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        token="token",
    )

    assert channel._approval_markdown(_request(str(TEST_ORIGINAL_SENDER_ID))) == (
        "## Approval required\n\n**Action:** command execution"
    )
    assert channel._approval_markdown(
        _request(
            str(TEST_ORIGINAL_SENDER_ID),
            description="Run ``` and {{ untouched }}.",
        )
    ) == (
        "## Approval required\n\n**Action:** command execution\n\n````\n"
        "Run ``` and {{ untouched }}.\n````"
    )


@pytest.mark.asyncio
async def test_telegram_approval_uses_original_sender_id_not_display_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = TelegramApprovalChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        token="token",
    )
    api = _FakeApprovalApi()
    monkeypatch.setattr(channel, "_api", api)
    monkeypatch.setattr(channel, "_bot_id", TEST_BOT_ID)

    task = asyncio.create_task(
        channel.request_approval(
            _request(str(TEST_ORIGINAL_SENDER_ID)),
            timeout=1,
        )
    )
    while not channel._pending_approvals:
        await asyncio.sleep(0)
    token = next(iter(channel._pending_approvals))

    await channel._handle_callback_query(
        _callback(
            query_id="other-user",
            token=token,
            sender_id=TEST_OTHER_SENDER_ID,
        )
    )
    while channel._approval_callback_tasks:
        await asyncio.sleep(0)
    assert not task.done()
    assert channel._last_update_disposition == "approval_callback_sender_mismatch"

    await channel._handle_callback_query(
        _callback(
            query_id="original-user",
            token=token,
            sender_id=TEST_ORIGINAL_SENDER_ID,
        )
    )
    result = await task
    while channel._approval_callback_tasks:
        await asyncio.sleep(0)

    assert result.decision is ApprovalDecision.APPROVED
    assert api.answers == [
        ("other-user", "This approval belongs to another user"),
        ("original-user", "Approved"),
    ]


@pytest.mark.asyncio
async def test_telegram_approval_requires_live_sender_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = TelegramApprovalChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        token="token",
    )
    monkeypatch.setattr(channel, "_api", _FakeApprovalApi())
    monkeypatch.setattr(channel, "_bot_id", TEST_BOT_ID)

    with pytest.raises(ValueError, match="original sender id"):
        await channel.request_approval(_request(None), timeout=1)


@pytest.mark.asyncio
async def test_telegram_approval_localizes_prompt_buttons_and_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = TelegramApprovalChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
            translator=create_translator(SIMPLIFIED_CHINESE),
        ),
        token="token",
    )
    api = _FakeApprovalApi()
    monkeypatch.setattr(channel, "_api", api)
    monkeypatch.setattr(channel, "_bot_id", TEST_BOT_ID)

    task = asyncio.create_task(
        channel.request_approval(
            _request(str(TEST_ORIGINAL_SENDER_ID)),
            timeout=1,
        )
    )
    while not channel._pending_approvals:
        await asyncio.sleep(0)
    token = next(iter(channel._pending_approvals))

    prompt = api.sent[0]
    rich_message = prompt["rich_message"]
    assert isinstance(rich_message, Mapping)
    assert rich_message["markdown"] == "## 需要审批\n\n**操作：** 命令执行"
    reply_markup = prompt["reply_markup"]
    assert isinstance(reply_markup, Mapping)
    inline_keyboard = reply_markup["inline_keyboard"]
    assert isinstance(inline_keyboard, list)
    assert isinstance(inline_keyboard[0], list)
    buttons = cast(list[Mapping[str, object]], inline_keyboard[0])
    assert all(isinstance(button, Mapping) for button in buttons)
    assert [button["text"] for button in buttons] == [
        "✅ 批准",
        "❎ 拒绝",
    ]

    await channel._handle_callback_query(
        _callback(
            query_id="original-user",
            token=token,
            sender_id=TEST_ORIGINAL_SENDER_ID,
        )
    )
    result = await task
    while channel._approval_callback_tasks:
        await asyncio.sleep(0)

    assert result.decision is ApprovalDecision.APPROVED
    assert len(api.sent) == 1
    assert api.edits == [
        {
            "chat_id": TEST_CHAT_ID,
            "message_id": 42,
            "rich_message": {
                "markdown": "## 需要审批\n\n**操作：** 命令执行\n\n操作已批准",
                "skip_entity_detection": True,
            },
            "reply_markup": {"inline_keyboard": []},
        }
    ]
    assert api.answers == [("original-user", "已批准")]

    await channel._handle_callback_query(
        _callback(
            query_id="duplicate",
            token=token,
            sender_id=TEST_ORIGINAL_SENDER_ID,
        )
    )
    while channel._approval_callback_tasks:
        await asyncio.sleep(0)
    assert api.answers[-1] == ("duplicate", "已批准")


@pytest.mark.asyncio
async def test_telegram_edit_message_text_sends_provider_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, Mapping[str, object]]] = []

    async def telegram(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = await request.json()
        assert isinstance(payload, Mapping)
        requests.append((method, payload))
        return web.json_response(
            {"ok": True, "result": {"message_id": payload["message_id"]}}
        )

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )
    try:
        async with aiohttp.ClientSession() as session:
            api = TelegramBotApi(session, token="token")
            await api.edit_message_text(
                {"chat_id": TEST_CHAT_ID, "message_id": 41, "text": "Updated"},
                timeout=1,
            )
    finally:
        await server.close()

    assert requests == [
        (
            "editMessageText",
            {"chat_id": TEST_CHAT_ID, "message_id": 41, "text": "Updated"},
        ),
    ]


@pytest.mark.asyncio
async def test_telegram_slow_callback_does_not_block_another_session_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates_ready = asyncio.Event()
    callback_started = asyncio.Event()
    disconnect = asyncio.Event()
    updates_delivered = False
    edit_requests = 0

    async def telegram(request: web.Request) -> web.Response:
        nonlocal updates_delivered, edit_requests
        method = request.match_info["method"]
        if method == "getMe":
            return web.json_response(
                {
                    "ok": True,
                    "result": {
                        "id": TEST_BOT_ID,
                        "is_bot": True,
                        "username": "test-bot",
                        "first_name": "Test Bot",
                    },
                }
            )
        if method == "sendRichMessage":
            updates_ready.set()
            return web.json_response({"ok": True, "result": {"message_id": 42}})
        if method == "getUpdates":
            if updates_delivered:
                await disconnect.wait()
                return web.json_response({"ok": True, "result": []})
            await updates_ready.wait()
            updates_delivered = True
            token = next(iter(channel._pending_approvals))
            return web.json_response(
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 1,
                            "callback_query": _callback(
                                query_id="slow-answer",
                                token=token,
                                sender_id=TEST_ORIGINAL_SENDER_ID,
                            ),
                        },
                        {
                            "update_id": 2,
                            "message": {
                                "message_id": 43,
                                "date": channel._started_at_s,
                                "chat": {
                                    "id": TEST_OTHER_SENDER_ID,
                                    "type": "private",
                                },
                                "from": {
                                    "id": TEST_OTHER_SENDER_ID,
                                    "is_bot": False,
                                },
                                "text": "Can this session still get through?",
                            },
                        },
                    ],
                }
            )
        if method == "answerCallbackQuery":
            callback_started.set()
            await disconnect.wait()
            return web.json_response({"ok": True, "result": True})
        if method == "editMessageText":
            edit_requests += 1
            return web.json_response({"ok": True, "result": {"message_id": 42}})
        raise AssertionError(f"unexpected Telegram method: {method}")

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )

    async def referenced_paths() -> set[str]:
        return set()

    channel = TelegramApprovalChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        token="token",
    )
    approval_task: asyncio.Task[ApprovalResult] | None = None
    inbound = channel.receive()
    try:
        await channel.start(timeout=1)
        approval_task = asyncio.create_task(
            channel.request_approval(
                _request(str(TEST_ORIGINAL_SENDER_ID)),
                timeout=1,
            )
        )
        await asyncio.wait_for(callback_started.wait(), timeout=1)
        result = await asyncio.wait_for(approval_task, timeout=1)
        approval_task = None
        message = await asyncio.wait_for(anext(inbound), timeout=1)
        assert result.decision is ApprovalDecision.APPROVED
        assert message.provider_message_id == "43"
        pending_tasks = channel.health["approval_callback_tasks_pending"]
        assert isinstance(pending_tasks, int)
        assert pending_tasks > 0
        await channel.stop(timeout=1)
    finally:
        disconnect.set()
        if approval_task is not None and not approval_task.done():
            approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
        if channel.health["state"] != "stopped":
            await channel.stop(timeout=1)
        await server.close()

    assert channel.health["approval_callback_tasks_pending"] == 0
    assert edit_requests == 0
