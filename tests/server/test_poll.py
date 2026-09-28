from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.permissions import Permission
from bazaar_compute_server.protocol import Event
from bazaar_compute_server.storage import Role

from ._serving import enrol, serving_app, signed_in
from .test_pages import _event, _health, _short


@pytest.mark.asyncio
async def test_poll_tracks_independent_consumers_from_persisted_data(
    tmp_path: Path,
) -> None:
    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        computer = (await enrol(storage, "poll computer")).computer
        agent = {
            "agent_id": "poll-agent",
            "name": "Polling agent",
            "status": "started",
            "channels": ["test"],
            "runtimes": ["test"],
        }
        await storage.record_events(
            computer.id, "poll-run", [Event.model_validate(_health([agent], 1))]
        )
        computer_ref, agent_ref, thread_ref = (
            await _short(storage, computer.id, agent["agent_id"], "thread-1")
        ).split("/")
        async with signed_in(base, storage) as session:
            async with session.get(base + "/agents") as response:
                page = await response.text()
            descriptors = [
                json.loads(html.unescape(text))
                for text in re.findall(r"data-(?:poll|agent)='([^']+)'", page)
            ]
            subscriptions = [
                {"id": str(i), "scope": d["scope"], "seen": d["seen"]}
                for i, d in enumerate(descriptors)
            ]
            subscriptions += [
                {
                    "id": "events",
                    "scope": {
                        "topic": "events",
                        "computer": computer_ref,
                        "agent": agent_ref,
                    },
                    "seen": {"since": 0},
                },
                {
                    "id": "messages",
                    "scope": {
                        "topic": "messages",
                        "computer": computer_ref,
                        "agent": agent_ref,
                        "thread": thread_ref,
                    },
                    "seen": {"since": 0, "offline": False},
                },
            ]
            async with session.post(
                base + "/poll", json={"subscriptions": subscriptions}
            ) as response:
                assert response.status == 200
                assert not (await response.json())["changes"]
            await storage.record_events(
                computer.id,
                "poll-run",
                [
                    Event.model_validate(
                        _event(2, "tool_call.completed", agent["agent_id"], name="read")
                    )
                ],
            )
            async with session.post(
                base + "/poll", json={"subscriptions": subscriptions}
            ) as response:
                changes = (await response.json())["changes"]
            assert {item["id"] for item in changes} == {"events"}
            subscriptions[-2]["seen"] = changes[0]["state"]
            await storage.record_events(
                computer.id,
                "poll-run",
                [
                    Event.model_validate(
                        _event(3, "runtime.request.turn.started", agent["agent_id"])
                    )
                ],
            )
            async with session.post(
                base + "/poll", json={"subscriptions": subscriptions}
            ) as response:
                changes = (await response.json())["changes"]
            statuses = [item for item in changes if "data" in item]
            assert statuses and all(
                item["data"]["status"] == "busy" for item in statuses
            )
            member_id = next(
                s["id"] for s in subscriptions if s["scope"]["topic"] == "agents"
            )
            assert all(item["id"] != member_id for item in changes)
            # Another account's own list works alongside an inaccessible object.
            account = await storage.add_account("other", "other password")
            role = Role(
                "reader",
                "Reader",
                frozenset({Permission.AGENTS_VIEW}),
                now_ms(),
                now_ms(),
            )
            await storage.save_role(role)
            await storage.set_account_roles(account.id, [role.id])
            async with signed_in(base, storage, "other") as other:
                async with other.post(
                    base + "/poll",
                    json={
                        "subscriptions": [
                            {
                                "id": "list",
                                "scope": {"topic": "agents", "until": None},
                                "seen": None,
                            },
                            {
                                "id": "hidden",
                                "scope": {
                                    "topic": "agent-status",
                                    "computer": computer_ref,
                                    "agent": agent_ref,
                                },
                                "seen": None,
                            },
                        ]
                    },
                ) as response:
                    result = (await response.json())["changes"]
                assert next(v for v in result if v["id"] == "hidden")["unavailable"]
                assert "state" in next(v for v in result if v["id"] == "list")
