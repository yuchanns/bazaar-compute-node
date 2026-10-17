"""Channel-owned addressing, ingress, delivery and approval presentation."""

from __future__ import annotations

from pathlib import Path
from time import time_ns
from typing import Literal

from bazaar_contrib_harness import ApprovalRequest
from pydantic import BaseModel, ConfigDict, Field


class ChannelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    agent_name: str
    workspace: Path


class ChannelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    name: str | None = None


class SenderIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    name: str | None = None
    kind: Literal["human", "agent", "system", "unknown"] = "human"


class ChannelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["dm", "group"]
    thread_id: str = Field(min_length=1)
    reply_to_message_id: str | None = None


class InboundAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    path: Path
    media_type: str | None = None


class OutboundAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    path: Path
    media_type: str | None = None


class InboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    target: ChannelTarget
    sender: SenderIdentity
    body: str
    attachments: tuple[InboundAttachment, ...] = ()
    occurred_at_ms: int = Field(default_factory=lambda: time_ns() // 1_000_000)


class ChannelSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: ChannelTarget
    body: str
    attachments: tuple[OutboundAttachment, ...] = ()


class ChannelDeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["confirmed", "queued", "partial", "failed", "unknown"]
    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None
    provider_thread_id: str | None = None
    error: str | None = None


class ChannelApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval: ApprovalRequest
    target: ChannelTarget
    sender_id: str
