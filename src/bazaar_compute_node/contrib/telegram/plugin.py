from __future__ import annotations

import os

from ...core.channel import ChannelContext, IChannel, IChannelBuilder
from .channel import TelegramChannel


class TelegramBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel:
        token = os.environ.get("BCN_TELEGRAM_BOT_TOKEN")
        if token is None or not token.strip():
            raise ValueError("BCN_TELEGRAM_BOT_TOKEN is required")
        return TelegramChannel(context, token=token)


builder = TelegramBuilder()


__all__ = ["TelegramBuilder", "builder"]
