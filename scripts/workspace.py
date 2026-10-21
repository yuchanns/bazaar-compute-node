"""Discover uv workspace members and their dependency ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
lazy import subprocess
lazy import sys
lazy import tomllib

from pydantic import BaseModel, Field
lazy from packaging.markers import Marker


class Dependency(BaseModel):
    id: str
    marker: str | None = None


class Source(BaseModel):
    editable: Path | None = None
    directory: Path | None = None
    virtual: Path | None = None


class Node(BaseModel):
    kind: str | dict[str, str]
    source: Source = Field(default_factory=Source)
    dependencies: list[Dependency] = Field(default_factory=list)
    dependency_groups: list[Dependency] = Field(default_factory=list)
    optional_dependencies: list[Dependency] = Field(default_factory=list)


class Tests(BaseModel):
    testpaths: list[Path] = Field(default_factory=lambda: [Path("tests")])


class CI(BaseModel):
    platforms: tuple[Literal["linux", "macos", "windows"], ...] = ("linux",)
    skip: bool = False


class Member(BaseModel):
    name: str
    path: Path
    id: str

    @property
    def ci(self) -> CI:
        with (self.path / "pyproject.toml").open("rb") as handle:
            config = tomllib.load(handle)
        return CI.model_validate(config.get("tool", {}).get("bcn", {}).get("ci", {}))

    @property
    def tests(self) -> list[Path]:
        with (self.path / "pyproject.toml").open("rb") as handle:
            config = tomllib.load(handle)
        tests = Tests.model_validate(
            config.get("tool", {}).get("pytest", {}).get("ini_options", {})
        )
        return [self.path / path for path in tests.testpaths]


class Metadata(BaseModel):
    workspace_root: Path
    workspace: Dependency
    members: list[Member]
    resolution: dict[str, Node]

    def closure(self, id: str, *, development: bool = False) -> set[str]:
        pending = [id]
        if development:
            pending.append(self.workspace.id)
        visited: set[str] = set()
        while pending:
            id = pending.pop()
            if id in visited:
                continue
            visited.add(id)
            node = self.resolution[id]
            pending.extend(
                dependency.id
                for dependency in node.dependencies
                if development
                or dependency.marker is None
                or Marker(dependency.marker).evaluate()
            )
            if development:
                pending.extend(group.id for group in node.dependency_groups)
                pending.extend(extra.id for extra in node.optional_dependencies)
        return visited

    def affected(self, base: str, head: str) -> list[Member]:
        base = subprocess.check_output(
            ["git", "merge-base", base, head], text=True
        ).strip()
        paths = (
            subprocess.check_output(
                ["git", "diff", "--name-only", "--no-renames", "-z", base, head]
            )
            .decode()
            .split("\0")
        )
        shared = {
            "pyproject.toml",
            "uv.lock",
            "uv.toml",
            ".python-version",
            "LICENSE",
            "pyrightconfig.json",
            "ruff.toml",
            ".ruff.toml",
        }
        if any(
            path in shared or path.startswith((".github/", "scripts/"))
            for path in paths
        ):
            return self.members

        closures = {
            member.id: self.closure(member.id, development=True)
            for member in self.members
        }
        local: dict[Path, set[str]] = {}
        for id in set().union(*closures.values()):
            node = self.resolution[id]
            if node.kind != "package":
                continue
            path = node.source.editable or node.source.directory
            if path is not None and path.is_relative_to(self.workspace_root):
                local.setdefault(path, set()).add(id)
        changed: set[str] = set()
        for path in filter(None, paths):
            path = self.workspace_root / path
            owners = [
                directory for directory in local if path.is_relative_to(directory)
            ]
            if owners:
                owner = max(owners, key=lambda directory: len(directory.parts))
                if owner == self.workspace_root and path.relative_to(owner).parts[
                    0
                ] in {"plans", "docs"}:
                    continue
                changed.update(local[owner])
        return [member for member in self.members if changed & closures[member.id]]


def load_metadata(root: Path) -> Metadata:
    result = subprocess.run(
        [
            "uv",
            "--preview-features",
            "workspace-metadata",
            "workspace",
            "metadata",
            "--locked",
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        sys.stderr.write(result.stderr.decode(errors="replace"))
        result.check_returncode()
    metadata = Metadata.model_validate_json(result.stdout)
    metadata.members = [
        member
        for member in metadata.members
        if metadata.resolution[member.id].source.virtual is None
    ]
    return metadata
