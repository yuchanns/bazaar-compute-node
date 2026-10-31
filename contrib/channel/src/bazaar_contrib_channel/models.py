"""Channel-owned addressing, ingress, delivery and approval presentation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Literal, Self

from bazaar_contrib_harness import ApprovalRequest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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
    id: str | None = Field(default=None, min_length=1)
    name: str | None = None
    kind: Literal["human", "agent", "system", "unknown"] = "human"
    display_name: str | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.id is None and self.name is None:
            raise ValueError("sender identity requires an id or name")
        return self


class ChannelTargetPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    display_name: str | None = None
    handle: str | None = None


class ChannelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["dm", "group"]
    thread_id: str = Field(min_length=1)
    reply_to_message_id: str | None = None


class InboundAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    name: str
    kind: str = "file"
    state: Literal["ready", "failed"] = "ready"
    source: AbstractAsyncContextManager[AsyncIterator[bytes]] | None = Field(
        default=None, exclude=True
    )
    media_type: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.state == "ready" and self.source is None:
            raise ValueError("a ready attachment requires a source")
        if self.state == "failed" and self.source is not None:
            raise ValueError("a failed attachment has no source")
        return self


class OutboundAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    path: Path
    media_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class InboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    provider_message_id: str | None = Field(default=None, min_length=1)
    # an edit of provider_message_id: equal for redeliveries of one edit, distinct across edits
    edit_version: str | None = Field(default=None, min_length=1)
    channel_session_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    target: ChannelTarget
    sender: SenderIdentity
    body: str
    message_type: str = "text"
    target_presentation: ChannelTargetPresentation | None = None
    reply_to_message_id: str | None = None
    received_at_ms: int | None = None
    provider_payload_ref: str | None = None
    attachments: tuple[InboundAttachment, ...] = ()
    occurred_at_ms: int | None = None
    provider_chat_id: str | None = None
    notifies_runtime: bool = True
    mentions_agent: bool = False
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ChannelSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: ChannelTarget
    body: str
    attachments: tuple[OutboundAttachment, ...] = ()
    actor_id: str | None = None
    channel: str | None = None
    channel_identity: str | None = None
    delivery_handle: str | None = None


class ChannelDeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["confirmed", "queued", "partial", "failed", "unknown"]
    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None
    provider_thread_id: str | None = None
    error: str | None = None
    error_kind: str | None = None
    receipt: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def provider_identity(self) -> Self:
        if (
            self.status in {"confirmed", "queued", "partial"}
            and self.provider_message_id is None
            and self.provider_receipt_ref is None
        ):
            raise ValueError(
                "a channel delivery receipt requires a provider identifier"
            )
        return self


class ChannelApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval: ApprovalRequest
    target: ChannelTarget
    sender_id: str
    channel: str | None = None
    channel_identity: str | None = None


class DmRequest(BaseModel):
    """A previously observed sender and the channel that observed them."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sender_id: str | None = None
    sender_name: str | None = None
    sender_kind: Literal["human", "agent", "system", "unknown"]
    channel: str
    channel_identity: str | None = None


class DmAddress(BaseModel):
    """A DM conversation in one channel's own terms."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    channel_session_id: str
    thread_id: str
    provider_thread_id: str
    delivery_handle: str | None = None
    channel_identity: str | None = None
