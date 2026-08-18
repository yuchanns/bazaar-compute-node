from __future__ import annotations

from types import MappingProxyType

MESSAGES = MappingProxyType(
    {
        "runtime.error.failed": "Execution failed: ${error}",
        "runtime.error.unknown": "Execution status is unknown: ${error}",
    }
)
