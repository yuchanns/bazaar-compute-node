"""Replace the default agent entry with an independently installed editorial agent."""

import asyncio
import json
from pathlib import Path

from bazaar_compute_node.boot import node
from bazaar_core import (
    CONTRIB_CHANGED,
    Context,
    Contrib,
    ContribEntry,
    ContribSpec,
    load,
)
from bcn_agent_workbench import configured
from provider_smoke import connect, report


async def exercise(path: Path):
    await asyncio.to_thread(path.mkdir)
    draft = path / "draft.txt"
    await asyncio.to_thread(
        draft.write_text,
        "The editorial team needs a short report for the Friday edition.",
    )
    brief = "You are the Friday edition assistant. Prepare editorial reports and request the editor's approval before saving."
    title = "Friday editorial desk"
    composition = await asyncio.to_thread(
        load,
        [
            ContribSpec(
                contrib="editor-agent",
                config={
                    "id": "desk",
                    "name": "Editorial",
                    "workspace": str(path),
                    "brief": brief,
                    "title": title,
                },
            ),
        ],
    )

    ready = asyncio.Event()

    def changed(ctx: Context, _):
        ready.set()

    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="ready", definition=Contrib(listen={CONTRIB_CHANGED: changed})
                ),
            )
        }
    )
    composition = await configured(path / "config.toml", composition)
    async with node(
        configer_options=(str(path / "config.toml"),), start=lambda _: composition
    ) as hub:
        peer = await connect(path / "editor.json", ready, hub)
        try:
            saved = await report(peer, draft)
            data = json.loads(await asyncio.to_thread(saved.read_text))
            session = json.loads(
                await asyncio.to_thread((saved.parent / "session.json").read_text)
            )
            assert data["words"] > 0 and data["editor"] == title
            assert session["instructions"] == [brief]
            assert data["context"]["agent_id"] == "desk"
        finally:
            await peer.aclose()
    assert await asyncio.to_thread((path / "editor.closed").read_text)
    assert await asyncio.to_thread((saved.parent / "session.closed").read_text)
