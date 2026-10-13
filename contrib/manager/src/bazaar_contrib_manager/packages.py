"""Resolve one complete environment and install the resulting lock unchanged."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy import re
lazy import shutil
lazy import sys
lazy import tomllib
lazy from collections.abc import Sequence
lazy from pathlib import Path
lazy from urllib.parse import unquote, urlsplit

lazy import tomli_w
lazy from packaging.requirements import Requirement
lazy from packaging.tags import sys_tags
lazy from packaging.utils import canonicalize_name, parse_wheel_filename

lazy from .contracts import EnvironmentInfo, ManagementError, Manifest, Package


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

    async def manifest(self, entries: Sequence[Package]) -> Manifest:
        path = self.path / "manifest.json"
        try:
            return Manifest.model_validate_json(
                await asyncio.to_thread(path.read_bytes)
            )
        except FileNotFoundError:
            pass
        installed = await self.inspect()
        packages = {package.package: package for package in installed.packages}
        host = packages["bazaar-compute-node"]
        required = {host.package}
        pending = [host]
        while pending:
            current = pending.pop()
            for raw in current.requirements:
                requirement = Requirement(raw)
                if requirement.marker is not None and not requirement.marker.evaluate():
                    continue
                name = str(canonicalize_name(requirement.name))
                if name not in required and name in packages:
                    required.add(name)
                    pending.append(packages[name])
        extra: list[Package] = []
        for package in packages.values():
            if not package.entries or package.package in required:
                continue
            sources = {
                entry.source for entry in entries if entry.package == package.package
            }
            if len(sources) != 1:
                raise ManagementError(
                    f"select a source for already installed {package.package} before managing its environment"
                )
            extra.append(
                Package(
                    package=package.package,
                    source=next(iter(sources)),
                    version=f"=={package.version}",
                )
            )
        return Manifest(
            host=Package(
                package=host.package, source=self.source, version=f"=={host.version}"
            ),
            packages=tuple(extra),
            installed={
                package.package: package.version for package in installed.packages
            },
        )

    async def resolve(self, manifest: Manifest, path: Path) -> Path:
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        packages = (manifest.host, *manifest.packages)
        indexes = list(dict.fromkeys(package.source for package in packages))
        project = {
            "project": {
                "name": "bazaar-contrib-environment",
                "version": "0",
                "requires-python": ">=3.15",
                "dependencies": [
                    package.package + package.version for package in packages
                ],
            },
            "tool": {
                "uv": {
                    "index": [
                        {"name": f"source-{index}", "url": source}
                        for index, source in enumerate(indexes)
                    ],
                    "sources": {
                        package.package: {
                            "index": f"source-{indexes.index(package.source)}"
                        }
                        for package in packages
                    },
                }
            },
        }
        await asyncio.to_thread(
            (path / "pyproject.toml").write_text, tomli_w.dumps(project), "utf-8"
        )
        lock = path / "pylock.toml"
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
            "--upgrade",
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

    async def install(self, manifest: Manifest, lock: Path) -> None:
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
                    filename = unquote(urlsplit(wheel["url"]).path.rsplit("/", 1)[-1])
                    wheel_tags = parse_wheel_filename(filename)[3]
                    priorities = [tags[tag] for tag in wheel_tags if tag in tags]
                    if priorities:
                        candidates.append((min(priorities), wheel))
                artifact = (
                    min(candidates, key=lambda item: item[0])[1]
                    if candidates
                    else package["sdist"]
                )
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
                requirements[manifest.host.package],
                "--with-requirements",
                str(path),
                "--python",
                str(info.base_python),
                "--refresh-package",
                manifest.host.package,
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
