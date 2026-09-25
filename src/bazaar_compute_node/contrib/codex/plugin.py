from __future__ import annotations

from ...core.runtime import (
    IRuntime,
    IRuntimeBuilder,
    RuntimeAvailability,
    RuntimeCommandContext,
    RuntimeModels,
)
from .inspect import inspect_runtime, list_models
from .runtime import Runtime


class CodexBuilder(IRuntimeBuilder):
    def build(self, context: RuntimeCommandContext) -> IRuntime:
        return Runtime(
            context,
            model=context.runtime_options.get("model"),
            effort=context.runtime_options.get("effort"),
        )

    async def inspect(self, *, timeout: float) -> RuntimeAvailability:
        return await inspect_runtime(timeout=timeout)

    async def models(self, *, timeout: float) -> RuntimeModels:
        return await list_models(timeout=timeout)


builder = CodexBuilder()


__all__ = ["CodexBuilder", "builder"]
