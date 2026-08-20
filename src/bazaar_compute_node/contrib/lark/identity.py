from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ...core.channel import ChannelIdentity


@dataclass(frozen=True, slots=True)
class LarkBotIdentity:
    open_id: str
    app_name: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_text(self.open_id, "bot open_id")
        if self.app_name is not None:
            _validate_identity_text(self.app_name, "bot app_name")

    @property
    def name(self) -> str | None:
        return self.app_name

    def as_channel_identity(self) -> ChannelIdentity:
        return ChannelIdentity(id=self.open_id, name=self.app_name)


def parse_bot_info(payload: object) -> LarkBotIdentity:
    if not isinstance(payload, Mapping):
        raise TypeError("Lark bot info must be an object")
    bot = payload.get("bot")
    if not isinstance(bot, Mapping):
        data = payload.get("data")
        bot = data.get("bot") if isinstance(data, Mapping) else None
    if not isinstance(bot, Mapping):
        if "open_id" in payload:
            bot = payload
        else:
            raise ValueError("Lark bot info is missing bot data")

    open_id = bot.get("open_id")
    if not isinstance(open_id, str) or not open_id:
        raise ValueError("Lark bot info is missing open_id")
    app_name = bot.get("app_name")
    if app_name is None:
        app_name = bot.get("name")
    if app_name is not None and (not isinstance(app_name, str) or not app_name):
        raise ValueError("Lark bot info contains an invalid display name")
    return LarkBotIdentity(open_id=open_id, app_name=app_name)


def _validate_identity_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty text")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} must not contain line breaks")


__all__ = ["LarkBotIdentity", "parse_bot_info"]
