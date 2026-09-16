"""The application uvicorn imports: `bazaar_compute_server.asgi:app`.

Configuration comes from the file `BCS_CONFIG` names, or the default one,
because an import string cannot carry arguments.
"""

from __future__ import annotations

import os
from pathlib import Path

from .app import create_app
from .config import load_configuration, resolve_data_dir

_config_path = os.environ.get("BCS_CONFIG")
app = create_app(
    load_configuration(Path(_config_path) if _config_path else None),
    resolve_data_dir(),
)

__all__ = ["app"]
