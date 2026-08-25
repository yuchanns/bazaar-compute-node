from __future__ import annotations

import pytest

from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.rendering import TextTemplate


def test_developer_instructions_render_runtime_context_and_identity() -> None:
    context = DeveloperInstructionContext(
        agent_name="Test {{ agent }}",
        bot_name="Test Bot",
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


def test_developer_instructions_render_identity_without_bot_name() -> None:
    rendered = DeveloperInstructionContext(
        agent_name="Test Agent",
        bot_name=None,
        agent_id="agent-1",
        runtime_session_id="runtime-1",
        runtime="test",
        workspace="/workspace",
    ).render()

    assert rendered.startswith("You're Test Agent, an AI agent in bcn ")


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
