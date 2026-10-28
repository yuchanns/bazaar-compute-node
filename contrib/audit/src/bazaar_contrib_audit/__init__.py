"""Publish complete execution events independently of their subscribers."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import monotonic_ns, time_ns
from typing import Annotated, Literal, Protocol
from uuid import uuid7

from bazaar_core import Context, Contrib, EventKey, ServiceKey
from pydantic import BaseModel, ConfigDict, Field, JsonValue

__all__ = [
    "AUDIT",
    "AUDIT_EVENT",
    "Audit",
    "AuditContext",
    "AuditError",
    "AuditEvent",
    "AuditFailure",
    "AuditInfo",
    "NodeAudit",
    "audit",
]


class AuditContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    channel: str | None = None
    harness: str | None = None
    target_kind: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    sender_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    provider_session_id: str | None = None
    provider_turn_id: str | None = None


class AuditError(BaseModel):
    """What went wrong; an event carrying one is a failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str
    message: str | None = None


class _AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    run_id: str
    sequence: int
    event_name: str
    occurred_at_ms: int
    observed_at_ms: int
    monotonic_ns: int
    context: AuditContext
    payload: JsonValue


class AuditInfo(_AuditRecord):
    level: Literal["info"] = "info"


class AuditFailure(_AuditRecord):
    level: Literal["error"] = "error"
    error: AuditError


type AuditEvent = Annotated[AuditInfo | AuditFailure, Field(discriminator="level")]


class Audit(Protocol):
    async def publish(
        self,
        event_name: str,
        *,
        context: AuditContext,
        payload: JsonValue,
        occurred_at_ms: int | None = None,
        error: AuditError | None = None,
    ) -> None: ...


AUDIT = ServiceKey[Audit]("audit")
AUDIT_EVENT = EventKey[AuditEvent]("audit.event")


class NodeAudit:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.run_id = str(uuid7())
        self.sequence = 0

    async def publish(
        self,
        event_name: str,
        *,
        context: AuditContext,
        payload: JsonValue,
        occurred_at_ms: int | None = None,
        error: AuditError | None = None,
    ) -> None:
        observed_at_ms = time_ns() // 1_000_000
        self.sequence += 1
        fields = {
            "id": str(uuid7()),
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_name": event_name,
            "occurred_at_ms": (
                observed_at_ms if occurred_at_ms is None else occurred_at_ms
            ),
            "observed_at_ms": observed_at_ms,
            "monotonic_ns": monotonic_ns(),
            "context": context,
            "payload": payload,
        }
        event: AuditEvent = (
            AuditInfo(**fields)
            if error is None
            else AuditFailure(**fields, error=error)
        )
        try:
            await self.ctx.announce(AUDIT_EVENT, event)
        except Exception:
            logging.getLogger(__name__).exception(
                "audit publication failed", extra={"event_name": event_name}
            )


@asynccontextmanager
async def lifespan(ctx: Context):
    ctx.offer(AUDIT, NodeAudit(ctx))
    yield


audit = Contrib(offers=[AUDIT], lifespan=lifespan)
