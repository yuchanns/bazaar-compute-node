"""Correlate approvals and publish Harness events to their connected Channel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
lazy import logging

from bazaar_contrib_audit import Audit, AuditContext
from bazaar_contrib_channel import (
    Channel,
    ChannelApprovalRequest,
    ChannelTarget,
)
from bazaar_contrib_harness import (
    ApprovalRequest,
    ApprovalResult,
    ApprovalScope,
    HarnessOutput,
    SandboxPolicy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalRoute:
    channel: Channel
    target: ChannelTarget
    session_id: str
    turn_id: str
    sender_id: str
    audit: Audit
    context: AuditContext
    sandbox_policy: SandboxPolicy
    sender_kind: str = "human"

    async def request_approval(
        self,
        request: ApprovalRequest,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> ApprovalResult:
        channel_request = ChannelApprovalRequest(
            approval=request, target=self.target, sender_id=self.sender_id
        )
        await self.audit.publish(
            "approval.requested",
            context=self.context,
            payload=channel_request.model_dump(mode="json"),
            occurred_at_ms=request.created_at_ms,
        )
        if (request.session_id, request.turn_id) != (self.session_id, self.turn_id):
            reason = "The approval request for this operation is invalid."
            logger.warning(
                "approval session/turn mismatch: request_id=%r; "
                "expected session_id=%r, turn_id=%r; "
                "received session_id=%r, turn_id=%r; "
                "agent_id=%r, channel=%r, harness=%r, target_kind=%r, thread_id=%r",
                request.request_id,
                self.session_id,
                self.turn_id,
                request.session_id,
                request.turn_id,
                self.context.agent_id,
                self.context.channel,
                self.context.harness,
                self.target.kind,
                self.target.thread_id,
            )
            result = ApprovalResult(
                request_id=request.request_id, decision="rejected", reason=reason
            )
        elif self.sandbox_policy is SandboxPolicy.DANGER_FULL_ACCESS:
            result = ApprovalResult(
                request_id=request.request_id,
                decision="approved",
                scope=next(
                    scope for scope in ApprovalScope if scope in request.allowed_scopes
                ),
                reason="Full-access execution allows tool use without asking.",
            )
        elif self.sender_kind != "human" or not self.sender_id:
            from .instruction import template

            result = ApprovalResult(
                request_id=request.request_id,
                decision="rejected",
                reason=await asyncio.to_thread(
                    lambda: template("approval_no_person.tpl").render()
                ),
            )
        else:
            result = await self.channel.request_approval(
                channel_request, timeout=timeout
            )
        if result.request_id != request.request_id:
            reason = "The approval decision could not be matched to this operation."
            logger.warning(
                "approval result request_id mismatch: "
                "expected request_id=%r, received request_id=%r; "
                "session_id=%r, turn_id=%r; "
                "agent_id=%r, channel=%r, harness=%r, target_kind=%r, thread_id=%r",
                request.request_id,
                result.request_id,
                self.session_id,
                self.turn_id,
                self.context.agent_id,
                self.context.channel,
                self.context.harness,
                self.target.kind,
                self.target.thread_id,
            )
            result = ApprovalResult(
                request_id=request.request_id, decision="rejected", reason=reason
            )
        elif (
            result.decision == "approved" and result.scope not in request.allowed_scopes
        ):
            reason = "The approval scope is not supported for this operation."
            logger.warning(
                "approval result scope mismatch: request_id=%r; "
                "allowed_scopes=%r, received scope=%r; session_id=%r, turn_id=%r; "
                "agent_id=%r, channel=%r, harness=%r, target_kind=%r, thread_id=%r",
                request.request_id,
                request.allowed_scopes,
                result.scope,
                self.session_id,
                self.turn_id,
                self.context.agent_id,
                self.context.channel,
                self.context.harness,
                self.target.kind,
                self.target.thread_id,
            )
            result = ApprovalResult(
                request_id=request.request_id, decision="rejected", reason=reason
            )
        await self.audit.publish(
            "approval.decided",
            context=self.context,
            payload=result.model_dump(mode="json"),
            occurred_at_ms=result.decided_at_ms,
        )
        return result


async def forward(
    audit: Audit,
    context: AuditContext,
    channel: Channel,
    event: HarnessOutput,
    target: ChannelTarget,
    *,
    deliver: bool = True,
) -> None:
    """Publish turn events and forward them to the channel."""
    await audit.publish(
        f"harness.{event.kind}",
        context=context.model_copy(
            update={
                key: value
                for key, value in (
                    ("provider_session_id", event.provider_session_id),
                    ("provider_turn_id", event.provider_turn_id),
                )
                if value is not None
            }
        ),
        payload=event.model_dump(mode="json"),
        occurred_at_ms=event.occurred_at_ms,
    )
    if deliver:
        try:
            channel.accept_turn_event(event, target=target)
        except Exception:
            logger.exception("channel rejected %s", event.kind)
