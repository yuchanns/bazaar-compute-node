from __future__ import annotations

from .model import Migration

# telegram and lark write the bot into the provider thread id, as
# `telegram:{bot_id}:{chat_id}:{topic_id}` and `lark:bot:{open_id}:chat:...`,
# so the bot a conversation lives on is the segment after the nine-character
# prefix either way. WeCom rows carry only the chat id and are filled in by
# the channel when it starts, since their bot comes from configuration.
BACKFILL_CHANNEL_IDENTITY_MIGRATION = Migration(
    version=29,
    name="backfill_channel_identity",
    statements=(
        """
        UPDATE channel_sessions
        SET channel_identity = SUBSTR(
            provider_thread_id, 10, INSTR(SUBSTR(provider_thread_id, 10), ':') - 1
        )
        WHERE channel_identity IS NULL
          AND (
            (channel = 'telegram' AND provider_thread_id LIKE 'telegram:%')
            OR (channel = 'lark' AND provider_thread_id LIKE 'lark:bot:%')
          )
          AND INSTR(SUBSTR(provider_thread_id, 10), ':') > 1
        """,
    ),
)

__all__ = ["BACKFILL_CHANNEL_IDENTITY_MIGRATION"]
