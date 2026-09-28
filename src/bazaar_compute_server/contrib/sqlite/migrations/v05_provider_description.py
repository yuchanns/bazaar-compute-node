from __future__ import annotations

from .model import Migration

PROVIDER_DESCRIPTION_MIGRATION = Migration(
    version=5,
    name="provider_description",
    statements=(
        "ALTER TABLE oidc_providers ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    ),
)
