from __future__ import annotations

from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.core.orchestration.turn import inbox_notice, reminder_notice


def _rendered_instructions() -> str:
    return DeveloperInstructionContext(
        node_id="node-test",
        runtime_session_id="session-test",
        runtime="test-runtime",
        workspace="workspace-from-node",
    ).render()


def test_developer_instructions_render_runtime_context() -> None:
    rendered = _rendered_instructions()

    assert "Node ID: node-test" in rendered
    assert "Runtime session ID: session-test" in rendered
    assert "Runtime: test-runtime" in rendered
    assert "Workspace: workspace-from-node" in rendered
    assert '`--attachment "<path>"`' in rendered
    assert "stdin body is optional when at least one attachment is present" in rendered
    assert "{{" not in rendered
    assert "}}" not in rendered


def test_developer_instructions_publish_only_real_bcc_command_families() -> None:
    rendered = _rendered_instructions()

    assert (
        "1. **Messages** — `bcc message check`, `bcc message send`, `bcc message read`."
    ) in rendered
    assert "2. **Thread attention** — `bcc thread unfollow`." in rendered
    assert (
        "3. **Reminders** — `bcc reminder schedule`, `bcc reminder check`, "
        "`bcc reminder list`, `bcc reminder snooze`, `bcc reminder update`, "
        "`bcc reminder cancel`."
    ) in rendered
    assert "bcc inbox check" not in rendered
    assert "bcc reminder log" not in rendered
    assert "--msg-id" not in rendered
    assert "bcc reminder schedule --channel" not in rendered


def test_developer_instructions_define_bcn_reminder_semantics() -> None:
    rendered = _rendered_instructions()

    assert "### Reminders" in rendered
    assert "anchored to an inbound bcn message" in rendered
    assert "wakes the bcn session that scheduled it" in rendered
    assert "The fire itself does not send a message or system receipt" in rendered
    assert "does not call the external Channel" in rendered
    assert "passing its message ID explicitly with `--message-id`" in rendered
    assert (
        "The anchor must be an inbound message in the current bcn session" in rendered
    )
    assert "A fired one-time Reminder can be snoozed back to scheduled" in rendered
    assert "update and cancel apply only to scheduled Reminders" in rendered
    assert "`check` marks the occurrences it returns as read" in rendered
    assert "does not mean the task described by the Reminder is complete" in rendered
    assert "`ScheduleWakeup`" in rendered
    assert "`CronCreate`" in rendered


def test_developer_instructions_define_localtime_and_calendar_timezone_semantics() -> (
    None
):
    rendered = _rendered_instructions()

    assert "BCN host's local time" in rendered
    assert "explicit numeric UTC offset" in rendered
    assert "do not reinterpret the displayed wall-clock value as UTC" in rendered
    assert "Durable timestamps remain UTC/epoch internally" in rendered
    assert "time=2026-03-15T09:00:00+08:00" in rendered
    assert "system IANA timezone at Reminder creation time" in rendered
    assert "that concrete timezone is persisted with the Reminder" in rendered
    assert "`every:*` rules are elapsed intervals" in rendered
    assert "`--fire-at` is an absolute ISO-8601 time" in rendered
    assert "must include an explicit UTC offset" in rendered


def test_developer_instructions_match_runtime_notice_contracts() -> None:
    rendered = _rendered_instructions()

    message_example = inbox_notice("<session-id>", 17).replace(
        "17 unread message(s)", "<n> unread message(s)"
    )
    reminder_example = reminder_notice("<session-id>", 17).replace(
        "Reminders pending: 17.", "Reminders pending: <n>."
    )

    assert message_example in rendered
    assert reminder_example in rendered
    assert "These are separate notice types" in rendered
    assert "does not combine message and Reminder counts into one notice" in rendered
    assert "Do not call `bcc message check` merely because a Reminder fired" in rendered
