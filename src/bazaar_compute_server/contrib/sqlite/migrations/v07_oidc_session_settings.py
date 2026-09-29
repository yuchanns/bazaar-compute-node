from __future__ import annotations

from .model import Migration

OIDC_SESSION_SETTINGS_MIGRATION = Migration(
    version=7,
    name="oidc_session_settings",
    statements=(
        "ALTER TABLE oidc_providers ADD COLUMN session_minutes INTEGER NOT NULL DEFAULT 10",
        "DROP TABLE oidc_transactions",
    ),
)
