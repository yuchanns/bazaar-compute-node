from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        content = "\n".join(self.statements).encode("utf-8")
        return sha256(content).hexdigest()


__all__ = ["Migration"]
