from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import aiohttp
import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark import api as lark_api
from bazaar_compute_node.contrib.lark.api import ClientConfig, LarkApi
from bazaar_compute_node.contrib.lark.approval import (
    LarkApprovalChannel,
    _approval_card_content,
    _parse_card_callback,
)
from bazaar_compute_node.contrib.lark.attachments import (
    LarkMention,
    LarkResourceDescriptor,
    _resource_download_type,
    project_lark_content,
)
from bazaar_compute_node.contrib.lark.channel import (
    LarkChannel,
    _normalize_parent_message,
    _TypingState,
)
from bazaar_compute_node.contrib.lark.frame import (
    Frame,
    FrameDecodeError,
    Header,
    decode_frame,
    encode_frame,
)
from bazaar_compute_node.contrib.lark.identity import (
    LarkBotIdentity,
    LarkThreadIdentity,
    parse_bot_info,
    parse_provider_thread_id,
)
from bazaar_compute_node.contrib.lark.outbound import (
    markdown_post_content,
    prepare_attachments,
    split_markdown,
)
from bazaar_compute_node.contrib.lark.plugin import LarkBuilder
from bazaar_compute_node.contrib.lark.transport import (
    DATA_METHOD,
    HEADER_TYPE,
    MESSAGE_EVENT,
    LarkAck,
)
from bazaar_compute_node.core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelIdentity,
)
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChannelTargetKind,
    OutboundAttachment,
    RuntimeEvent,
    RuntimeEventState,
)
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator


def _context(tmp_path: Path, options: dict[str, object]) -> ChannelContext:
    async def referenced_paths() -> set[str]:
        return set()

    return ChannelContext(
        agent_id="agent-test",
        attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
        options=options,
        workspace=lambda: tmp_path,
    )


def test_lark_frame_round_trip_skips_unknown_fields() -> None:
    frame = Frame(
        SeqID=7,
        LogID=8,
        service=9,
        method=DATA_METHOD,
        headers=[Header(key=HEADER_TYPE, value=MESSAGE_EVENT)],
        payload_encoding="json",
        payload_type="event",
        payload=b'{"event":"ready"}',
        LogIDNew="log-new",
    )

    encoded = encode_frame(frame)
    decoded = decode_frame(encoded + b"\x98\x06\x01")

    assert decoded == frame


def test_lark_frame_golden_fixture() -> None:
    fixture = Path(__file__).with_name("fixtures") / "lark_frame.hex"

    frame = decode_frame(bytes.fromhex(fixture.read_text()))

    assert frame.SeqID == 1
    assert frame.LogID == 2
    assert frame.service == 3
    assert frame.method == 0
    assert frame.headers == [Header(key="type", value="ping")]


def test_lark_frame_accepts_empty_optional_header_values() -> None:
    frame = Frame(
        SeqID=1,
        LogID=2,
        service=3,
        method=0,
        headers=[
            Header(key="type", value="ping"),
            Header(key="is_ack", value=""),
        ],
    )

    assert decode_frame(encode_frame(frame)) == frame


@pytest.mark.parametrize(
    "frame",
    (
        Frame(
            SeqID=1,
            LogID=1,
            service=1,
            method=0,
            headers=[Header(key="k" * 65, value="v")],
        ),
        Frame(
            SeqID=1,
            LogID=1,
            service=1,
            method=0,
            headers=[Header(key="key", value="v" * 4097)],
        ),
    ),
)
def test_lark_frame_rejects_oversized_headers(frame: Frame) -> None:
    with pytest.raises(FrameDecodeError):
        encode_frame(frame)


def test_lark_frame_rejects_malformed_and_missing_required_data() -> None:
    with pytest.raises(FrameDecodeError):
        decode_frame(b"not-a-protobuf-frame")
    with pytest.raises(FrameDecodeError):
        decode_frame(b"\x08\x01")


def test_lark_client_config_uses_bounded_provider_values() -> None:
    assert ClientConfig.from_payload({}) == ClientConfig()
    assert ClientConfig.from_payload(
        {
            "PingInterval": 10,
            "ReconnectCount": 3,
            "ReconnectInterval": 20,
            "ReconnectNonce": 4,
        }
    ) == ClientConfig(
        ping_interval=10,
        reconnect_count=3,
        reconnect_interval=20,
        reconnect_nonce=4,
    )
    with pytest.raises(ValueError):
        ClientConfig.from_payload({"PingInterval": 0})
    with pytest.raises(ValueError):
        ClientConfig.from_payload({"ReconnectCount": 10_001})


def test_lark_identity_prefers_app_name_and_supports_name_fallback() -> None:
    assert parse_bot_info(
        {"data": {"bot": {"open_id": "ou_1", "app_name": "App"}}}
    ) == LarkBotIdentity(open_id="ou_1", app_name="App")
    assert parse_bot_info({"bot": {"open_id": "ou_2", "name": "Legacy"}}).name == (
        "Legacy"
    )
    assert parse_bot_info({"open_id": "ou_3"}).as_channel_identity() == ChannelIdentity(
        id="ou_3"
    )
    thread = LarkThreadIdentity("ou_bot", "oc/chat", "omt/topic")
    assert (
        parse_provider_thread_id(
            thread.provider_thread_id,
            bot_open_id="ou_bot",
        )
        == thread
    )
    with pytest.raises(ValueError):
        parse_provider_thread_id(thread.provider_thread_id, bot_open_id="ou_other")


def test_lark_builder_rejects_missing_or_invalid_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BCN_TEST_LARK_SECRET", raising=False)
    with pytest.raises(ValueError, match="app_id is required"):
        LarkBuilder().build(
            _context(tmp_path, {"app_secret_env": "BCN_TEST_LARK_SECRET"})
        )
    with pytest.raises(ValueError, match="credential environment is missing"):
        LarkBuilder().build(
            _context(
                tmp_path,
                {"app_id": "cli_app", "app_secret_env": "BCN_TEST_LARK_SECRET"},
            )
        )
    with pytest.raises(ValueError, match="must be feishu or lark"):
        LarkBuilder().build(
            _context(
                tmp_path,
                {
                    "app_id": "cli_app",
                    "app_secret_env": "BCN_TEST_LARK_SECRET",
                    "region": "unknown",
                },
            )
        )


@pytest.mark.asyncio
async def test_lark_api_redacts_credentials_from_provider_errors() -> None:
    async with aiohttp.ClientSession() as session:
        api = LarkApi(
            session,
            app_id="cli_app",
            app_secret="app-secret",
            base_url="https://open.feishu.cn",
        )
        api._token_snapshot = lark_api._TokenSnapshot(
            token="tenant-token",
            expires_at=1.0,
            refresh_at=0.5,
        )

        error = api._safe_provider_message("app-secret tenant-token")

    assert error == "<redacted> <redacted>"


def test_lark_post_projection_preserves_rich_nodes() -> None:
    projection = project_lark_content(
        "post",
        json.dumps(
            {
                "zh_cn": {
                    "title": "Title",
                    "content": [
                        [
                            {"tag": "text", "text": "Hello "},
                            {
                                "tag": "at",
                                "user_id": "ou_user",
                            },
                            {
                                "tag": "a",
                                "text": "link",
                                "href": "https://example.invalid",
                            },
                            {
                                "tag": "img",
                                "image_key": "img-key",
                                "alt": "photo.png",
                            },
                        ]
                    ],
                }
            }
        ),
        mentions={
            "@_user_1": LarkMention(
                key="@_user_1",
                open_id="ou_user",
                display_name="Alice",
            )
        },
        bot_open_id="ou_bot",
    )

    assert projection.message_type == "post"
    assert projection.body == (
        "Title\nHello @Alicelink (https://example.invalid)[image: photo.png]"
    )
    assert projection.resources == (
        LarkResourceDescriptor(
            file_key="img-key",
            resource_type="image",
            name="photo.png",
        ),
    )


@pytest.mark.parametrize(
    ("resource_type", "expected"),
    (
        ("image", "image"),
        ("file", "file"),
        ("audio", "file"),
        ("media", "file"),
        ("sticker", None),
    ),
)
def test_lark_resource_download_type_matches_provider_api(
    resource_type: str,
    expected: str | None,
) -> None:
    assert _resource_download_type(resource_type) == expected


def test_lark_parent_message_normalizes_message_api_shape() -> None:
    parent = _normalize_parent_message(
        {
            "message_id": "om_parent",
            "msg_type": "image",
            "body": {"content": '{"image_key":"img-parent"}'},
            "sender": {
                "id": "ou_sender",
                "id_type": "open_id",
                "sender_type": "user",
                "tenant_key": "tenant",
            },
        }
    )

    assert parent["message_type"] == "image"
    assert parent["content"] == '{"image_key":"img-parent"}'
    sender = parent["sender"]
    assert isinstance(sender, dict)
    assert sender["sender_id"] == {"open_id": "ou_sender"}


def test_lark_outbound_markdown_uses_post_locale_map() -> None:
    encoded = markdown_post_content("hello **世界**")

    assert json.loads(encoded) == {
        "zh_cn": {
            "title": "",
            "content": [[{"tag": "md", "text": "hello **世界**"}]],
        }
    }


def test_lark_outbound_markdown_splits_by_codepoints_and_preserves_fences() -> None:
    content = "intro\n\n```python\n" + ("🙂" * 40) + "\n```\nend"

    parts = split_markdown(content, limit=30)

    assert len(parts) > 2
    assert all(len(part) <= 30 for part in parts)
    assert parts[0].startswith("intro")
    assert all(part.startswith("```python\n") for part in parts[1:-1])
    assert parts[-1].endswith("end")


def test_lark_outbound_attachment_preflight_checks_size_and_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "note.txt"
    payload = b"hello lark"
    path.write_bytes(payload)
    descriptor = OutboundAttachment(
        name=path.name,
        relative_path=path.name,
        media_type="text/plain",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    prepared = prepare_attachments(tmp_path, (descriptor,))

    assert prepared[0].message_type == "file"
    assert prepared[0].file_type == "stream"
    with prepared[0].open() as opened:
        assert opened.read() == payload

    path.write_bytes(b"changed!!!")
    with pytest.raises(ValueError, match="digest mismatch"):
        prepare_attachments(tmp_path, (descriptor,))


def test_lark_terminal_does_not_queue_reaction_removal(tmp_path: Path) -> None:
    channel = LarkChannel(
        _context(tmp_path, {}),
        app_id="cli_app",
        app_secret="app-secret",
        region="feishu",
        base_url="https://open.feishu.cn",
        timer_wheel=TimerWheel(),
    )
    session_id = "session-1"
    channel._stream_routes[session_id] = "om_message"
    channel._typing_states[session_id] = _TypingState(
        message_id="om_message",
        reaction_id="reaction-1",
    )

    channel.accept_turn_event(
        RuntimeEvent(
            created_at_ms=1,
            event_name="turn.completed",
            state=RuntimeEventState.COMPLETED,
        ),
        session_id=session_id,
    )

    assert session_id not in channel._stream_routes
    assert session_id not in channel._typing_states
    assert channel._typing_queue.empty()


def test_lark_approval_card_content_contains_bounded_action_values() -> None:
    request = ChannelApprovalRequest(
        approval=ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            runtime_session_id="runtime-1",
            action="command_execution",
            created_at_ms=1,
            description="Run the requested command.",
        ),
        target_kind=ChannelTargetKind.DM,
        provider_thread_id=LarkThreadIdentity("ou_bot", "oc_chat").provider_thread_id,
        provider_sender_id="ou_user",
    )

    card = json.loads(
        _approval_card_content(
            request,
            "approval-token",
            translator=create_translator(ENGLISH),
        )
    )

    assert card["schema"] == "2.0"
    assert card["config"] == {"update_multi": True}
    assert card["header"]["title"] == {
        "tag": "plain_text",
        "content": "Approval required",
    }
    elements = card["body"]["elements"]
    assert elements[0]["tag"] == "markdown"
    assert "command execution" in elements[0]["content"]
    columns = elements[1]["columns"]
    assert [column["elements"][0]["behaviors"][0]["value"] for column in columns] == [
        {"action": "approve", "token": "approval-token"},
        {"action": "reject", "token": "approval-token"},
    ]

    chinese_card = json.loads(
        _approval_card_content(
            request,
            "approval-token",
            translator=create_translator(SIMPLIFIED_CHINESE),
        )
    )
    assert chinese_card["header"]["title"]["content"] == "需要审批"
    assert [
        column["elements"][0]["text"]["content"]
        for column in chinese_card["body"]["elements"][1]["columns"]
    ] == ["✅ 批准", "❎ 拒绝"]

    resolved_card = json.loads(
        _approval_card_content(
            request,
            "approval-token",
            translator=create_translator(ENGLISH),
            decision=ApprovalDecision.APPROVED,
        )
    )
    assert resolved_card["body"]["elements"][1] == {
        "tag": "markdown",
        "content": "Approved",
    }


def test_lark_card_callback_parser_validates_card_button_shape() -> None:
    payload = {
        "header": {"event_id": "event-1"},
        "event": {
            "operator": {"open_id": "ou_user"},
            "context": {
                "open_chat_id": "oc_chat",
                "open_message_id": "om_prompt",
            },
            "action": {
                "tag": "button",
                "value": {"action": "approve", "token": "approval-token"},
            },
            "token": "card-update-token",
        },
    }

    assert _parse_card_callback(payload) == (
        "event-1",
        "ou_user",
        "oc_chat",
        "om_prompt",
        "approve",
        "approval-token",
        "card-update-token",
    )
    payload["event"]["action"]["tag"] = "select_static"
    assert _parse_card_callback(payload) is None


@pytest.mark.asyncio
async def test_lark_card_action_event_frame_uses_card_callback_dispatch(
    tmp_path: Path,
) -> None:
    channel = LarkApprovalChannel(
        _context(tmp_path, {}),
        app_id="cli_app",
        app_secret="app-secret",
        region="feishu",
        base_url="https://open.feishu.cn",
        timer_wheel=TimerWheel(),
    )
    payload = {
        "header": {"event_id": "event-1", "event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_user"},
            "context": {
                "open_chat_id": "oc_chat",
                "open_message_id": "om_prompt",
            },
            "action": {
                "tag": "button",
                "value": {"action": "approve", "token": "approval-token"},
            },
            "token": "card-update-token",
        },
    }

    ack = await channel._handle_event("event", payload, object())

    assert isinstance(ack, LarkAck)
    assert ack.payload is not None
    assert channel.health["approval_callbacks"] == 1


def test_lark_card_ack_encodes_cardkit_toast(tmp_path: Path) -> None:
    channel = LarkApprovalChannel(
        _context(tmp_path, {}),
        app_id="cli_app",
        app_secret="app-secret",
        region="feishu",
        base_url="https://open.feishu.cn",
        timer_wheel=TimerWheel(),
    )

    ack = channel._card_ack("Approved")
    assert ack.payload is not None
    envelope = json.loads(ack.payload)
    toast = json.loads(base64.b64decode(envelope["data"]))

    assert envelope["code"] == 200
    assert toast == {"toast": {"type": "success", "content": "Approved"}}
