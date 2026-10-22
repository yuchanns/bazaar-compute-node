"""Replace the default agent entry with an independently installed editorial agent."""

import asyncio
import json
from pathlib import Path

from bazaar_core import (
    CONTRIB_CHANGED,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
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
            ContribSpec(contrib="audit"),
            ContribSpec(contrib="storage"),
            ContribSpec(
                contrib="sqlite", config={"path": str(path / "editor.sqlite3")}
            ),
            ContribSpec(contrib="database", config={"backend": "sqlite"}),
            ContribSpec(contrib="schema"),
            ContribSpec(contrib="legacy-node-migrations"),
            ContribSpec(contrib="timer-wheel"),
            ContribSpec(contrib="messages-node"),
            ContribSpec(contrib="sidecar"),
            ContribSpec(
                contrib="sidecar-tcp",
                config={"endpoint": str(path / "sidecar-port")},
            ),
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
    composition = await configured(path / "config.toml", composition)
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
    async with ContribHub() as hub, hub.enable(composition):
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
