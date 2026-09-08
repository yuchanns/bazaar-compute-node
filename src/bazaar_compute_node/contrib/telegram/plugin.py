from __future__ import annotations

import os
from typing import cast

from ...core.channel import ChannelContext, IChannel, IChannelBuilder
from .outbound import TelegramOutboundChannel


class TelegramBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel:
        token_env = context.options.get("token_env")
        if not isinstance(token_env, str) or not token_env:
            raise ValueError("agent.channel.token_env is required for telegram")
        token = os.environ.get(token_env)
        if token is None or not token.strip():
            raise ValueError(f"telegram credential environment is missing: {token_env}")
        raw_allowed = context.options.get("allowed_sender_ids")
        if not isinstance(raw_allowed, list) or not raw_allowed:
            raise ValueError(
                "agent.channel.allowed_sender_ids is required for telegram "
                "and must be a non-empty list"
            )
        # A username can be changed at will, so it cannot carry authority;
        # only the numeric id can.
        if any(
            not isinstance(sender_id, int) or isinstance(sender_id, bool)
            for sender_id in cast(list[object], raw_allowed)
        ):
            raise ValueError(
                "agent.channel.allowed_sender_ids must hold Telegram user ids"
            )
        return TelegramOutboundChannel(
            context,
            token=token,
            allowed_sender_ids=frozenset(cast(list[int], raw_allowed)),
        )


builder = TelegramBuilder()


__all__ = ["TelegramBuilder", "builder"]
