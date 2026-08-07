"""Provider-neutral identity for the bcn client."""

from __future__ import annotations

from dataclasses import dataclass

from .. import __version__


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Canonical client identity exposed to provider protocol adapters."""

    name: str
    version: str

    def __post_init__(self) -> None:
        for field_name, value in (("name", self.name), ("version", self.version)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
            if "\r" in value or "\n" in value:
                raise ValueError(f"{field_name} must not contain line breaks")


CLIENT_NAME = "bcn"
CLIENT_VERSION = __version__
CLIENT_INFO = ClientInfo(name=CLIENT_NAME, version=CLIENT_VERSION)


__all__ = ["CLIENT_INFO", "CLIENT_NAME", "CLIENT_VERSION", "ClientInfo"]
