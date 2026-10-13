"""Installed metadata inspection for the actual target interpreter."""

from __future__ import annotations

lazy import importlib
lazy import sys
lazy import tomllib
lazy from importlib.metadata import distributions
lazy from pathlib import Path

lazy from bazaar_core import CONTRIB_GROUP, Contrib
lazy from packaging.utils import canonicalize_name

lazy from .contracts import EntryInfo, EnvironmentInfo, InstalledPackage


def inspect() -> EnvironmentInfo:
    packages: list[InstalledPackage] = []
    for distribution in distributions():
        entries: list[EntryInfo] = []
        defaults = None
        for point in distribution.entry_points:
            if point.group != CONTRIB_GROUP:
                continue
            try:
                definition = point.load()
                if not isinstance(definition, Contrib):
                    raise TypeError("entry point must export a Contrib definition")
                entries.append(
                    EntryInfo(
                        name=point.name,
                        value=point.value,
                        config_schema=(
                            definition.config.model_json_schema()
                            if definition.config is not None
                            else None
                        ),
                    )
                )
            except Exception as error:  # noqa: BLE001 - Retain diagnostics from independent installed plugins.
                entries.append(
                    EntryInfo(name=point.name, value=point.value, error=str(error))
                )
                continue
            if defaults is None:
                module = importlib.import_module(point.module.split(".", 1)[0])
                if module.__file__ is not None:
                    path = Path(module.__file__).with_name("defaults.toml")
                    if path.is_file():
                        defaults = tomllib.loads(path.read_text(encoding="utf-8"))
        packages.append(
            InstalledPackage(
                package=str(canonicalize_name(distribution.metadata["Name"])),
                version=distribution.version,
                summary=distribution.metadata.get("Summary", ""),
                entries=tuple(entries),
                defaults=defaults,
                requirements=tuple(distribution.requires or ()),
            )
        )
    return EnvironmentInfo(
        prefix=Path(sys.prefix),
        python=Path(sys.executable),
        base_python=Path(getattr(sys, "_base_executable", sys.executable)),
        packages=tuple(packages),
    )


if __name__ == "__main__":
    print(inspect().model_dump_json())
