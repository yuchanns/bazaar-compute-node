"""Generate the official contrib catalog before delegating packaging to uv."""

from __future__ import annotations

lazy import json
lazy import tomllib
lazy from collections.abc import Mapping
lazy from pathlib import Path
lazy from typing import Any

import uv_build

get_requires_for_build_editable = uv_build.get_requires_for_build_editable
get_requires_for_build_sdist = uv_build.get_requires_for_build_sdist
get_requires_for_build_wheel = uv_build.get_requires_for_build_wheel
prepare_metadata_for_build_editable = uv_build.prepare_metadata_for_build_editable
prepare_metadata_for_build_wheel = uv_build.prepare_metadata_for_build_wheel


def generate_catalog() -> None:
    root = Path(__file__).parent
    with (root / "catalog.uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    packages = sorted(
        package["name"]
        for package in lock["package"]
        if (
            package["source"].get("editable")
            or package["source"].get("directory")
            or ""
        ).startswith("contrib/")
    )
    catalog = {
        "contribs": [
            {"package": package, "source": "https://pypi.org/simple/"}
            for package in packages
        ]
    }
    (root / "src" / "bazaar_compute_node" / "catalog.json").write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
    )


def build_wheel(
    wheel_directory: str,
    config_settings: Mapping[Any, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    generate_catalog()
    return uv_build.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(
    sdist_directory: str, config_settings: Mapping[Any, Any] | None = None
) -> str:
    generate_catalog()
    return uv_build.build_sdist(sdist_directory, config_settings)


def build_editable(
    wheel_directory: str,
    config_settings: Mapping[Any, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    generate_catalog()
    return uv_build.build_editable(wheel_directory, config_settings, metadata_directory)
