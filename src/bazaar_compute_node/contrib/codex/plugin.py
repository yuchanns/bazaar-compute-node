from __future__ import annotations

from ...core.runtime import IRuntime, RuntimeCommandContext
from .runtime import Runtime


def create_runtime(context: RuntimeCommandContext) -> IRuntime:
    return Runtime(
        context,
        model=context.runtime_options.get("model"),
        effort=context.runtime_options.get("effort"),
    )


__all__ = ["create_runtime"]
