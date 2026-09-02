from __future__ import annotations

from bazaar_compute_node.core.approval import (
    approval_action_text,
    approval_description_text,
)
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator


def test_approval_description_is_localized_alongside_the_action() -> None:
    details = {
        "reason": "The command needs confirmation.",
        "command": "python -m pytest",
        "cwd": "/workspace",
    }

    english = create_translator(ENGLISH)
    chinese = create_translator(SIMPLIFIED_CHINESE)
    assert approval_action_text(english, "command_execution") == "command execution"
    assert approval_action_text(chinese, "command_execution") == "命令执行"

    rendered_english = approval_description_text(english, details)
    rendered_chinese = approval_description_text(chinese, details)
    for rendered in (rendered_english, rendered_chinese):
        for value in details.values():
            assert value in rendered
    assert "Working directory" in rendered_english
    assert "工作目录" in rendered_chinese


def test_approval_description_covers_only_the_details_it_was_given() -> None:
    translator = create_translator(ENGLISH)

    rendered = approval_description_text(translator, {"cwd": "/tmp"})

    assert "/tmp" in rendered
    assert "Command" not in rendered


def test_approval_description_is_bounded() -> None:
    translator = create_translator(ENGLISH)

    rendered = approval_description_text(translator, {"reason": "x" * 8_000})

    assert len(rendered) == 4_000
    assert rendered.endswith("…")
