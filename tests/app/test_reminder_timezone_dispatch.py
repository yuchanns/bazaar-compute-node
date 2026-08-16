from __future__ import annotations

from typing import cast

import pytest

from bazaar_compute_node.app import reminder_dispatch
from bazaar_compute_node.app.reminder_dispatch import CommandDispatcher
from bazaar_compute_node.core.command import ICommandService, IReminderService
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import Reminder, ReminderState
from bazaar_compute_node.core.reminder import (
    ReminderCancelRequest,
    ReminderCancelResult,
    ReminderCheckRequest,
    ReminderCheckResult,
    ReminderListRequest,
    ReminderListResult,
    ReminderScheduleRequest,
    ReminderScheduleResult,
    ReminderSnoozeRequest,
    ReminderSnoozeResult,
    ReminderUpdateRequest,
    ReminderUpdateResult,
)

ANCHOR_ID = "019c5678-0000-7000-8000-000000000001"


class _ReminderService:
    def __init__(self) -> None:
        self.schedule_request: ReminderScheduleRequest | None = None

    async def schedule(
        self,
        session_id: str,
        request: ReminderScheduleRequest,
    ) -> ReminderScheduleResult:
        self.schedule_request = request
        return ReminderScheduleResult(
            Reminder(
                reminder_id="019c1234-0000-7000-8000-000000000001",
                owner_session_id=session_id,
                anchor_message_id=request.message_id,
                title=request.title,
                state=ReminderState.SCHEDULED,
                next_fire_at_ms=request.next_fire_at_ms,
                repeat_rule=request.repeat_rule,
                timezone=request.timezone,
                revision=1,
                last_occurrence_no=0,
                created_at_ms=0,
                updated_at_ms=0,
            )
        )

    async def check(
        self,
        _session_id: str,
        _request: ReminderCheckRequest,
    ) -> ReminderCheckResult:
        raise AssertionError("unexpected Reminder check")

    async def list(
        self,
        _session_id: str,
        _request: ReminderListRequest,
    ) -> ReminderListResult:
        raise AssertionError("unexpected Reminder list")

    async def snooze(
        self,
        _session_id: str,
        _request: ReminderSnoozeRequest,
    ) -> ReminderSnoozeResult:
        raise AssertionError("unexpected Reminder snooze")

    async def update(
        self,
        _session_id: str,
        _request: ReminderUpdateRequest,
    ) -> ReminderUpdateResult:
        raise AssertionError("unexpected Reminder update")

    async def cancel(
        self,
        _session_id: str,
        _request: ReminderCancelRequest,
    ) -> ReminderCancelResult:
        raise AssertionError("unexpected Reminder cancel")


def _dispatcher(service: _ReminderService) -> CommandDispatcher:
    dispatcher = CommandDispatcher(
        cast(ICommandService, object()),
        reminder_service=cast(IReminderService, service),
        timeout_budget=TimeoutBudget(
            startup_seconds=1,
            provider_call_seconds=1,
            command_seconds=1,
            shutdown_seconds=1,
        ),
    )
    dispatcher.start_accepting()
    return dispatcher


def _schedule_request(
    *,
    repeat_rule: str = "daily@09:00",
    timezone: str | None = None,
) -> dict[str, object]:
    return {
        "kind": "command",
        "resource": "reminder",
        "command": "schedule",
        "session_id": "bcn-a",
        "title": "Morning check",
        "message_id": ANCHOR_ID,
        "repeat_rule": repeat_rule,
        "timezone": timezone,
    }


@pytest.mark.asyncio
async def test_schedule_defaults_calendar_timezone_to_system_iana(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reminder_dispatch,
        "system_timezone_name",
        lambda: "Asia/Shanghai",
    )
    service = _ReminderService()

    response = await _dispatcher(service)(_schedule_request())

    assert response["ok"] is True
    assert service.schedule_request is not None
    assert service.schedule_request.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_schedule_explicit_timezone_bypasses_system_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_system_timezone() -> str:
        raise AssertionError("explicit --tz unexpectedly resolved system timezone")

    monkeypatch.setattr(
        reminder_dispatch,
        "system_timezone_name",
        unexpected_system_timezone,
    )
    service = _ReminderService()

    response = await _dispatcher(service)(_schedule_request(timezone="UTC"))

    assert response["ok"] is True
    assert service.schedule_request is not None
    assert service.schedule_request.timezone == "UTC"


@pytest.mark.asyncio
async def test_elapsed_recurrence_does_not_require_system_iana_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_system_timezone() -> str:
        raise AssertionError("elapsed recurrence unexpectedly resolved system timezone")

    monkeypatch.setattr(
        reminder_dispatch,
        "system_timezone_name",
        unexpected_system_timezone,
    )
    service = _ReminderService()

    response = await _dispatcher(service)(_schedule_request(repeat_rule="every:15m"))

    assert response["ok"] is True
    assert service.schedule_request is not None
    assert service.schedule_request.timezone == "UTC"


@pytest.mark.asyncio
async def test_calendar_schedule_without_resolvable_system_timezone_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_system_timezone() -> str:
        raise ValueError("system timezone could not be resolved to an IANA timezone")

    monkeypatch.setattr(
        reminder_dispatch,
        "system_timezone_name",
        missing_system_timezone,
    )
    service = _ReminderService()

    response = await _dispatcher(service)(_schedule_request())

    assert response == {
        "ok": False,
        "code": "REMINDER_TIMEZONE_INVALID",
        "error": "system timezone could not be resolved to an IANA timezone",
        "next_action": "Pass an explicit IANA timezone with `--tz` and retry.",
    }
    assert service.schedule_request is None
