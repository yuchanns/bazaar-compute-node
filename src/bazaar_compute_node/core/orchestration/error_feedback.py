from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from types import MappingProxyType

from ...i18n import Translator
from ..audit import ErrorKind
from ..channel import ChannelSendRequest
from ..correlation import CorrelationContext
from ..models import (
    Message,
    OutboundDeliveryState,
    RuntimeEventState,
    RuntimeTurn,
    RuntimeTurnState,
)
from ..storage import IStorageScope
from .delivery import OutboundDeliveryService
from .reminder import resolve_reminder_anchor
from .services import SessionAuditRecorder

MESSAGE_KEYS: Mapping[RuntimeTurnState, str] = MappingProxyType(
    {
        RuntimeTurnState.FAILED: "runtime.error.failed",
        RuntimeTurnState.UNKNOWN: "runtime.error.unknown",
    }
)
_SUCCESS_STATES = frozenset(
    {
        OutboundDeliveryState.SENT,
        OutboundDeliveryState.QUEUED,
    }
)


class RuntimeErrorReporter:
    """Deliver one localized Channel reply for a terminal runtime error."""

    def __init__(
        self,
        *,
        agent_id: str,
        delivery: OutboundDeliveryService,
        storage: IStorageScope,
        audit: SessionAuditRecorder,
        translator: Translator,
        detail: Callable[[str, str], str],
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        self._agent_id = agent_id
        self._delivery = delivery
        self._storage = storage
        self._audit = audit
        self._translator = translator
        self._detail = detail
        self._logger = logging.getLogger(
            "bazaar_compute_node.orchestration.error_feedback"
        )

    async def report(self, message: Message, turn: RuntimeTurn | None) -> None:
        if turn is None:
            return
        message_key = MESSAGE_KEYS.get(turn.state)
        if message_key is None:
            return

        terminal_detail = turn.error_message or turn.error_kind or turn.state.value
        feedback_detail = self._detail(message.session_id, terminal_detail)
        correlation = CorrelationContext(
            node_id=self._agent_id,
            channel=message.channel,
            channel_session_id=message.channel_session_id,
            bcn_session_id=message.session_id,
            runtime_session_id=turn.session_id,
            turn_id=turn.turn_id,
            inbound_seq=message.seq,
            provider_thread_id=message.provider_thread_id,
            provider_turn_id=turn.provider_turn_id,
        )
        await self._record(
            event_name="runtime.error_feedback.started",
            state=RuntimeEventState.STARTED,
            correlation=correlation,
            metadata={"terminal_state": turn.state.value},
        )
        provider_thread_id = message.provider_thread_id
        if provider_thread_id is None:
            raise RuntimeError("runtime error feedback has no provider thread")
        anchor = await resolve_reminder_anchor(
            self._storage,
            self._agent_id,
            message,
        )
        result = await self._delivery.deliver(
            ChannelSendRequest(
                session_id=message.session_id,
                body=self._translator.text(message_key, {"error": feedback_detail}),
                attachments=(),
                target_kind=message.target_kind,
                provider_thread_id=provider_thread_id,
                provider_reply_to_message_id=(
                    anchor.provider_message_id if anchor is not None else None
                ),
            )
        )
        metadata: dict[str, object] = {
            "terminal_state": turn.state.value,
            "delivery_state": result.state.value,
        }
        if result.provider_receipt_ref is not None:
            metadata["provider_receipt_ref"] = result.provider_receipt_ref
        if result.state in _SUCCESS_STATES:
            await self._record(
                event_name="runtime.error_feedback.sent",
                state=RuntimeEventState.COMPLETED,
                correlation=correlation,
                metadata=metadata,
            )
            return

        error_kind = self._delivery_error_kind(result.state, result.error_kind)
        error_message = result.error_message or (
            f"error feedback delivery {result.state.value}"
        )
        await self._record(
            event_name="runtime.error_feedback.failed",
            state=RuntimeEventState.FAILED,
            correlation=correlation,
            error_kind=error_kind,
            error_message=self._detail(message.session_id, error_message),
            metadata=metadata,
        )

    async def _record(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
        metadata: dict[str, object],
    ) -> None:
        try:
            await self._audit.append(
                event_name=event_name,
                state=state,
                correlation=correlation,
                error_kind=error_kind,
                error_message=error_message,
                metadata=metadata,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("runtime error feedback audit failed")

    @staticmethod
    def _delivery_error_kind(
        state: OutboundDeliveryState,
        error_kind: str | None,
    ) -> ErrorKind:
        if error_kind is not None:
            try:
                return ErrorKind(error_kind)
            except ValueError:
                pass
        if state is OutboundDeliveryState.PARTIAL:
            return ErrorKind.PROVIDER_PARTIAL
        if state is OutboundDeliveryState.UNKNOWN:
            return ErrorKind.PROVIDER_UNKNOWN
        return ErrorKind.PROVIDER_FAILED


__all__ = ["RuntimeErrorReporter"]
