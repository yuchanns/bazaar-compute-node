from __future__ import annotations

from types import MappingProxyType

MESSAGES = MappingProxyType(
    {
        "runtime.error.failed": "执行失败：${error}",
        "runtime.error.unknown": "执行状态未知：${error}",
    }
)
