"""Who may talk to an agent: what a conversation stands as when it opens,
before anyone has looked at it."""

from __future__ import annotations

from dataclasses import dataclass

from .channel import IChannel
from .models import Review


@dataclass(frozen=True, slots=True)
class ReviewPolicy:
    """The chats each channel lets in by name, and whether anyone is there
    to ask about the rest.

    A chat on a channel's list is approved as it opens. One that is not is
    left pending when someone reviews (a control is configured, so a server
    can answer); when nobody does, an empty list means the channel lets
    everyone in, and a list that names anyone keeps the rest pending - the
    operator has said who is expected.

    Each channel is listed with its own chats: a message says which bot
    heard it, and the bot says which it is once it is up, so two bots of one
    kind keep two lists."""

    listed: tuple[tuple[IChannel, frozenset[str]], ...] = ()
    reviewed: bool = False

    def opening(self, channel_identity: str | None, chat_id: str | None) -> Review:
        for channel, chats in self.listed:
            identity = channel.get_identity()
            if identity is not None and identity.id == channel_identity:
                break
        else:
            chats = frozenset()
        if chat_id is not None and chat_id in chats:
            return Review.APPROVED
        if self.reviewed or chats:
            return Review.PENDING
        return Review.APPROVED


__all__ = ["ReviewPolicy"]
