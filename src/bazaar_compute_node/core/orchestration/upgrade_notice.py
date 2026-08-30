"""What one conversation has been told about the release on offer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UpgradePending:
    """A release this conversation has not been told about yet."""

    version: str


@dataclass(frozen=True, slots=True)
class UpgradeAnnounced:
    """A release this conversation has already been told about."""

    version: str


# a conversation that has heard of nothing has no entry at all
UpgradeNotice = UpgradePending | UpgradeAnnounced

__all__ = ["UpgradeAnnounced", "UpgradeNotice", "UpgradePending"]
