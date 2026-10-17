"""Exercise CI planning and installation with real local workspace packages."""

from __future__ import annotations

from pathlib import Path
lazy import json
lazy import shutil
lazy import subprocess
lazy import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / "scripts" / "ci.py"


def add_package(root: Path, name: str, *, dependency: str | None = None) -> Path:
    directory = root / "packages" / name
    module = directory / "src" / name
    module.mkdir(parents=True)
    dependencies = [f"{dependency}==0.3.0"] if dependency else []
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.3.0"\n'
        'requires-python = ">=3.15"\nlicense = "Apache-2.0"\n'
        'license-files = ["LICENSE"]\n'
        f"dependencies = {json.dumps(dependencies)}\n"
        '[build-system]\nrequires = ["uv_build>=0.13.0,<0.14.0"]\n'
        'build-backend = "uv_build"\n'
    )
    shutil.copyfile(ROOT / "LICENSE", directory / "LICENSE")
    if dependency:
        source = f"from {dependency} import write as write\n"
    else:
        source = (
            "from pathlib import Path\n\n\n"
            "def write(path: Path, text: str) -> None:\n"
            "    path.write_text(text)\n"
        )
    (module / "__init__.py").write_text(source)
    tests = directory / "tests"
    tests.mkdir()
    (tests / f"test_{name}.py").write_text(
        f"from {name} import write\n\n\n"
        "def test_write(tmp_path):\n"
        '    path = tmp_path / "journal.txt"\n'
        f'    write(path, "{name} stores a journal entry")\n'
        '    assert "journal entry" in path.read_text()\n'
    )
    (tests / "package_smoke.py").write_text(
        "from pathlib import Path\nfrom tempfile import TemporaryDirectory\n\n"
        f"from {name} import write\n\n"
        "with TemporaryDirectory() as directory:\n"
        '    path = Path(directory) / "installed.txt"\n'
        f'    write(path, "{name} installed distribution")\n'
        '    assert "installed distribution" in path.read_text()\n'
    )
    return directory


def commit(root: Path) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Workspace change"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ci-workspace"\nversion = "0.0.0"\n'
        'requires-python = ">=3.15"\n'
        "[dependency-groups]\n"
        'dev = ["pytest", "ruff", "pyright==1.1.414", "pydantic", "packaging", '
        '"blockbuster>=1.5.29,<1.6"]\n'
        "[tool.uv]\npackage = false\n"
        '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
        "[tool.uv.sources]\njournal = { workspace = true }\n"
        "reader = { workspace = true }\n"
    )
    (tmp_path / ".gitignore").write_text(".venv/\n__pycache__/\n.pytest_cache/\n")
    add_package(tmp_path, "journal")
    add_package(tmp_path, "reader", dependency="journal")
    add_package(tmp_path, "observer")
    subprocess.run(["uv", "lock"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Workspace"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=tmp_path, check=True
    )
    commit(tmp_path)
    return tmp_path


def test_changes_follow_workspace_dependencies(workspace: Path) -> None:
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip()
    notes = workspace / "packages" / "journal" / "NOTES.txt"
    notes.write_text("Journal package notes\n")
    head = commit(workspace)
    matrix = json.loads(
        subprocess.check_output(
            [sys.executable, str(CI), "plan", "--base", base, "--head", head],
            cwd=workspace,
            text=True,
        )
    )
    assert {"journal", "reader"}.issubset(matrix["package"])
    subprocess.run(
        ["uv", "sync", "--locked", "--all-packages"], cwd=workspace, check=True
    )
    for name in matrix["package"]:
        subprocess.run(
            [sys.executable, str(CI), "check", "--package", name],
            cwd=workspace,
            check=True,
        )
        subprocess.run(
            [sys.executable, str(CI), "test", "--package", name],
            cwd=workspace,
            check=True,
        )

    base = head
    notes.rename(workspace / "packages" / "observer" / "NOTES.txt")
    head = commit(workspace)
    output = workspace / "matrix.txt"
    matrix = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(CI),
                "plan",
                "--base",
                base,
                "--head",
                head,
                "--github-output",
                str(output),
            ],
            cwd=workspace,
            text=True,
        )
    )
    assert {"journal", "reader", "observer"}.issubset(matrix["package"])
    assert "has_packages=true" in output.read_text()


def test_package_jobs_follow_declared_platforms(workspace: Path) -> None:
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip()
    for name, platforms in (
        ("journal", ["linux", "macos"]),
        ("reader", ["windows"]),
    ):
        with (workspace / "packages" / name / "pyproject.toml").open("a") as handle:
            handle.write(f"\n[tool.bcn.ci]\nplatforms = {json.dumps(platforms)}\n")
    (workspace / "packages" / "observer" / "NOTES.txt").write_text(
        "Observer package notes\n"
    )
    head = commit(workspace)
    matrix = json.loads(
        subprocess.check_output(
            [sys.executable, str(CI), "plan", "--base", base, "--head", head],
            cwd=workspace,
            text=True,
        )
    )
    for name, prefixes in (
        ("journal", ("ubuntu-", "macos-")),
        ("reader", ("windows-",)),
        ("observer", ("ubuntu-", "macos-", "windows-")),
    ):
        jobs = [job for job in matrix["tests"]["include"] if job["package"] == name]
        assert jobs
        assert all(job["os"].startswith(prefixes) for job in jobs)
        for prefix in prefixes:
            assert any(job["os"].startswith(prefix) for job in jobs)


def test_new_member_builds_with_its_workspace_dependencies(workspace: Path) -> None:
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip()
    inspector = add_package(workspace, "inspector", dependency="reader")
    (inspector / "tests").rename(inspector / "checks")
    with (inspector / "pyproject.toml").open("a") as handle:
        handle.write(
            '\n[tool.pytest.ini_options]\ntestpaths = ["checks"]\n'
            '\n[tool.pyright]\ninclude = ["src", "checks"]\n'
        )
    installed = workspace / "installed-journal.txt"
    with (inspector / "checks" / "package_smoke.py").open("a") as handle:
        handle.write(
            "\nwrite(\n"
            '    Path(__file__).parents[3] / "installed-journal.txt",\n'
            '    "installed package stores a journal",\n'
            ")\n"
        )
    subprocess.run(["uv", "lock"], cwd=workspace, check=True)
    head = commit(workspace)
    matrix = json.loads(
        subprocess.check_output(
            [sys.executable, str(CI), "plan", "--base", base, "--head", head],
            cwd=workspace,
            text=True,
        )
    )
    assert "inspector" in matrix["package"]
    subprocess.run(
        ["uv", "sync", "--locked", "--all-packages"], cwd=workspace, check=True
    )
    subprocess.run(
        [sys.executable, str(CI), "check", "--package", "inspector"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(CI), "package", "--package", "inspector"],
        cwd=workspace,
        check=True,
    )
    assert "stores a journal" in installed.read_text()
