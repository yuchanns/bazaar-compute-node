from __future__ import annotations

import os
from pathlib import Path


def resolve_data_dir(explicit: Path | str | None = None) -> Path:
    """Resolve the persistent node data directory for the current platform."""

    configured = explicit or os.environ.get("BCN_DATA_DIR")
    if configured is not None:
        return Path(configured).expanduser().resolve(strict=False)

    return (Path.home() / ".bcn").resolve(strict=False)


def resolve_workspace_dir(
    workspace_id: str,
    data_dir: Path | str | None = None,
) -> Path:
    """Resolve the persistent shared workspace for one node identity."""

    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("workspace_id must be a non-empty string")
    return resolve_data_dir(data_dir) / "workspaces" / workspace_id
