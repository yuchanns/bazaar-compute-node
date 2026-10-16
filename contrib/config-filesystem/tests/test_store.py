from __future__ import annotations

lazy import asyncio
lazy import os
lazy import stat
lazy from pathlib import Path

from bazaar_contrib_config import ConfigConflict, ConfigSnapshot

from bazaar_contrib_config_filesystem import FileConfigStore


async def test_two_clients_save_and_rebase_after_an_editor_change(
    tmp_path: Path,
):
    path = tmp_path / "config.toml"
    original = b'# user settings\nversion = "release-2026.10"\ncontribs = []\n'
    await asyncio.to_thread(path.write_bytes, original)
    async with FileConfigStore(path) as first, FileConfigStore(path) as second:
        snapshot = await first.read()
        assert snapshot is not None
        peer = await second.read()
        assert peer is not None
        candidate = {
            "version": "release-2026.11",
            "contribs": [{"enabled": [{"entry": "status"}]}],
        }
        results = await asyncio.gather(
            first.write(candidate, expected_revision=snapshot.revision),
            second.write(
                {"version": "release-2026.11", "contribs": []},
                expected_revision=peer.revision,
            ),
            return_exceptions=True,
        )
        assert any(isinstance(result, ConfigSnapshot) for result in results)
        assert any(isinstance(result, ConfigConflict) for result in results)
        snapshot = await first.read()
        assert snapshot is not None
        await asyncio.to_thread(
            path.write_bytes,
            (await asyncio.to_thread(path.read_bytes))
            + b"\n# edited outside the carrier\n",
        )
        result = await asyncio.gather(
            first.write(candidate, expected_revision=snapshot.revision),
            return_exceptions=True,
        )
        assert isinstance(result[0], ConfigConflict)
        snapshot = await first.read()
        assert snapshot is not None
        saved = await second.write(candidate, expected_revision=snapshot.revision)
        assert saved.payload["contribs"]
    if os.name != "nt":
        assert stat.S_IMODE((await asyncio.to_thread(path.stat)).st_mode) == 0o600


async def test_competing_creators_keep_the_committed_document_after_reopening(
    tmp_path: Path,
):
    path = tmp_path / "new" / "config.toml"
    async with FileConfigStore(path) as first, FileConfigStore(path) as second:
        results = await asyncio.gather(
            first.write(
                {"version": "5", "contribs": [{"enabled": [{"entry": "status"}]}]},
                expected_revision=None,
            ),
            second.write({"version": "5", "contribs": []}, expected_revision=None),
            return_exceptions=True,
        )
    committed = next(result for result in results if isinstance(result, ConfigSnapshot))
    assert any(isinstance(result, ConfigConflict) for result in results)
    async with FileConfigStore(path) as store:
        recovered = await store.read()
        assert recovered is not None
        assert recovered.payload == committed.payload
        assert recovered.revision == committed.revision
