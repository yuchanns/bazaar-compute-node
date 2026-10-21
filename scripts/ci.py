"""Plan and verify affected distributions discovered from the uv workspace."""

from __future__ import annotations

import argparse
import json
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel
from pyright_lsp_check import configured_files, load_config
from workspace import load_metadata
lazy from workspace import Member, Metadata


class Project(BaseModel):
    name: str
    version: str
    license: str


def package(metadata: Metadata, member: Member) -> None:
    required = metadata.closure(member.id)
    members = [item for item in metadata.members if item.id in required]
    with TemporaryDirectory(prefix="bcn-ci-package-") as directory:
        directory = Path(directory)
        artifacts: dict[str, dict[str, Path]] = {"wheel": {}, "sdist": {}}
        for item in members:
            output = directory / item.name
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--package",
                    item.name,
                    "--no-sources",
                    "--out-dir",
                    str(output),
                ],
                check=True,
            )
            artifacts["wheel"][item.name] = next(output.glob("*.whl"))
            artifacts["sdist"][item.name] = next(output.glob("*.tar.gz"))

        with (member.path / "pyproject.toml").open("rb") as handle:
            project = Project.model_validate(tomllib.load(handle)["project"])
        smoke = next(
            (
                path / "package_smoke.py"
                for path in member.tests
                if (path / "package_smoke.py").is_file()
            ),
            None,
        )
        for kind, distributions in artifacts.items():
            command = ["uv", "run", "--isolated", "--no-project", "--python", "3.15"]
            for artifact in distributions.values():
                command.extend(["--with", str(artifact)])
            command.extend(
                [
                    str(Path(__file__).with_name("ci_package_smoke.py")),
                    project.name,
                    project.version,
                    project.license,
                ]
            )
            if smoke is not None:
                command.append(str(smoke))
            print(f"Verify {member.name} {kind}", flush=True)
            subprocess.run(command, cwd=directory, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--base")
    plan.add_argument("--head")
    plan.add_argument("--all-packages", action="store_true")
    plan.add_argument("--github-output", type=Path)
    test = commands.add_parser("test")
    test.add_argument("--system", action="store_true")
    checks = commands.add_parser("check")
    for command in (test, checks):
        selection = command.add_mutually_exclusive_group(required=True)
        selection.add_argument("--package")
        selection.add_argument("--all-packages", action="store_true")
    build = commands.add_parser("package")
    build.add_argument("--package", required=True)
    args = parser.parse_args()

    metadata = load_metadata(Path.cwd())
    if args.command == "plan":
        if args.all_packages:
            members = metadata.members
        else:
            if not args.base or not args.head:
                parser.error("plan requires --all-packages or both --base and --head")
            members = metadata.affected(args.base, args.head)
        platforms = {
            "linux": "ubuntu-latest",
            "macos": "macos-latest",
            "windows": "windows-latest",
        }
        tests = [
            {
                "package": member.name,
                "component": member.path.name,
                "os": platforms[platform],
            }
            for member in members
            if not member.ci.skip
            for platform in member.ci.platforms
        ]
        matrix = json.dumps(
            {
                "package": [member.name for member in members],
                "tests": {"include": tests},
            }
        )
        print(matrix)
        if args.github_output is not None:
            with args.github_output.open("a") as handle:
                handle.write(
                    f"matrix={matrix}\nhas_packages={str(bool(members)).lower()}\n"
                    f"has_tests={str(bool(tests)).lower()}\n"
                )
        return

    members = metadata.members
    if args.package:
        members = [member for member in members if member.name == args.package]
        if not members:
            parser.error(f"no workspace package named {args.package}")
    for member in members:
        if args.command == "check":
            files = [
                str(path)
                for path in configured_files(member.path, load_config(member.path))
            ]
            for command in [["ruff", "format", "--check"], ["ruff", "check"]]:
                subprocess.run(
                    ["uv", "run", "--no-sync", *command, *files],
                    cwd=member.path,
                    check=True,
                )
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    str(Path(__file__).with_name("pyright_lsp_check.py")),
                    "--package",
                    member.name,
                    str(metadata.workspace_root),
                ],
                cwd=metadata.workspace_root,
                check=True,
            )
            continue
        if args.command == "package":
            package(metadata, member)
            continue

        if any(path.exists() for path in member.tests):
            marker = "system" if args.system else "not e2e and not system"
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    str(Path(__file__).with_name("ci_pytest.py")),
                    "-c",
                    str(member.path / "pyproject.toml"),
                    "-m",
                    marker,
                ],
                cwd=member.path,
                check=False,
            )
            # pytest returns 5 when a package has no tests for the selected marker.
            if result.returncode not in (0, 5):
                raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
