from __future__ import annotations

import pytest

from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.rendering import TextTemplate


def test_developer_instructions_render_identity() -> None:
    # runtime context and identity are rendered
    context = DeveloperInstructionContext(
        agent_name="Test {{ agent }}",
        bot_names=("Test Bot",),
        agent_id="agent-1",
        runtime_session_id="runtime-1",
        runtime="test",
        workspace="/workspace",
    )

    rendered = context.render()

    assert rendered.startswith(
        "You're Test Bot, A.K.A Test {{ agent }}, an AI agent in bcn "
    )
    assert "- Agent ID: agent-1" in rendered
    assert "- Runtime session ID: runtime-1" in rendered
    assert "- Runtime: test" in rendered
    assert "- Workspace: /workspace" in rendered
    assert rendered.endswith("your current work.\n\n")

    # identity renders without a bot name
    rendered = DeveloperInstructionContext(
        agent_name="Test Agent",
        bot_names=(),
        agent_id="agent-1",
        runtime_session_id="runtime-1",
        runtime="test",
        workspace="/workspace",
    ).render()

    assert rendered.startswith("You're Test Agent, an AI agent in bcn ")

    # an agent on several bots is introduced by every name it goes by
    rendered = DeveloperInstructionContext(
        agent_name="Test Agent",
        bot_names=("Bot One", "Bot Two", "Bot Three"),
        agent_id="agent-1",
        runtime_session_id="runtime-1",
        runtime="test",
        workspace="/workspace",
    ).render()

    assert rendered.startswith(
        "You're Bot One, Bot Two and Bot Three, A.K.A Test Agent, an AI agent in bcn "
    )

    with pytest.raises(ValueError, match="bot_names must not contain line breaks"):
        DeveloperInstructionContext(
            agent_name="Test Agent",
            bot_names=("Bot One", "Bot\nTwo"),
            agent_id="agent-1",
            runtime_session_id="runtime-1",
            runtime="test",
            workspace="/workspace",
        )


def test_text_template_requires_exact_argument_keys() -> None:
    template = TextTemplate.from_source(
        "conditional",
        "{% if enabled %}{{ value }}{% endif %}",
    )

    assert template.variables == frozenset({"enabled", "value"})
    assert template.render({"enabled": True, "value": "{{ untouched }}"}) == (
        "{{ untouched }}"
    )
    with pytest.raises(ValueError, match="missing: value"):
        template.render({"enabled": False})
    with pytest.raises(ValueError, match="unexpected: extra"):
        template.render({"enabled": False, "value": "", "extra": ""})
