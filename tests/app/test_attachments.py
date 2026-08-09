from __future__ import annotations

from pathlib import Path
from uuid import uuid7

import pytest

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
