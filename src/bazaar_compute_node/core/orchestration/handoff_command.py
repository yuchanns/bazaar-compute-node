from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import time_ns
from uuid import uuid7

from ..audit import ErrorKind
from ..command import IHandoffService, SessionNotFoundError
from ..correlation import CorrelationContext
from ..handoff import (
    HandoffCheckItem,
    HandoffCheckRequest,
    HandoffCheckResult,
    HandoffSendRequest,
    HandoffSendResult,
)
from ..models import BcnSession, Handoff, InboundMessage, RuntimeEventState
from ..storage import (
    HandoffConflictError,
    IHandoffStorageScope,
    IHandoffStorageTransaction,
    InboxTargetResolutionError,
)
from .services import SessionAuditRecorder


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


class HandoffCommandFailure(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


class HandoffCommandService(IHandoffService):
    """Persist and inspect cross-session handoffs for one Agent."""

    def __init__(
        self,
        *,
        storage: IHandoffStorageScope,
        audit: SessionAuditRecorder,
        publish_wake: Callable[[str], Awaitable[None]],
        node_id: Callable[[], str],
        clock: Callable[[], int] | None = None,
        handoff_id: Callable[[], str] | None = None,
    ) -> None:
        self._storage = storage
        self._audit = audit
        self._publish_wake = publish_wake
        self._node_id = node_id
        self._clock = clock or _current_time_ms
        self._handoff_id = handoff_id or (lambda: str(uuid7()))

    async def send(
        self,
        session_id: str,
        request: HandoffSendRequest,
    ) -> HandoffSendResult:
        try:
            async with self._storage.transaction() as transaction:
                await self._require_session(transaction, session_id)
                target_session = await self._resolve_target(
                    transaction,
                    request.target,
                )
                if target_session.id == session_id:
                    raise HandoffCommandFailure(
                        "HANDOFF_TARGET_CURRENT",
                        "Handoff target must belong to another conversation.",
                    )
                source_message_id = await self._resolve_source_message_id(
                    transaction,
                    session_id,
                    request.source_message_id,
                )
                target_anchor = await transaction.get_latest_inbound_message(
                    target_session.id
                )
                if target_anchor is None:
                    raise HandoffCommandFailure(
                        "HANDOFF_TARGET_NOT_READY",
                        "Handoff target has no inbound conversation anchor.",
                    )
                handoff = await transaction.save_handoff(
                    Handoff(
                        handoff_id=self._handoff_id(),
                        command_id=request.command_id,
                        source_session_id=session_id,
                        target_session_id=target_session.id,
                        source_message_id=source_message_id,
                        body=request.body,
                        created_at_ms=request.created_at_ms,
                    )
                )
        except SessionNotFoundError, HandoffCommandFailure:
            raise
        except InboxTargetResolutionError as error:
            raise HandoffCommandFailure(
                "HANDOFF_TARGET_NOT_FOUND",
                str(error),
                next_action="Run `bcc inbox list` and reuse the exact target.",
            ) from error
        except HandoffConflictError as error:
            raise HandoffCommandFailure("HANDOFF_CONFLICT", str(error)) from error

        try:
            await self._publish_wake(handoff.target_session_id)
        except Exception as error:
            await self._audit.append_tool(
                operation="bcc.handoff.send",
                status="failed",
                state=RuntimeEventState.FAILED,
                correlation=self._correlation(
                    session_id,
                    command_id=handoff.command_id,
                ),
                arguments=self._send_audit_arguments(handoff, request.target),
                error_kind=ErrorKind.INTERNAL,
                error_message="Handoff was stored but its wake could not be published.",
            )
            raise HandoffCommandFailure(
                "HANDOFF_WAKE_FAILED",
                "Handoff was stored but its wake could not be published.",
            ) from error

        await self._audit.append_tool(
            operation="bcc.handoff.send",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(
                session_id,
                command_id=handoff.command_id,
            ),
            arguments=self._send_audit_arguments(handoff, request.target),
        )
        return HandoffSendResult(handoff=handoff, target=request.target)

    async def check(
        self,
        session_id: str,
        request: HandoffCheckRequest,
    ) -> HandoffCheckResult:
        try:
            async with self._storage.transaction() as transaction:
                await self._require_session(transaction, session_id)
                handoffs = await transaction.list_pending_handoffs(
                    session_id,
                    limit=request.limit,
                )
                if not handoffs:
                    result = HandoffCheckResult(items=(), has_more=False)
                else:
                    source_targets = []
                    for handoff in handoffs:
                        source_targets.append(
                            await self._resolve_source_target(transaction, handoff)
                        )
                    marked = await transaction.mark_handoffs_read(
                        session_id,
                        tuple(handoff.handoff_id for handoff in handoffs),
                        read_at_ms=self._clock(),
                    )
                    marked_by_id = {handoff.handoff_id: handoff for handoff in marked}
                    if len(marked_by_id) != len(handoffs):
                        raise HandoffCommandFailure(
                            "HANDOFF_CHECK_FAILED",
                            "Pending handoff batch changed before it was marked read.",
                        )
                    result = HandoffCheckResult(
                        items=tuple(
                            HandoffCheckItem(
                                handoff=marked_by_id[handoff.handoff_id],
                                source_target=source_target,
                            )
                            for handoff, source_target in zip(
                                handoffs,
                                source_targets,
                                strict=True,
                            )
                        ),
                        has_more=(
                            await transaction.count_pending_handoffs(session_id) > 0
                        ),
                    )
        except SessionNotFoundError, HandoffCommandFailure:
            raise
        except (TypeError, ValueError) as error:
            raise HandoffCommandFailure("HANDOFF_CHECK_FAILED", str(error)) from error

        await self._audit.append_tool(
            operation="bcc.handoff.check",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(session_id),
            arguments={
                "session_id": session_id,
                "handoff_ids": tuple(item.handoff.handoff_id for item in result.items),
                "count": len(result.items),
                "has_more": result.has_more,
            },
        )
        return result

    @staticmethod
    async def _require_session(
        transaction: IHandoffStorageTransaction,
        session_id: str,
    ) -> None:
        if await transaction.get_bcn_session(session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {session_id}")

    @staticmethod
    async def _resolve_target(
        transaction: IHandoffStorageTransaction,
        target: str,
    ) -> BcnSession:
        return await transaction.resolve_inbox_target(target)

    @staticmethod
    async def _resolve_source_message_id(
        transaction: IHandoffStorageTransaction,
        session_id: str,
        source_message_id: str | None,
    ) -> str | None:
        if source_message_id is None:
            source = await transaction.get_latest_inbound_message(session_id)
            if source is None:
                raise HandoffCommandFailure(
                    "HANDOFF_SOURCE_NOT_READY",
                    "Current conversation has no inbound source anchor.",
                )
            return None
        source = await transaction.resolve_inbound_message(
            session_id,
            source_message_id,
        )
        if source is None:
            raise HandoffCommandFailure(
                "HANDOFF_SOURCE_NOT_FOUND",
                f"Handoff source message was not found in the current session: {source_message_id}",
            )
        return source.message_id

    @staticmethod
    async def _resolve_source_target(
        transaction: IHandoffStorageTransaction,
        handoff: Handoff,
    ) -> str:
        source: InboundMessage | None
        if handoff.source_message_id is None:
            source = await transaction.get_latest_inbound_message(
                handoff.source_session_id
            )
        else:
            source = await transaction.resolve_inbound_message(
                handoff.source_session_id,
                handoff.source_message_id,
            )
        if source is None:
            raise HandoffCommandFailure(
                "HANDOFF_CHECK_FAILED",
                f"Handoff source context is missing: {handoff.handoff_id}",
            )
        return source.canonical_target

    def _correlation(
        self,
        session_id: str,
        *,
        command_id: str | None = None,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self._node_id(),
            bcn_session_id=session_id,
            command_id=command_id,
        )

    @staticmethod
    def _send_audit_arguments(handoff: Handoff, target: str) -> dict[str, object]:
        return {
            "handoff_id": handoff.handoff_id,
            "command_id": handoff.command_id,
            "source_session_id": handoff.source_session_id,
            "target_session_id": handoff.target_session_id,
            "source_message_id": handoff.source_message_id,
            "target": target,
        }


__all__ = ["HandoffCommandFailure", "HandoffCommandService"]
