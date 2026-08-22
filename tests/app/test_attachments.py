from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid7

import pytest

from bazaar_compute_node.app import attachments as attachment_module
from bazaar_compute_node.app.attachments import AttachmentMaterializer


@pytest.mark.asyncio
async def test_attachment_materializer_publishes_only_terminal_content(
    tmp_path: Path,
) -> None:
    async def no_references() -> set[str]:
        return set()

    materializer = AttachmentMaterializer(
        lambda: tmp_path, no_references, max_file_bytes=16
    )

    attachment = await materializer.materialize(
        b"content", name="../report.txt", kind="file", media_type="text/plain"
    )

    assert attachment.state == "ready"
    assert attachment.name == "report.txt"
    assert attachment.relative_path is not None
    assert (tmp_path / attachment.relative_path).read_bytes() == b"content"
    assert not tuple((tmp_path / "attachments" / ".staging").iterdir())


@pytest.mark.asyncio
async def test_attachment_materializer_cleans_failed_staging(tmp_path: Path) -> None:
    async def no_references() -> set[str]:
        return set()

    materializer = AttachmentMaterializer(
        lambda: tmp_path, no_references, max_file_bytes=4
    )

    with pytest.raises(ValueError, match="per-file"):
        await materializer.materialize(b"too large", name="payload.bin", kind="file")

    assert not tuple((tmp_path / "attachments" / ".staging").iterdir())


@pytest.mark.asyncio
async def test_attachment_materializer_uses_binary_flag_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_references() -> set[str]:
        return set()

    binary_flag = 0x8000
    opened_flags: list[int] = []
    real_open = os.open

    def recording_open(path: Path, flags: int, mode: int) -> int:
        opened_flags.append(flags)
        return real_open(path, flags & ~binary_flag, mode)

    monkeypatch.setattr(attachment_module.os, "O_BINARY", binary_flag, raising=False)
    monkeypatch.setattr(attachment_module.os, "open", recording_open)

    materializer = AttachmentMaterializer(lambda: tmp_path, no_references)
    attachment = await materializer.materialize(
        b"line\nbytes", name="payload.bin", kind="file"
    )

    assert opened_flags == [os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag]
    assert attachment.relative_path is not None
    assert (tmp_path / attachment.relative_path).read_bytes() == b"line\nbytes"


@pytest.mark.asyncio
async def test_attachment_materializer_rejects_write_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_references() -> set[str]:
        return set()

    real_write_all = attachment_module._write_all

    def expanding_write(descriptor: int, payload: bytes) -> None:
        real_write_all(descriptor, payload)
        os.write(descriptor, b"extra")

    monkeypatch.setattr(attachment_module, "_write_all", expanding_write)
    materializer = AttachmentMaterializer(lambda: tmp_path, no_references)

    with pytest.raises(OSError, match="write size mismatch"):
        await materializer.materialize(b"payload", name="payload.bin", kind="file")

    assert not tuple((tmp_path / "attachments" / ".staging").iterdir())
    assert not tuple(
        path for path in (tmp_path / "attachments").iterdir() if path.name != ".staging"
    )


@pytest.mark.asyncio
async def test_attachment_reconciliation_removes_only_unreferenced_local_ids(
    tmp_path: Path,
) -> None:
    retained_id = str(uuid7())
    orphan_id = str(uuid7())
    retained = tmp_path / "attachments" / retained_id
    orphan = tmp_path / "attachments" / orphan_id
    retained.mkdir(parents=True)
    orphan.mkdir(parents=True)
    (retained / "content.txt").write_text("retained", encoding="utf-8")
    (orphan / "content.txt").write_text("orphan", encoding="utf-8")

    async def referenced_paths() -> set[str]:
        return {f"attachments/{retained_id}/content.txt"}

    materializer = AttachmentMaterializer(lambda: tmp_path, referenced_paths)
    await materializer.reconcile()

    assert retained.exists()
    assert not orphan.exists()
