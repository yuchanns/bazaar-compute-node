from __future__ import annotations

from ...core.runtime import IRuntime, RuntimeCommandContext
from .runtime import CodexAppServerRuntime


def create_runtime(context: RuntimeCommandContext) -> IRuntime:
    return CodexAppServerRuntime(
        context,
        model=context.runtime_options.get("model"),
        effort=context.runtime_options.get("effort"),
    )


__all__ = ["create_runtime"]
