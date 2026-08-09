from __future__ import annotations

import os

from ...core.channel import ChannelContext, IChannel
from .channel import WeComChannel


def create_channel(context: ChannelContext) -> IChannel:
    bot_id = context.options.get("bot_id")
    websocket_url = context.options.get("websocket_url")
    if not isinstance(bot_id, str) or not bot_id:
        raise ValueError("channel.wecom.bot_id is required")
    if websocket_url is not None and (
        not isinstance(websocket_url, str) or not websocket_url
    ):
        raise ValueError("channel.wecom.websocket_url must be non-empty text")
    secret = os.environ.get("BCN_WECOM_BOT_SECRET")
    if not secret:
        raise ValueError("BCN_WECOM_BOT_SECRET is required")
    return WeComChannel(
        context,
        bot_id=bot_id,
        secret=secret,
        websocket_url=websocket_url or "wss://openws.work.weixin.qq.com",
    )


__all__ = ["create_channel"]
