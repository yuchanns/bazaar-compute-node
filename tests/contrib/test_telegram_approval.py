from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.approval import TelegramApprovalChannel
from bazaar_compute_node.contrib.telegram.identity import TelegramThreadIdentity
from bazaar_compute_node.core.channel import ChannelApprovalRequest, ChannelContext
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChannelTargetKind,
)


class _FakeApprovalApi:
    def __init__(self) -> None:
        self.sent: list[Mapping[str, object]] = []
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


def _request(sender_id: str | None) -> ChannelApprovalRequest:
    identity = TelegramThreadIdentity(
        bot_id=8688828365,
        chat_id=1956760814,
        topic_id=0,
    )
    return ChannelApprovalRequest(
        approval=ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            runtime_session_id="runtime-1",
            action="command_execution",
            created_at_ms=1,
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
            "chat": {"id": 1956760814},
        },
    }


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
    monkeypatch.setattr(channel, "_bot_id", 8688828365)

    task = asyncio.create_task(
        channel.request_approval(_request("1956760814"), timeout=1)
    )
    while not channel._pending_approvals:
        await asyncio.sleep(0)
    token = next(iter(channel._pending_approvals))

    await channel._handle_callback_query(
        _callback(query_id="other-user", token=token, sender_id=6820994803)
    )
    assert not task.done()
    assert channel._last_update_disposition == "approval_callback_sender_mismatch"

    await channel._handle_callback_query(
        _callback(query_id="original-user", token=token, sender_id=1956760814)
    )
    result = await task

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
    monkeypatch.setattr(channel, "_bot_id", 8688828365)

    with pytest.raises(ValueError, match="original sender id"):
        await channel.request_approval(_request(None), timeout=1)
