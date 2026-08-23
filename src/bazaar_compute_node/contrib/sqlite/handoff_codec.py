from __future__ import annotations

from typing import cast

import aiosqlite

from ...core.models import Handoff
from .codec import _optional_text, _required_text


def handoff_from_row(row: aiosqlite.Row) -> Handoff:
    return Handoff(
        handoff_id=_required_text(row["handoff_id"], "handoff_id"),
        command_id=_required_text(row["command_id"], "command_id"),
        source_session_id=_required_text(
            row["source_session_id"], "source_session_id"
        ),
        target_session_id=_required_text(
            row["target_session_id"], "target_session_id"
        ),
        source_message_id=_optional_text(row["source_message_id"], "source_message_id"),
        body=_required_text(row["body"], "body"),
        created_at_ms=cast(int, row["created_at_ms"]),
        read_at_ms=cast(int | None, row["read_at_ms"]),
    )


__all__ = ["handoff_from_row"]
