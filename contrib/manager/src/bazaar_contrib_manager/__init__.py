"""Shared contrib catalog, package management, and operation services."""

from bazaar_contrib_invoke import CommandEntry

command_entry = CommandEntry(contrib="contrib-manager", help="Manage contribs")

lazy from .catalog import CONTRIB_CATALOG, ContribCatalog
lazy from .contracts import (
    CatalogEntry,
    EntryInfo,
    IndexStatus,
    InstalledPackage,
    ManagementError,
    ManagerConfig,
    Operation,
    Package,
)
lazy from .service import CONTRIB_MANAGER, ContribManager, manager

__all__ = [
    "CONTRIB_CATALOG",
    "CONTRIB_MANAGER",
    "CatalogEntry",
    "ContribCatalog",
    "ContribManager",
    "EntryInfo",
    "IndexStatus",
    "InstalledPackage",
    "ManagementError",
    "ManagerConfig",
    "Operation",
    "Package",
    "command_entry",
    "manager",
]
