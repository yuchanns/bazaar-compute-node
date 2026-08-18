from __future__ import annotations

import os

from ...core.channel import ChannelContext, IChannel, IChannelBuilder
from .channel import WeComChannel


class WeComBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel:
        bot_id = context.options.get("bot_id")
        secret_env = context.options.get("secret_env")
        websocket_url = context.options.get("websocket_url")
        if not isinstance(bot_id, str) or not bot_id:
            raise ValueError("agent.channel.bot_id is required for wecom")
        if not isinstance(secret_env, str) or not secret_env:
            raise ValueError("agent.channel.secret_env is required for wecom")
        if websocket_url is not None and (
            not isinstance(websocket_url, str) or not websocket_url
        ):
            raise ValueError("agent.channel.websocket_url must be non-empty text")
        secret = os.environ.get(secret_env)
        if secret is None or not secret.strip():
            raise ValueError(f"wecom credential environment is missing: {secret_env}")
        return WeComChannel(
            context,
            bot_id=bot_id,
            secret=secret,
            websocket_url=websocket_url or "wss://openws.work.weixin.qq.com",
        )


builder = WeComBuilder()


__all__ = ["WeComBuilder", "builder"]
