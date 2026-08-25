from __future__ import annotations

from bazaar_compute_node.core.instruction import DeveloperInstructionContext


def test_developer_instructions_remove_legacy_resource_notice_surfaces() -> None:
    rendered = DeveloperInstructionContext(
        agent_name="Test Agent",
        bot_name=None,
        agent_id="agent-1",
        runtime_session_id="runtime-1",
        runtime="test",
        workspace="/workspace",
    ).render()

    assert (
        "**Reminders** — `bcc reminder schedule`, `bcc reminder list`, "
        "`bcc reminder snooze`, `bcc reminder update`, `bcc reminder cancel`."
    ) in rendered
    assert "bcc reminder check" not in rendered
    assert "[reminder notice" not in rendered
    assert "Reminders pending:" not in rendered
    assert "Reminder occurrences" not in rendered
    assert "does not call the external Channel" in rendered

    assert "bcc handoff" not in rendered
    assert "[handoff notice" not in rendered
    assert "Handoffs pending:" not in rendered
