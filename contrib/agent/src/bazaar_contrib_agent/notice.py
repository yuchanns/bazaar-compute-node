"""Render content-free inbox updates for turn starts and steering."""

from collections.abc import Sequence

from bazaar_contrib_messages.models import Message
from bazaar_contrib_messages.states import ChannelTargetKind

from .instruction import template


def inbox_notice(
    messages: Sequence[Message],
    *,
    total_unread_count: int,
    closing_bracket_on_own_line: bool,
    upgrade_version: str | None = None,
    installed_version: str | None = None,
) -> str:
    if not messages:
        raise ValueError("inbox notice requires at least one changed message")
    if total_unread_count < len(messages):
        raise ValueError("total unread count cannot be smaller than changed messages")

    buckets: dict[str, list[Message]] = {}
    for message in messages:
        buckets.setdefault(message.target, []).append(message)

    rows: list[tuple[int, str, dict[str, object]]] = []
    for target, changed in buckets.items():
        ordered = sorted(changed, key=lambda message: (message.seq, message.message_id))
        first = ordered[0]
        latest = ordered[-1]
        flags: set[str] = set()
        if any(message.target_kind is ChannelTargetKind.DM for message in ordered):
            flags.add("dm")
        if any(message.mentions_agent for message in ordered):
            flags.add("mention")
        if any(message.metadata.get("threaded") is True for message in ordered):
            flags.add("thread")
        rows.append(
            (
                latest.seq,
                target,
                {
                    "target": target,
                    "pending_count": len(ordered),
                    "first_id": first.message_id[:8],
                    "latest_sender": (
                        latest.sender.label if latest.sender is not None else None
                    ),
                    "latest_id": latest.message_id[:8],
                    "flags": sorted(flags),
                },
            )
        )

    rows.sort(key=lambda row: (-row[0], row[1]))
    return template("inbox_notice.tpl").render(
        {
            "distribution": "bazaar-compute-node",
            "total_unread_count": total_unread_count,
            "rows": [row[2] for row in rows],
            "upgrade_version": upgrade_version,
            "installed_version": installed_version,
            "closing_bracket_on_own_line": closing_bracket_on_own_line,
        }
    )
