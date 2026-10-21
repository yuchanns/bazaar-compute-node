"""Prepare, build, verify and publish the public workspace distributions."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from email.parser import BytesParser
from graphlib import TopologicalSorter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import BaseModel, Field
from workspace import load_metadata

ReleaseVersion = Annotated[
    str,
    Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(rc[1-9][0-9]*)?$"
    ),
]


class Request(BaseModel):
    version: ReleaseVersion
    branch: str


class Project(BaseModel):
    name: str
    version: str
    license: str
    dependencies: list[str] = Field(default_factory=list)


class Package(BaseModel):
    name: str
    path: str
    license: str
    dependencies: list[str]
    platforms: tuple[str, ...]
    smoke: str | None


class Source(BaseModel):
    version: ReleaseVersion
    branch: str
    source_commit: str
    packages: list[Package]


class Distribution(BaseModel):
    package: str
    filename: str
    kind: Literal["wheel", "sdist"]
    sha256: str


class Manifest(Source):
    release_commit: str
    source_date_epoch: int
    files: list[Distribution]


class PublishedFile(BaseModel):
    filename: str
    digests: dict[str, str]


class PublishedFiles(BaseModel):
    urls: list[PublishedFile]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def plan(request: Request) -> Source:
    subprocess.run(
        ["git", "check-ref-format", "--branch", request.branch],
        capture_output=True,
        check=True,
    )
    saved = Path(".releases") / f"{request.version}.json"
    if saved.exists():
        source = Source.model_validate_json(saved.read_text())
        if source.branch != request.branch or source.version != request.version:
            raise ValueError("this version was prepared on another release branch")
        return source
    root = Path.cwd()
    metadata = load_metadata(root)
    packages: dict[str, Package] = {}
    for member in metadata.members:
        path = member.path.relative_to(root)
        if path.parts[0] not in {"app", "core", "contrib"}:
            continue
        project = Project.model_validate(
            tomllib.loads((member.path / "pyproject.toml").read_text())["project"]
        )
        if Version(request.version) < Version(project.version):
            raise ValueError(
                f"{project.name}: {request.version} precedes {project.version}"
            )
        smoke = next(
            (
                path / "package_smoke.py"
                for path in member.tests
                if (path / "package_smoke.py").is_file()
            ),
            None,
        )
        packages[member.name] = Package(
            name=member.name,
            path=path.as_posix(),
            license=project.license,
            dependencies=project.dependencies,
            platforms=member.ci.platforms,
            smoke=smoke.relative_to(root).as_posix() if smoke is not None else None,
        )
    graph: dict[str, set[str]] = {}
    for package in packages.values():
        dependencies: set[str] = set()
        for value in package.dependencies:
            requirement = Requirement(value)
            name = canonicalize_name(requirement.name)
            if name in packages:
                if not requirement.specifier.contains(
                    request.version, prereleases=True
                ):
                    raise ValueError(
                        f"{package.name}: {value} does not accept {request.version}; "
                        "update the package's compatibility contract before release"
                    )
                dependencies.add(name)
        graph[package.name] = dependencies
    graph["bazaar-compute-node"].update(packages.keys() - {"bazaar-compute-node"})
    return Source(
        version=request.version,
        branch=request.branch,
        source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        packages=[packages[name] for name in TopologicalSorter(graph).static_order()],
    )


def prepare(request: Request, source_commit: str, output: Path) -> None:
    if subprocess.check_output(["git", "status", "--porcelain"], text=True):
        raise ValueError("prepare requires a clean checkout")
    path = Path(".releases") / f"{request.version}.json"
    commit = ""
    if path.exists():
        source = Source.model_validate_json(path.read_text())
        if source.branch != request.branch or source.version != request.version:
            raise ValueError("this version was prepared on another release branch")
        commit = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(path)], text=True
        ).strip()
        mode = "retry"
    else:
        source = plan(request)
        if source.source_commit != source_commit:
            raise ValueError("the selected branch advanced; start a new workflow run")
        for package in source.packages:
            subprocess.run(
                [
                    "uv",
                    "version",
                    "--package",
                    package.name,
                    request.version,
                    "--frozen",
                ],
                check=True,
            )
        subprocess.run(["uv", "lock"], check=True)
        path.parent.mkdir(exist_ok=True)
        path.write_text(source.model_dump_json(indent=2) + "\n")
        mode = "new"
    previous = subprocess.run(
        [
            "git",
            "describe",
            "--tags",
            "--abbrev=0",
            "--match",
            "v*",
            "--exclude",
            "*rc*",
            source.source_commit,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    with output.open("a") as handle:
        handle.write(
            f"mode={mode}\nrelease_commit={commit}\nsource_commit={source.source_commit}\n"
            f"plan={path.as_posix()}\nversion={request.version}\ntag=v{request.version}\n"
            f"is_prerelease={str(Version(request.version).is_prerelease).lower()}\n"
            f"previous_tag={previous.stdout.strip() if previous.returncode == 0 else ''}\n"
        )


def build(path: Path, directory: Path) -> None:
    source = Source.model_validate_json(path.read_text())
    if subprocess.check_output(["git", "status", "--porcelain"], text=True):
        raise ValueError("build requires the clean, fixed release checkout")
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("build requires an empty output directory")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    epoch = int(
        subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", "HEAD"], text=True
        )
    )
    subprocess.run(
        ["uv", "build", "--all-packages", "--no-sources", "--out-dir", str(directory)],
        env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch)},
        check=True,
    )
    packages = {package.name: package for package in source.packages}
    files: list[Distribution] = []
    entries: dict[str, str] = {}
    for artifact in sorted(directory.iterdir()):
        if artifact.suffix == ".whl":
            kind = "wheel"
            with zipfile.ZipFile(artifact) as archive:
                metadata = BytesParser().parsebytes(
                    archive.read(
                        next(
                            name
                            for name in archive.namelist()
                            if name.endswith(".dist-info/METADATA")
                        )
                    )
                )
                entry_file = next(
                    (
                        name
                        for name in archive.namelist()
                        if name.endswith(".dist-info/entry_points.txt")
                    ),
                    None,
                )
                if entry_file is not None:
                    parser = configparser.ConfigParser()
                    parser.optionxform = str
                    parser.read_string(archive.read(entry_file).decode())
                    if parser.has_section("bazaar_compute_node.contribs"):
                        for entry in parser["bazaar_compute_node.contribs"]:
                            if entry in entries:
                                raise ValueError(
                                    f"duplicate contrib entry {entry}: {entries[entry]} and {artifact.name}"
                                )
                            entries[entry] = artifact.name
        elif artifact.name.endswith(".tar.gz"):
            kind = "sdist"
            with tarfile.open(artifact) as archive:
                member = next(
                    member
                    for member in archive.getmembers()
                    if member.name.endswith("/PKG-INFO")
                )
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"{artifact.name}: missing PKG-INFO")
                with handle:
                    metadata = BytesParser().parsebytes(handle.read())
        else:
            continue
        name = canonicalize_name(str(metadata["Name"]))
        package = packages[name]
        if (
            str(metadata["Version"]) != source.version
            or str(metadata["License-Expression"]) != package.license
        ):
            raise ValueError(
                f"{artifact.name}: version or license differs from the release plan"
            )
        requirements = {
            str(Requirement(value)) for value in metadata.get_all("Requires-Dist", [])
        }
        if requirements != {str(Requirement(value)) for value in package.dependencies}:
            raise ValueError(
                f"{artifact.name}: dependencies differ from the release plan"
            )
        files.append(
            Distribution(
                package=name, filename=artifact.name, kind=kind, sha256=digest(artifact)
            )
        )
    for name in packages:
        if sorted(file.kind for file in files if file.package == name) != [
            "sdist",
            "wheel",
        ]:
            raise ValueError(f"{name}: release requires one wheel and one sdist")
    manifest = Manifest(
        **source.model_dump(),
        release_commit=commit,
        source_date_epoch=epoch,
        files=files,
    )
    (directory / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")


def load_release(directory: Path) -> Manifest:
    manifest = Manifest.model_validate_json((directory / "manifest.json").read_text())
    source = Source.model_validate_json(
        (Path(".releases") / f"{manifest.version}.json").read_text()
    )
    if Source.model_validate(manifest.model_dump()) != source:
        raise ValueError("artifact manifest differs from the committed release plan")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if manifest.release_commit != commit:
        raise ValueError("checkout is not the artifact's fixed release commit")
    for file in manifest.files:
        path = directory / file.filename
        if path.parent.resolve() != directory.resolve() or digest(path) != file.sha256:
            raise ValueError(f"{file.filename}: release artifact checksum differs")
    return manifest


def inspect_pypi(manifest: Manifest, package: Package) -> None:
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{package.name}/{manifest.version}/json"
    ) as response:
        published = PublishedFiles.model_validate(json.load(response))
    files = {file.filename: file.digests["sha256"] for file in published.urls}
    for file in manifest.files:
        if file.package == package.name and files.get(file.filename) != file.sha256:
            raise ValueError(
                f"PyPI {file.filename}: missing or different from the fixed artifact"
            )


def verify(directory: Path, pypi: bool) -> None:
    manifest = load_release(directory)
    if pypi:
        for package in manifest.packages:
            inspect_pypi(manifest, package)
    root = Path.cwd()
    platform = {"linux": "linux", "darwin": "macos", "win32": "windows"}[sys.platform]
    for kind in ["pypi"] if pypi else ["wheel", "sdist"]:
        with TemporaryDirectory(prefix="bcn-release-") as temporary:
            temporary = Path(temporary)
            environment = temporary / "environment"
            subprocess.run(
                ["uv", "venv", "--python", "3.15", str(environment)], check=True
            )
            python = environment / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            requirements = (
                [f"{package.name}=={manifest.version}" for package in manifest.packages]
                if pypi
                else [
                    str((directory / file.filename).resolve())
                    for file in manifest.files
                    if file.kind == kind
                ]
            )
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--default-index",
                    "https://pypi.org/simple",
                    *(["--refresh"] if pypi else []),
                    *requirements,
                ],
                cwd=temporary,
                check=True,
            )
            variables = {
                **os.environ,
                "PATH": str(python.parent) + os.pathsep + os.environ.get("PATH", ""),
                "BCN_HOME": str(temporary / "node"),
                "UV_TOOL_DIR": str(temporary / "tools"),
                "UV_TOOL_BIN_DIR": str(temporary / "bin"),
            }
            for package in manifest.packages:
                print(f"Verify {kind}: {package.name} {manifest.version}", flush=True)
                command = [
                    str(python),
                    str(root / "scripts/ci_package_smoke.py"),
                    package.name,
                    manifest.version,
                    package.license,
                ]
                if package.smoke is not None and platform in package.platforms:
                    command.append(str(root / package.smoke))
                subprocess.run(command, cwd=temporary, env=variables, check=True)


def publish(directory: Path, dry_run: bool) -> None:
    manifest = load_release(directory)
    records: list[dict[str, str]] = []
    for package in manifest.packages:
        files = [
            str(directory / file.filename)
            for file in manifest.files
            if file.package == package.name
        ]
        print(f"Publish {package.name} {manifest.version}", flush=True)
        subprocess.run(
            [
                "uv",
                "publish",
                *(["--dry-run"] if dry_run else ["--trusted-publishing", "always"]),
                *files,
            ],
            check=True,
        )
        if not dry_run:
            inspect_pypi(manifest, package)
            records.append(
                {
                    "package": package.name,
                    "version": manifest.version,
                    "status": "published",
                }
            )
            (directory / "published.json").write_text(
                json.dumps(records, indent=2) + "\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("version")
        command.add_argument("--branch", required=True)
        if name == "plan":
            command.add_argument("--github-output", type=Path)
        if name == "prepare":
            command.add_argument("--source", required=True)
            command.add_argument("--github-output", type=Path, required=True)
    for name in ("build", "inspect", "verify", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--directory", type=Path, default=Path("dist"))
        if name == "build":
            command.add_argument("--plan", type=Path, required=True)
        if name == "verify":
            command.add_argument("--pypi", action="store_true")
        if name == "publish":
            command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command in {"plan", "prepare"}:
        request = Request(version=args.version, branch=args.branch)
        if args.command == "plan":
            source = plan(request)
            print(source.model_dump_json(indent=2))
            if args.github_output is not None:
                with args.github_output.open("a") as handle:
                    handle.write(f"source_commit={source.source_commit}\n")
        else:
            prepare(request, args.source, args.github_output)
    elif args.command == "build":
        build(args.plan, args.directory)
    elif args.command == "inspect":
        print(load_release(args.directory).model_dump_json(indent=2))
    elif args.command == "verify":
        verify(args.directory, args.pypi)
    elif args.command == "publish":
        publish(args.directory, args.dry_run)


if __name__ == "__main__":
    main()
