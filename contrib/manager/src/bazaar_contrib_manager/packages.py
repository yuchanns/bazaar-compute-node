"""Resolve one complete environment and install the resulting lock unchanged."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy import re
lazy import shutil
lazy import sys
lazy import tomllib
lazy from pathlib import Path
lazy from urllib.parse import unquote, urlsplit

lazy import tomli_w
lazy from bazaar_contrib_config import (
    ConfigDocument,
    ConfigurationEnvironment,
    ContribPackage,
)
lazy from packaging.requirements import Requirement
lazy from packaging.tags import sys_tags
lazy from packaging.utils import canonicalize_name, parse_wheel_filename

lazy from .contracts import EnvironmentInfo, EnvironmentPlan, ManagementError, Package


class Packages:
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.python = Path(sys.executable)
        self.prefix = Path(sys.prefix)
        self.uv: str | None = None
        self.tool = False

    async def command(self, *arguments: str, cwd: Path | None = None) -> str:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            raise
        if process.returncode:
            detail = (errors or output).decode("utf-8", errors="replace").strip()
            detail = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", detail)
            raise ManagementError(
                f"package command failed ({process.returncode}): {detail}"
            )
        return output.decode("utf-8", errors="replace")

    async def initialize(self) -> None:
        self.uv = await asyncio.to_thread(shutil.which, "uv")
        if self.uv is None:
            raise ManagementError("uv is not on this node's PATH")
        directory = Path((await self.command(self.uv, "tool", "dir")).strip())
        self.tool = await asyncio.to_thread(
            lambda: (
                (directory / "bazaar-compute-node").resolve() == self.prefix.resolve()
            )
        )
        if not self.tool and sys.prefix == sys.base_prefix:
            raise ManagementError(
                "package operations require a uv tool or dedicated venv"
            )

    async def inspect(self, python: Path | None = None) -> EnvironmentInfo:
        output = await self.command(
            str(python or self.python), "-m", "bazaar_contrib_manager.environment"
        )
        return EnvironmentInfo.model_validate_json(output)

    async def plan(
        self, document: ConfigDocument, environment: ConfigurationEnvironment | None
    ) -> EnvironmentPlan:
        installed = await self.inspect()
        host = next(
            package
            for package in installed.packages
            if package.package == "bazaar-compute-node"
        )
        declared = {
            entry.distribution
            for entry in document.contribs
            if entry.package and entry.applies
        }
        required = {
            str(canonicalize_name(Requirement(requirement).name))
            for requirement in host.requirements
        }
        entries = (
            {entry.entry for entry in environment.entries}
            if environment is not None
            else set()
        )
        bootstrap = tuple(
            ContribPackage(
                package=(
                    f"{package.package} @ {package.url}"
                    if package.url is not None
                    else f"{package.package}=={package.version}"
                ),
                source=self.source,
            )
            for package in installed.packages
            if package.package not in declared | required | {host.package}
            and any(entry.name in entries for entry in package.entries)
        )
        return EnvironmentPlan(
            host=Package(
                package=host.package, source=self.source, version=f"=={host.version}"
            ),
            packages=(
                *(entry for entry in document.contribs if entry.package),
                *bootstrap,
            ),
        )

    async def satisfied(self, plan: EnvironmentPlan) -> bool:
        """Check declarations and the requested extras against actual installed metadata."""
        installed = await self.inspect()
        packages = {package.package: package for package in installed.packages}
        pending = [
            Requirement(entry.package) for entry in plan.packages if entry.applies
        ]
        pending.append(Requirement(plan.host.package + plan.host.version))
        checked: set[tuple[str, frozenset[str]]] = set()
        while pending:
            requirement = pending.pop()
            name = str(canonicalize_name(requirement.name))
            package = packages.get(name)
            if package is None or not requirement.specifier.contains(
                package.version, prereleases=True
            ):
                return False
            if requirement.url and package.url != requirement.url.split("#", 1)[0]:
                return False
            key = name, frozenset(requirement.extras)
            if key in checked:
                continue
            checked.add(key)
            for raw in package.requirements:
                dependency = Requirement(raw)
                if dependency.marker is None or any(
                    dependency.marker.evaluate({"extra": extra})
                    for extra in ("", *requirement.extras)
                ):
                    pending.append(dependency)
        return True

    async def resolve(
        self, plan: EnvironmentPlan, path: Path, *, upgrade: bool = False
    ) -> Path:
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        packages = (
            ContribPackage(
                package=plan.host.package + plan.host.version, source=plan.host.source
            ),
            *plan.packages,
        )
        indexes = list(
            dict.fromkeys(package.source or self.source for package in packages)
        )
        sources: dict[str, dict[str, str]] = {}
        for package in packages:
            requirement = Requirement(package.package)
            if requirement.url or not package.applies:
                continue
            source = {"index": f"source-{indexes.index(package.source or self.source)}"}
            name = package.distribution
            if name in sources and sources[name] != source:
                raise ManagementError(f"conflicting sources for {name}")
            sources[name] = source
        project = {
            "project": {
                "name": "bazaar-contrib-environment",
                "version": "0",
                "requires-python": ">=3.15",
                "dependencies": [package.package for package in packages],
            },
            "tool": {
                "uv": {
                    "index": [
                        {"name": f"source-{index}", "url": source}
                        for index, source in enumerate(indexes)
                    ],
                    "sources": sources,
                }
            },
        }
        await asyncio.to_thread(
            (path / "pyproject.toml").write_text, tomli_w.dumps(project), "utf-8"
        )
        lock = path / "pylock.toml"
        try:
            await asyncio.to_thread(shutil.copyfile, self.path / "pylock.toml", lock)
        except FileNotFoundError:
            pass
        await self.command(
            str(self.uv),
            "pip",
            "compile",
            str(path / "pyproject.toml"),
            "--python",
            str(self.python),
            "--format",
            "pylock.toml",
            "--output-file",
            str(lock),
            *(("--upgrade",) if upgrade else ()),
            cwd=path,
        )
        return lock

    async def stage(self, lock: Path) -> Path:
        path = lock.parent / "environment"
        await self.command(
            str(self.uv), "venv", "--python", str(self.python), str(path)
        )
        python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        await self.command(
            str(self.uv), "pip", "sync", "--python", str(python), str(lock)
        )
        return python

    async def install(self, plan: EnvironmentPlan, lock: Path) -> None:
        if self.tool:
            info = await self.inspect()
            document = tomllib.loads(
                await asyncio.to_thread(lock.read_text, encoding="utf-8")
            )
            tags = await asyncio.to_thread(
                lambda: {tag: index for index, tag in enumerate(sys_tags())}
            )
            requirements: dict[str, str] = {}
            for package in document["packages"]:
                candidates = []
                for wheel in package.get("wheels", ()):
                    filename = wheel.get("name") or (
                        Path(wheel["path"]).name
                        if "path" in wheel
                        else unquote(urlsplit(wheel["url"]).path.rsplit("/", 1)[-1])
                    )
                    wheel_tags = parse_wheel_filename(filename)[3]
                    priorities = [tags[tag] for tag in wheel_tags if tag in tags]
                    if priorities:
                        candidates.append((min(priorities), wheel))
                artifact = (
                    min(candidates, key=lambda item: item[0])[1]
                    if candidates
                    else package["archive"]
                    if "archive" in package
                    else package["directory"]
                    if "directory" in package
                    else package["sdist"]
                )
                if "path" in artifact:
                    path = await asyncio.to_thread(
                        (lock.parent / artifact["path"]).resolve
                    )
                    url = path.as_uri()
                else:
                    url = artifact["url"]
                digest = artifact.get("hashes", {}).get("sha256")
                if digest is not None:
                    url += f"#sha256={digest}"
                requirements[package["name"]] = f"{package['name']} @ {url}"
            path = lock.with_name("requirements.txt")
            await asyncio.to_thread(
                path.write_text, "\n".join(requirements.values()) + "\n", "utf-8"
            )
            await self.command(
                str(self.uv),
                "tool",
                "install",
                requirements[plan.host.package],
                "--with-requirements",
                str(path),
                "--python",
                str(info.base_python),
                cwd=lock.parent,
            )
        else:
            await self.command(
                str(self.uv),
                "pip",
                "sync",
                "--python",
                str(self.python),
                str(lock),
                cwd=lock.parent,
            )
