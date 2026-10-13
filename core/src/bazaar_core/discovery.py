"""Installed contrib metadata and resource-free entry point loading."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
lazy from importlib.metadata import Distribution, entry_points
lazy from typing import cast
lazy from uuid import uuid7

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ServiceKey
lazy from .contracts import (
    Composition,
    Contrib,
    ContribEntry,
    ContribError,
    ContribSource,
    ContribStatus,
    Failed,
)

CONTRIB_GROUP = "bazaar_compute_node.contribs"


class ContribSpec(BaseModel):
    """An installed entry name and parameters for load() to turn into a ContribEntry."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    contrib: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    location: str = Field(default="<configuration>", min_length=1)
    required_offers: Sequence[ServiceKey[Any]] = ()


def discover() -> tuple[ContribSource, ...]:
    """Read distribution metadata without importing or starting contribs."""
    return tuple(
        ContribSource(
            cast(Distribution, point.dist).metadata["Name"],
            cast(Distribution, point.dist).version,
            point.name,
            point.value,
            point,
        )
        for point in entry_points(group=CONTRIB_GROUP)
    )


def load(specs: Sequence[ContribSpec]) -> Composition:
    """Load each installed definition, retaining failures beside valid entries."""
    index: dict[str, list[ContribSource]] = {}
    for source in discover():
        index.setdefault(source.name, []).append(source)
    entries: list[ContribEntry] = []
    diagnostics: list[ContribStatus] = []
    for spec in specs:
        try:
            sources = index.get(spec.contrib, ())
            if len(sources) != 1:
                detail = "; ".join(source.label for source in sources)
                raise ContribError(
                    f"expected one installed entry {spec.contrib!r}; "
                    f"found {len(sources)}" + (f": {detail}" if detail else "")
                )
            source = sources[0]
            try:
                definition = source.entry_point.load()
                if not isinstance(definition, Contrib):
                    raise ContribError("entry point must export a Contrib definition")
                entries.append(
                    ContribEntry(
                        name=spec.contrib,
                        definition=definition,
                        config=spec.config,
                        location=spec.location,
                        required_offers=spec.required_offers,
                        source=source,
                    )
                )
            except Exception as error:
                raise ContribError(f"{source.label}: {error}") from error
        except ContribError as error:
            diagnostics.append(
                ContribStatus(
                    uuid7(),
                    spec.contrib,
                    spec.location,
                    Failed(error=str(error), phase="stopped"),
                )
            )
    return Composition(
        entries=entries,
        diagnostics=diagnostics,
        isolate_errors=True,
    )
