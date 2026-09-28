from __future__ import annotations

from .model import Migration

PROVIDER_DEFAULT_ROLE_MIGRATION = Migration(
    version=6,
    name="provider_default_role",
    statements=(
        "ALTER TABLE oidc_providers ADD COLUMN default_role_id TEXT REFERENCES roles(id)",
    ),
)
