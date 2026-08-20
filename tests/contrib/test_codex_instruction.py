from __future__ import annotations

from pathlib import Path

from bazaar_compute_node.contrib.codex import build_thread_start_params
from bazaar_compute_node.core.instruction import DeveloperInstructionContext


def test_codex_thread_start_receives_rendered_reminder_instructions() -> None:
    workspace = Path("/workspace")
    rendered = DeveloperInstructionContext(
        agent_name="Test Agent",
        bot_name="provider-id(provider-bot)",
        agent_id="agent-test",
        runtime_session_id="runtime-session-test",
        runtime="codex",
        workspace=str(workspace),
    ).render()

    params = build_thread_start_params(
        rendered,
        cwd=workspace,
    )

    assert params["developerInstructions"] == rendered
    assert "### Reminders" in rendered
    assert "`bcc reminder schedule`" in rendered
    assert "`bcc reminder check`" in rendered
    assert "[reminder notice session=<session-id>]" in rendered
    assert (
        "Reminder firing never sends an external Channel message by itself" in rendered
    )
    assert "You're provider-id(provider-bot), A.K.A Test Agent" in rendered
