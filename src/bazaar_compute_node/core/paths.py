from __future__ import annotations

import os
from pathlib import Path


def resolve_data_dir() -> Path:
    """Resolve .bcn under BCN_HOME or the user's home."""

    path = Path(os.environ.get("BCN_HOME") or Path.home())
    return path.expanduser().resolve() / ".bcn"


def resolve_workspace_dir(agent_id: str) -> Path:
    """Resolve the persistent workspace owned by one configured Agent."""

    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string")
    return resolve_data_dir() / "workspaces" / agent_id
