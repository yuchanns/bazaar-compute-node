"""Shared contrib catalog, package management, and operation services."""

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
    "manager",
]
