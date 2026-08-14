from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_PORTABLE_UNIX_ENDPOINT_BYTES = 103


@dataclass(frozen=True, slots=True)
class IsolatedTestEnvironment:
    """Filesystem and process environment for one standalone acceptance run."""

    root: Path
    home: Path
    codex_home: Path
    data_dir: Path
    workspace: Path
    endpoint_path: Path


@contextmanager
def temporary_test_directory(*, prefix: str = "bcn-") -> Iterator[Path]:
    """Yield one system-selected temporary directory and remove it on exit."""

    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        yield Path(directory)


@contextmanager
def isolated_test_environment(
    *,
    prefix: str = "bcn-",
    endpoint_name: str = "bcn.sock",
) -> Iterator[IsolatedTestEnvironment]:
    """Isolate standalone BCN state under one system-selected temporary root."""

    endpoint_component = Path(endpoint_name)
    if (
        not endpoint_name
        or endpoint_component.name != endpoint_name
        or endpoint_name in {".", ".."}
        or "/" in endpoint_name
        or "\\" in endpoint_name
    ):
        raise ValueError("endpoint_name must be a single path component")

    with temporary_test_directory(prefix=prefix) as root:
        home = root / "home"
        codex_home = root / "codex-home"
        data_dir = home / ".bcn"
        workspace = root / "workspace"
        endpoint_path = root / endpoint_name
        if (
            os.name != "nt"
            and len(os.fsencode(endpoint_path)) > _PORTABLE_UNIX_ENDPOINT_BYTES
        ):
            raise RuntimeError(
                "temporary endpoint exceeds the portable AF_UNIX path limit"
            )

        for directory in (home, codex_home, data_dir, workspace):
            directory.mkdir()

        environment = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(codex_home),
            "BCN_DATA_NAME": data_dir.name,
        }
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        try:
            yield IsolatedTestEnvironment(
                root=root,
                home=home,
                codex_home=codex_home,
                data_dir=data_dir,
                workspace=workspace,
                endpoint_path=endpoint_path,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
