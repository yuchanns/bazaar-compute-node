from __future__ import annotations

from .model import Migration

LOGIN_ORDER_MIGRATION = Migration(
    version=4,
    name="login_order",
    statements=(
        "ALTER TABLE auth_settings ADD COLUMN login_order TEXT NOT NULL DEFAULT '[\"password\"]'",
        (
            "UPDATE auth_settings SET login_order=(SELECT json_group_array(id) FROM "
            "(SELECT 'password' AS id UNION ALL SELECT id FROM "
            "(SELECT id FROM oidc_providers ORDER BY created_at_ms, id))) WHERE id=1"
        ),
    ),
)
