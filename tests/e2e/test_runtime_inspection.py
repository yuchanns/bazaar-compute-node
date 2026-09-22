from __future__ import annotations

import shutil
from uuid import uuid4

import pytest

from bazaar_compute_node.contrib.claude.plugin import builder as claude
from bazaar_compute_node.contrib.claude.runtime import ENVIRONMENT as CLAUDE_ENVIRONMENT
from bazaar_compute_node.contrib.codex.plugin import builder as codex
from bazaar_compute_node.contrib.codex.runtime import ENVIRONMENT as CODEX_ENVIRONMENT
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.runtime import IRuntimeBuilder, RuntimeCommandContext
from bazaar_compute_node.core.utils.command import platform_environment

pytestmark = pytest.mark.e2e


async def _run_command(*_: object) -> None:
    return None


def _context(agent_id: str, names: tuple[str, ...]) -> RuntimeCommandContext:
    return RuntimeCommandContext(
        run_command=_run_command,
        environment_for_session=lambda _: {},
        environment_for_probe=lambda: platform_environment(names),
        agent_id=agent_id,
        agent_name="Kana",
        bot_names=lambda: (),
    )


def _skill(directory: str, agent_id: str) -> None:
    """A skill of the agent's own workspace, where the runtime looks."""

    skill = resolve_workspace_dir(agent_id) / directory / "skills" / "probe-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: probe-skill\ndescription: A skill of this workspace\n---\nBody.\n"
    )


@pytest.mark.asyncio
async def test_real_codex_says_its_version_and_models() -> None:
    """The node asks Codex what it is without opening a session: the version
    it prints, the models `model/list` names over every page with the efforts
    each takes, and which one it answers as unasked."""

    if shutil.which("codex") is None:
        pytest.fail("codex CLI is required for the inspection integration test")
    said = await codex.inspect(timeout=60)
    listed = await codex.models(timeout=60)

    assert said.available and said.version and listed.error is None
    assert listed.models and all(model.id and model.name for model in listed.models)
    assert any(model.efforts for model in listed.models)
    assert [model for model in listed.models if model.default]


@pytest.mark.asyncio
async def test_real_claude_marks_the_model_it_answers_as_unasked() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the inspection integration test")
    listed = await claude.models(timeout=60)

    assert listed.error is None
    assert [model for model in listed.models if model.default]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("builder", "executable", "directory", "names"),
    [
        (codex, "codex", ".agents", CODEX_ENVIRONMENT),
        (claude, "claude", ".claude", CLAUDE_ENVIRONMENT),
    ],
)
async def test_an_idle_agent_is_asked_for_the_skills_of_its_workspace(
    builder: IRuntimeBuilder,
    executable: str,
    directory: str,
    names: tuple[str, ...],
) -> None:
    """With no session up, the runtime is started in the agent's workspace
    for the asking and says the skill found there is the workspace's."""

    if shutil.which(executable) is None:
        pytest.fail(f"{executable} CLI is required for the inspection integration test")
    agent_id = f"inspect-e2e-{uuid4()}"
    _skill(directory, agent_id)
    runtime = builder.build(_context(agent_id, names))

    described = await runtime.describe(timeout=60)

    assert ("probe-skill", "workspace") in {
        (skill.name, skill.source) for skill in described.skills
    }
