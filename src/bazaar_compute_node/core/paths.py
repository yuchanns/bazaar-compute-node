from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_data_dir(explicit: Path | str | None = None) -> Path:
    """Resolve the persistent node data directory for the current platform."""

    configured = explicit or os.environ.get("BCN_DATA_DIR")
    if configured is not None:
        return Path(configured).expanduser().resolve(strict=False)

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return (Path(base).expanduser() / "bcn").resolve(strict=False)
        return (Path.home() / "AppData" / "Local" / "bcn").resolve(strict=False)

    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "bcn").resolve(
            strict=False
        )

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return (Path(xdg_data_home).expanduser() / "bcn").resolve(strict=False)
    return (Path.home() / ".local" / "share" / "bcn").resolve(strict=False)
