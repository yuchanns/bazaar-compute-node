from __future__ import annotations

import re
from pathlib import Path

from bazaar_compute_server.i18n import LANGUAGES, create_translator

_NODE_SRC = Path(__file__).resolve().parents[2] / "src" / "bazaar_compute_node"

# the names core builds at runtime, spelled out per the enum they come from
_DYNAMIC = (
    *(
        f"runtime.turn.{state}"
        for state in ("started", "completed", "failed", "cancelled", "unknown")
    ),
    *(
        f"channel.outbound.{state}"
        for state in ("pending", "queued", "sent", "partial", "failed", "unknown")
    ),
    *(f"runtime.process.{status}" for status in ("failed", "unknown")),
    *(f"runtime.process.{operation}.requested" for operation in ("start", "reconcile")),
    *(
        f"tool.{operation}.completed"
        for operation in (
            "bcc.message.send",
            "bcc.message.check",
            "bcc.message.read",
            "bcc.inbox.check",
            "bcc.thread.unfollow",
        )
    ),
    "tool.bcc.message.send.freshness_hold",
)


# a runtime's own word for a turn event rides in metadata; the audit names it
# runtime.turn.<state> whichever runtime it was
_RUNTIME_WORDS = re.compile(r"^(claudecode|codex|bcn)\.turn\.")


def _literal_event_names() -> set[str]:
    names: set[str] = set()
    for path in _NODE_SRC.rglob("*.py"):
        names.update(
            name
            for name in re.findall(
                r'event_name\s*=\s*"([a-z_.]+)"', path.read_text("utf-8")
            )
            if not _RUNTIME_WORDS.match(name)
        )
    return names


def test_every_event_the_node_emits_has_words_in_every_language() -> None:
    names = _literal_event_names() | set(_DYNAMIC)
    assert "tool_call.started" in names and "channel.inbound.persisted" in names
    for language in LANGUAGES:
        translator = create_translator(language)
        missing = sorted(name for name in names if not translator.has(f"event.{name}"))
        assert missing == [], f"{language}: {missing}"
