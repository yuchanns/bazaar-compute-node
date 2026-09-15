from __future__ import annotations

_PROVIDER_PREFIX = "wecom"


def conversation_thread_id(bot_id: str, conversation: str) -> str:
    """Address a conversation by the bot it lives on and WeCom's own chat id."""

    return f"{_PROVIDER_PREFIX}:{bot_id}:{conversation}"


def parse_provider_thread_id(value: str) -> tuple[str, str]:
    """Split a provider thread id back into the bot id and the WeCom chat id."""

    if not isinstance(value, str) or not value:
        raise ValueError("WeCom provider_thread_id must be non-empty")
    parts = value.split(":", 2)
    if len(parts) != 3 or parts[0] != _PROVIDER_PREFIX or not parts[1] or not parts[2]:
        raise ValueError("WeCom provider_thread_id has invalid format")
    return parts[1], parts[2]


__all__ = ["conversation_thread_id", "parse_provider_thread_id"]
