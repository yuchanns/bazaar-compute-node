from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points, version

from bazaar_compute_node import __version__
from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: package_smoke.py VERSION")

    expected_version = sys.argv[1]
    distribution_version = version("bazaar-compute-node")
    result = subprocess.run(
        ["bcn", "--version"],
        capture_output=True,
        check=True,
        text=True,
    )

    if distribution_version != expected_version:
        raise SystemExit(
            f"distribution version {distribution_version!r} != {expected_version!r}"
        )
    if __version__ != expected_version:
        raise SystemExit(f"runtime version {__version__!r} != {expected_version!r}")
    if result.stdout.strip() != f"bcn {expected_version}":
        raise SystemExit(f"unexpected bcn --version output: {result.stdout!r}")

    instructions = DeveloperInstructionContext(
        agent_name="Package Smoke Agent",
        bot_name="Package Smoke Bot",
        agent_id="agent-1",
        runtime_session_id="runtime-1",
        runtime="codex",
        workspace="/workspace",
    ).render()
    if not instructions.startswith(
        "You're Package Smoke Bot, A.K.A Package Smoke Agent, an AI agent in bcn "
    ):
        raise SystemExit("developer instruction resource did not render")
    if (
        create_translator(ENGLISH).text(
            "runtime.error.failed",
            {"error": "package smoke"},
        )
        != "Execution failed: package smoke"
    ):
        raise SystemExit("English locale resource did not render")
    if (
        create_translator(SIMPLIFIED_CHINESE).text(
            "runtime.error.failed",
            {"error": "package smoke"},
        )
        != "执行失败：package smoke"
    ):
        raise SystemExit("Simplified Chinese locale resource did not render")

    lark_entry_point = next(
        (
            item
            for item in entry_points(group="bazaar_compute_node.channels")
            if item.name == "lark"
        ),
        None,
    )
    if lark_entry_point is None:
        raise SystemExit("lark channel entry point is missing")
    lark_entry_point.load()


if __name__ == "__main__":
    main()
