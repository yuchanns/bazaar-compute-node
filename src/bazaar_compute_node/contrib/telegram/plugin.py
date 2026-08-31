from __future__ import annotations

import os

from ...core.channel import ChannelContext, IChannel, IChannelBuilder
from .outbound import TelegramOutboundChannel


class TelegramBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel:
        token_env = context.options.get("token_env")
        if not isinstance(token_env, str) or not token_env:
            raise ValueError("agent.channel.token_env is required for telegram")
        activity = context.options.get("activity", False)
        if not isinstance(activity, bool):
            raise TypeError("agent.channel.activity must be a boolean for telegram")
        token = os.environ.get(token_env)
        if token is None or not token.strip():
            raise ValueError(f"telegram credential environment is missing: {token_env}")
        return TelegramOutboundChannel(context, token=token, activity=activity)


builder = TelegramBuilder()


__all__ = ["TelegramBuilder", "builder"]
