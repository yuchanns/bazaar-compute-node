from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bazaar_compute_server.access import Access
from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage
from bazaar_compute_server.fleet import agent_page
from bazaar_compute_server.permissions import Permission as P
from bazaar_compute_server.protocol import Event
from bazaar_compute_server.storage import OIDCProvider, OIDCTransaction, Role, RoleShare

from ._serving import serving, signed_in


@pytest.mark.asyncio
async def test_role_members_browse_shared_resources_and_receive_action_grants(
    tmp_path: Path,
) -> None:
    async with serving(tmp_path) as (base, storage):
        owner = await storage.find_account("admin")
        assert owner is not None
        member = await storage.add_account("reviewer", "reviewer password")
        computer = (
            await storage.add_computer("review workstation", owner_id=owner.id)
        ).computer
        await storage.record_events(
            computer.id,
            "run",
            [
                Event.model_validate(
                    {
                        "seq": 1,
                        "event_name": "node.health",
                        "state": "completed",
                        "created_at_ms": now_ms(),
                        "correlation": {},
                        "metadata": {
                            "agents": [
                                {
                                    "agent_id": "assistant",
                                    "name": "Assistant",
                                    "status": "started",
                                }
                            ]
                        },
                    }
                )
            ],
        )
        reader = Role(
            "reader",
            "Readers",
            frozenset({P.COMPUTERS_VIEW, P.AGENTS_VIEW}),
            now_ms(),
            now_ms(),
        )
        reviewer = Role(
            "reviewer",
            "Reviewers",
            frozenset({P.AGENTS_APPROVE, P.AGENTS_UPDATE}),
            now_ms(),
            now_ms(),
        )
        await storage.save_role(reader)
        await storage.save_role(reviewer)
        await storage.set_account_roles(member.id, [reader.id, reviewer.id])
        await storage.save_role_share(
            RoleShare(
                reader.id,
                "computer",
                computer.id,
                frozenset({P.COMPUTERS_VIEW, P.AGENTS_VIEW, P.AGENTS_APPROVE}),
            )
        )
        access = Access(member, storage, await storage.list_roles(member.id))
        assert await access.can("computer", computer.id, P.COMPUTERS_VIEW)
        assert await access.can("agent", "assistant", P.AGENTS_APPROVE)
        computers = await storage.list_computers(member.id)
        assert computer in computers
        page = await agent_page(storage, access)
        assert any(agent.id == "assistant" for agent in page.agents)

        # Give this group configuration access, then apply its next role change.
        share = RoleShare(
            reviewer.id, "agent", "assistant", frozenset({P.AGENTS_UPDATE})
        )
        await storage.save_role_share(share)
        access = Access(member, storage, await storage.list_roles(member.id))
        assert await access.can("agent", "assistant", P.AGENTS_UPDATE)
        await storage.set_account_roles(member.id, [reader.id])
        access = Access(member, storage, await storage.list_roles(member.id))
        assert await access.can("agent", "assistant", P.AGENTS_VIEW)
        assert all(access.allows(point) for point in reader.permissions)

        # Share just the agent: it stays navigable with its computer name.
        await storage.remove_role_share(reader.id, "computer", computer.id)
        await storage.save_role_share(
            RoleShare(reader.id, "agent", "assistant", frozenset({P.AGENTS_VIEW}))
        )
        page = await agent_page(storage, access)
        assert any(agent.id == "assistant" for agent in page.agents)
        assert computer in await storage.list_computers(member.id, for_agents=True)
        async with signed_in(base, storage, member.name) as browser:
            computer_ref, agent_ref = await storage.shorten([computer.id, "assistant"])
            async with browser.get(
                f"{base}/agents/{computer_ref}/{agent_ref}"
            ) as response:
                assert response.status == 200
                assert "Assistant" in await response.text()
            async with browser.get(f"{base}/settings/appearance") as response:
                assert response.status == 200
                assert "appearance" in await response.text()


@pytest.mark.asyncio
async def test_first_oidc_login_assigns_the_current_default_and_keeps_membership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bcs.sqlite3"
    storage = SqliteStorage(path, retention_days=30)
    await storage.start()
    try:
        reader = Role(
            "reader", "Readers", frozenset({P.AGENTS_VIEW}), now_ms(), now_ms()
        )
        reviewer = replace(
            reader,
            id="reviewer",
            name="Reviewers",
            permissions=frozenset({P.AGENTS_VIEW, P.AGENTS_APPROVE}),
        )
        await storage.save_role(reader)
        await storage.save_role(reviewer)
        await storage.set_default_role(reader.id)
        provider = OIDCProvider(
            "work",
            "Work",
            "",
            "https://issuer.example",
            "client",
            "secret",
            "https://bcs.example/callback",
            now_ms(),
            now_ms(),
        )
        await storage.save_oidc_provider(provider)
        first = await storage.oidc_account(
            provider.id, provider.issuer, "first", "Colleague", "first@example.com"
        )
        assert reader in await storage.list_roles(first.id)
        await storage.set_default_role(reviewer.id)
        second = await storage.oidc_account(
            provider.id, provider.issuer, "second", "Colleague", "second@example.com"
        )
        assert reviewer in await storage.list_roles(second.id)
        returning = await storage.oidc_account(
            provider.id, provider.issuer, "first", "New name", "first@example.com"
        )
        assert returning.id == first.id
        assert reader in await storage.list_roles(returning.id)
        assert first.id != second.id
        assert returning.auth_type == "oidc"
        await storage.remove_role(reviewer.id, replacement_id=reader.id)
        assert await storage.default_role() == reader
        transaction = OIDCTransaction(
            "state",
            provider.id,
            "browser",
            "nonce",
            "verifier",
            provider.redirect_uri,
            now_ms() + 60_000,
        )
        await storage.save_oidc_transaction(transaction)
        assert (
            await storage.consume_oidc_transaction(
                transaction.state, transaction.browser_hash, provider.id
            )
            == transaction
        )
    finally:
        await storage.stop()
    # Reopening runs the migration ledger and restores the saved membership.
    await storage.start()
    try:
        assert reader in await storage.list_roles(first.id)
        assert provider in await storage.list_oidc_providers()
    finally:
        await storage.stop()


@pytest.mark.asyncio
async def test_provider_default_role_overrides_global_for_new_accounts(
    tmp_path: Path,
) -> None:
    storage = SqliteStorage(tmp_path / "bcs.sqlite3", retention_days=30)
    await storage.start()
    try:
        reader = Role(
            "reader", "Readers", frozenset({P.AGENTS_VIEW}), now_ms(), now_ms()
        )
        reviewer = replace(
            reader,
            id="reviewer",
            name="Reviewers",
            permissions=frozenset({P.AGENTS_VIEW, P.AGENTS_APPROVE}),
        )
        await storage.save_role(reader)
        await storage.save_role(reviewer)
        await storage.set_default_role(reader.id)
        provider = OIDCProvider(
            "work",
            "Work",
            "",
            "https://issuer.example",
            "client",
            "secret",
            "https://bcs.example/callback",
            now_ms(),
            now_ms(),
            default_role_id=reviewer.id,
        )
        await storage.save_oidc_provider(provider)
        account = await storage.oidc_account(
            provider.id, provider.issuer, "first", "First", "first@example.com"
        )
        assert reviewer in await storage.list_roles(account.id)
        await storage.stop()
        await storage.start()
        assert (await storage.get_oidc_provider(provider.id)) == provider
        assert reviewer in await storage.list_roles(account.id)
        await storage.set_account_roles(account.id, [reader.id])
        returning = await storage.oidc_account(
            provider.id, provider.issuer, "first", "First", "first@example.com"
        )
        assert reader in await storage.list_roles(returning.id)
        provider = replace(provider, default_role_id=None)
        await storage.save_oidc_provider(provider)
        following = await storage.oidc_account(
            provider.id,
            provider.issuer,
            "following",
            "Following",
            "following@example.com",
        )
        assert reader in await storage.list_roles(following.id)
        await storage.remove_role(reviewer.id)
        assert provider in await storage.list_oidc_providers()
    finally:
        await storage.stop()
