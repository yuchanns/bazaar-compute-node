"""Recover a real channel delivery, reconcile uncertainty, and keep sending."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from bazaar_compute_node.boot import node
from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_INPUT,
    ChannelInput,
    ChannelRetryPolicy,
    ChannelSender,
    ChannelSendRequest,
    ChannelTarget,
)
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib, ContribEntry, ContribSpec, load
from bcn_agent_workbench import configured


async def exercise(directory: Path):
    senders: list[ChannelSender] = []

    @asynccontextmanager
    async def inputs(ctx: Context):
        ctx.offer(
            CHANNEL_INPUT,
            ChannelInput(agent_id="editor", agent_name="Editor", workspace=directory),
        )
        yield

    @asynccontextmanager
    async def consumer(ctx: Context):
        senders.append(
            ChannelSender(
                ctx.accept(CHANNEL),
                ctx.accept(TIMER),
                policy=ChannelRetryPolicy(timeout=2),
            )
        )
        yield

    composition = await asyncio.to_thread(load, (ContribSpec(contrib="test-channel"),))
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="input",
                    definition=Contrib(offers=[CHANNEL_INPUT], lifespan=inputs),
                ),
                ContribEntry(
                    name="consumer",
                    definition=Contrib(accepts=[CHANNEL, TIMER], lifespan=consumer),
                ),
            )
        }
    )
    composition = await configured(directory / "config.toml", composition)
    async with node(
        configer_options=(str(directory / "config.toml"),), start=lambda _: composition
    ):
        endpoint = json.loads(
            await asyncio.to_thread((directory / "channel.json").read_text)
        )
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint["port"])
        await asyncio.sleep(0)
        sender = senders[0]
        target = ChannelTarget(kind="dm", thread_id="printer")
        try:
            sending = asyncio.create_task(
                sender.send(
                    ChannelSendRequest(
                        target=target,
                        body="\n".join(
                            f"第{index}批海报请核对印刷时间、纸张规格和交付安排。"
                            for index in range(1, 201)
                        ),
                    )
                )
            )
            first = json.loads(await reader.readline())
            await asyncio.sleep(0.8)
            writer.write(json.dumps({"id": first["id"]}).encode() + b"\n")
            await writer.drain()
            part = json.loads(await reader.readline())
            assert part["part"]["index"] > first["part"]["index"]
            # Recovery outlasts one timeout; every attempt keeps its own budget.
            for delay in (2.2, 0.05):
                await asyncio.sleep(0.8)
                writer.write(
                    json.dumps(
                        {
                            "id": part["id"],
                            "error": {
                                "kind": "channel_timeout",
                                "message": "The upstream token endpoint timed out before sending.",
                                "phase": "prepare",
                                "delivery": "not_sent",
                                "retryable": True,
                                "code": "token_timeout",
                                "retry_after": delay,
                            },
                        },
                    ).encode()
                    + b"\n"
                )
                await writer.drain()
                recovered = json.loads(await reader.readline())
                assert recovered["operation_id"] == part["operation_id"]
                part = recovered
            await asyncio.sleep(0.8)
            writer.write(json.dumps({"id": part["id"]}).encode() + b"\n")
            await writer.drain()
            receipt = await sending
            assert receipt.status == "confirmed" and receipt.provider_message_id
            assert "token_timeout" in json.dumps(receipt.receipt)

            sending = asyncio.create_task(
                sender.send(
                    ChannelSendRequest(
                        target=target, body="海报已经交给印刷，请保存交付回执。"
                    )
                )
            )
            uncertain = json.loads(await reader.readline())
            # The recipient retains this delivery while its acknowledgement is lost.
            saved = directory / "printer-receipt.json"
            await asyncio.to_thread(
                saved.write_text, json.dumps(uncertain["outbound"], ensure_ascii=False)
            )
            receipt = await sending
            assert receipt.status == "unknown"
            evidence = json.dumps(receipt.receipt)
            assert "channel_timeout" in evidence
            sending = asyncio.create_task(
                sender.send(
                    ChannelSendRequest(target=target, body="请继续确认纸张规格。")
                )
            )
            following = json.loads(await reader.readline())
            assert "纸张规格" in following["outbound"]["body"]
            writer.write(json.dumps({"id": following["id"]}).encode() + b"\n")
            await writer.drain()
            assert (await sending).status == "confirmed"
            assert "已经交给印刷" in await asyncio.to_thread(saved.read_text)
        finally:
            writer.close()
            await writer.wait_closed()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="bcs-") as directory:
        asyncio.run(exercise(Path(directory)))
