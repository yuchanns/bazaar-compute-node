from __future__ import annotations

from pathlib import Path


def resolve_data_dir() -> Path:
    """Resolve the persistent node data directory under the user's home."""

    return (Path.home() / ".bcn").resolve(strict=False)


def resolve_workspace_dir(
    workspace_id: str,
) -> Path:
    """Resolve the persistent shared workspace for one node identity."""

    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("workspace_id must be a non-empty string")
    return resolve_data_dir() / "workspaces" / workspace_id
