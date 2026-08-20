from __future__ import annotations

from pathlib import Path

import aiohttp
import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark import api as lark_api
from bazaar_compute_node.contrib.lark.api import ClientConfig, LarkApi
from bazaar_compute_node.contrib.lark.frame import (
    Frame,
    FrameDecodeError,
    Header,
    decode_frame,
    encode_frame,
)
from bazaar_compute_node.contrib.lark.identity import (
    LarkBotIdentity,
    parse_bot_info,
)
from bazaar_compute_node.contrib.lark.plugin import LarkBuilder
from bazaar_compute_node.contrib.lark.transport import (
    DATA_METHOD,
    HEADER_TYPE,
    MESSAGE_EVENT,
)
from bazaar_compute_node.core.channel import ChannelContext, ChannelIdentity


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
