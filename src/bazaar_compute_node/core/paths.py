from __future__ import annotations

import os
from pathlib import Path

DATA_NAME_ENV = "BCN_DATA_NAME"
DEFAULT_DATA_NAME = ".bcn"


def resolve_data_name() -> str:
    """Resolve the persistent node data name."""

    name = os.environ.get(DATA_NAME_ENV, DEFAULT_DATA_NAME)
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"{DATA_NAME_ENV} must be a single path component")
    return name


def resolve_data_dir() -> Path:
    """Resolve the persistent node data directory under the user's home."""

    return Path.home() / resolve_data_name()


def resolve_workspace_dir(agent_id: str) -> Path:
    """Resolve the persistent workspace owned by one configured Agent."""

    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string")
    return resolve_data_dir() / "workspaces" / agent_id
