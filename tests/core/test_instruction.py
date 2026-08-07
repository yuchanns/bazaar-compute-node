from __future__ import annotations

from bazaar_compute_node.core.instruction import (
    DeveloperInstructionContext,
)


def test_developer_instructions_render_runtime_context() -> None:
    context = DeveloperInstructionContext(
        node_id="node-test",
        runtime_session_id="session-test",
        runtime="test-runtime",
        workspace="workspace-from-node",
    )

    rendered = context.render()

    assert "Node ID: node-test" in rendered
    assert "Runtime session ID: session-test" in rendered
    assert "Runtime: test-runtime" in rendered
    assert "Workspace: workspace-from-node" in rendered
    assert "{{" not in rendered
    assert "}}" not in rendered
