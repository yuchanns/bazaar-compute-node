"""The exit code a node uses to ask its host to start it again."""

from __future__ import annotations

# systemd restarts on a non-zero exit, launchd on any exit, and the Windows
# launcher looks for exactly this one before it swaps and starts bcn again
RESTART_EXIT_CODE = 75

__all__ = ["RESTART_EXIT_CODE"]
