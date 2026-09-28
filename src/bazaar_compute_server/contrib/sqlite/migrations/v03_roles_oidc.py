"""Roles and external login identities for BCS."""

from .model import Migration

ROLES_OIDC_MIGRATION = Migration(
    version=3,
    name="roles_oidc",
    statements=(
        "ALTER TABLE accounts ADD COLUMN auth_type TEXT NOT NULL DEFAULT 'local'",
        "ALTER TABLE accounts ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE roles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            permissions TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )""",
        """CREATE TABLE account_roles (
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            PRIMARY KEY(account_id, role_id)
        )""",
        "CREATE INDEX account_roles_by_role ON account_roles(role_id)",
        """CREATE TABLE auth_settings (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            default_role_id TEXT REFERENCES roles(id)
        )""",
        "INSERT INTO auth_settings(id) VALUES (1)",
        """CREATE TABLE oidc_providers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, logo_url TEXT NOT NULL,
            issuer TEXT NOT NULL, client_id TEXT NOT NULL, client_secret TEXT NOT NULL,
            redirect_uri TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )""",
        """CREATE TABLE oidc_identities (
            issuer TEXT NOT NULL, subject TEXT NOT NULL,
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            provider_id TEXT NOT NULL REFERENCES oidc_providers(id),
            display_name TEXT NOT NULL, email TEXT NOT NULL,
            PRIMARY KEY(issuer, subject)
        )""",
        "CREATE INDEX oidc_identities_by_account ON oidc_identities(account_id)",
        """CREATE TABLE oidc_transactions (
            state TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL REFERENCES oidc_providers(id) ON DELETE CASCADE,
            browser_hash TEXT NOT NULL, nonce TEXT NOT NULL,
            code_verifier TEXT NOT NULL, redirect_uri TEXT NOT NULL,
            expires_at_ms INTEGER NOT NULL
        )""",
        "CREATE INDEX oidc_transactions_by_expiry ON oidc_transactions(expires_at_ms)",
    ),
)
