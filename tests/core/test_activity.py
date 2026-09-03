from __future__ import annotations

from bazaar_compute_node.core.activity import (
    ActivityKind,
    ActivityOverview,
    ActivityReducer,
    ActivityStatus,
    overview_lines,
    snapshot_line,
)
from bazaar_compute_node.core.models import (
    ContentDelta,
    ContentDeltaKind,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    TokenUsage,
    ToolCall,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    UsageUpdated,
)
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator


def _call(call_id: str, name: str = "/bin/bash") -> ToolCall:
    return ToolCall(call_id=call_id, name=name)


def test_tool_lifecycle_updates_snapshot_and_counts_unique_ids() -> None:
    reducer = ActivityReducer()

    assert reducer.apply(ToolCallStarted(call=_call("a")))
    assert reducer.snapshot is not None
    assert reducer.snapshot.kind is ActivityKind.TOOL_CALL
    assert reducer.snapshot.status is ActivityStatus.RUNNING
    assert reducer.snapshot.name == "/bin/bash"

    assert reducer.apply(ToolCallCompleted(call=_call("a")))
    assert reducer.snapshot is not None
    assert reducer.snapshot.status is ActivityStatus.COMPLETED

    assert reducer.apply(ToolCallStarted(call=_call("b", "grep")))
    assert reducer.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert reducer.overview == ActivityOverview(tool_calls=2)


def test_orphan_terminal_tool_events_are_counted() -> None:
    reducer = ActivityReducer()

    assert reducer.apply(ToolCallCompleted(call=_call("a")))
    assert reducer.apply(ToolCallFailed(call=_call("b")))
    assert reducer.snapshot is not None
    assert reducer.snapshot.status is ActivityStatus.FAILED

    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert reducer.overview == ActivityOverview(tool_calls=2)


def test_named_compaction_ids_are_deduplicated() -> None:
    reducer = ActivityReducer()

    reducer.apply(ContextCompactionStarted(compaction_id="c1"))
    reducer.apply(ContextCompactionCompleted(compaction_id="c1"))
    reducer.apply(ContextCompactionStarted(compaction_id="c2"))
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))

    assert reducer.overview == ActivityOverview(context_compactions=2)


def test_anonymous_compaction_pairs_and_counts_orphans() -> None:
    paired = ActivityReducer()
    paired.apply(ContextCompactionStarted())
    paired.apply(ContextCompactionCompleted())
    paired.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert paired.overview == ActivityOverview(context_compactions=1)

    orphan = ActivityReducer()
    orphan.apply(ContextCompactionCompleted())
    orphan.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert orphan.overview == ActivityOverview(context_compactions=1)

    repeated = ActivityReducer()
    repeated.apply(ContextCompactionStarted())
    repeated.apply(ContextCompactionStarted())
    repeated.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert repeated.overview == ActivityOverview(context_compactions=1)


def test_latest_usage_total_replaces_previous_report() -> None:
    reducer = ActivityReducer()

    assert not reducer.apply(
        UsageUpdated(total=TokenUsage(input_tokens=10, output_tokens=1))
    )
    reducer.apply(
        UsageUpdated(
            total=TokenUsage(
                input_tokens=12400,
                cached_input_tokens=9100,
                output_tokens=860,
            )
        )
    )
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))

    assert reducer.overview == ActivityOverview(
        input_tokens=12400,
        cached_input_tokens=9100,
        output_tokens=860,
    )


def test_provider_error_mid_turn_does_not_settle_usage_again() -> None:
    # codex reports a retryable provider error as a started turn; the usage it
    # goes on to report is still cumulative for the same attempt
    reducer = ActivityReducer()

    reducer.apply(TurnStarted(event_name="codex.turn.started"))
    reducer.apply(UsageUpdated(total=TokenUsage(input_tokens=100, output_tokens=50)))
    reducer.apply(TurnStarted(event_name="codex.turn.error"))
    reducer.apply(UsageUpdated(total=TokenUsage(input_tokens=150, output_tokens=80)))
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))

    assert reducer.overview == ActivityOverview(input_tokens=150, output_tokens=80)


def test_a_second_attempt_adds_to_what_the_first_one_spent() -> None:
    reducer = ActivityReducer()

    reducer.apply(TurnStarted(event_name="codex.turn.started"))
    reducer.apply(UsageUpdated(total=TokenUsage(input_tokens=100, output_tokens=50)))
    reducer.apply(TurnStarted(event_name="codex.turn.started"))
    reducer.apply(UsageUpdated(total=TokenUsage(input_tokens=30, output_tokens=20)))
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))

    assert reducer.overview == ActivityOverview(input_tokens=130, output_tokens=70)


def test_usage_only_turn_still_produces_overview() -> None:
    reducer = ActivityReducer()

    reducer.apply(UsageUpdated(total=TokenUsage(input_tokens=5)))
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))

    assert reducer.overview is not None
    assert not reducer.overview.empty
    assert reducer.overview.input_tokens == 5


def test_turn_without_displayable_events_stays_lazy_and_empty() -> None:
    reducer = ActivityReducer()

    assert not reducer.apply(TurnStarted(event_name="codex.turn.started"))
    assert not reducer.apply(
        ContentDelta(kind=ContentDeltaKind.AGENT_MESSAGE, text="hi")
    )
    assert reducer.snapshot is None

    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert reducer.overview is not None
    assert reducer.overview.empty


def test_events_after_terminal_are_ignored() -> None:
    reducer = ActivityReducer()

    reducer.apply(ToolCallStarted(call=_call("a")))
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))
    overview = reducer.overview

    assert not reducer.apply(ToolCallStarted(call=_call("b")))
    assert reducer.overview == overview
    assert reducer.snapshot is None


def test_non_turn_terminal_payload_does_not_finalise_overview() -> None:
    reducer = ActivityReducer()

    reducer.apply(ToolCallStarted(call=_call("a")))
    assert not reducer.apply(
        TurnFailed(
            event_name="codex.tool.failed",
            error_kind="provider_unknown",
        )
    )
    assert reducer.overview is None
    assert reducer.snapshot is not None

    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))
    assert reducer.overview == ActivityOverview(tool_calls=1)


def test_snapshot_line_uses_status_icon_and_translated_kind() -> None:
    reducer = ActivityReducer()
    reducer.apply(ToolCallStarted(call=_call("a", "/bin/bash")))
    assert reducer.snapshot is not None

    english = snapshot_line(create_translator(ENGLISH), reducer.snapshot)
    assert english.icon == "⌛️"
    assert english.label == "Tool call"
    assert english.name == "/bin/bash"

    chinese = snapshot_line(create_translator(SIMPLIFIED_CHINESE), reducer.snapshot)
    assert chinese.label == "工具调用"

    reducer.apply(ContextCompactionStarted())
    assert reducer.snapshot is not None
    compaction = snapshot_line(create_translator(ENGLISH), reducer.snapshot)
    assert compaction.label == "Context compaction"
    assert compaction.name is None


def test_overview_lines_omit_zero_values() -> None:
    overview = ActivityOverview(
        tool_calls=3,
        context_compactions=1,
        input_tokens=12400,
        cached_input_tokens=9100,
        output_tokens=860,
    )

    assert overview_lines(create_translator(SIMPLIFIED_CHINESE), overview) == (
        "工具调用 **3** 次",
        "上下文压缩 **1** 次",
        "输入 **12.4K** · 缓存 **9.1K** · 输出 **860**",
    )
    assert overview_lines(create_translator(ENGLISH), overview) == (
        "Tool calls **3**",
        "Context compactions **1**",
        "Input **12.4K** · Cache **9.1K** · Output **860**",
    )

    assert overview_lines(
        create_translator(SIMPLIFIED_CHINESE), ActivityOverview(input_tokens=120)
    ) == ("输入 **120**",)
    assert overview_lines(
        create_translator(SIMPLIFIED_CHINESE),
        ActivityOverview(cached_input_tokens=9100),
    ) == ("缓存 **9.1K**",)

    assert overview_lines(
        create_translator(ENGLISH), ActivityOverview(tool_calls=2)
    ) == ("Tool calls **2**",)
    assert overview_lines(create_translator(ENGLISH), ActivityOverview()) == ()


def test_negative_or_missing_token_counts_are_treated_as_zero() -> None:
    reducer = ActivityReducer()

    reducer.apply(UsageUpdated(total=TokenUsage(input_tokens=None, output_tokens=0)))
    reducer.apply(TurnCompleted(event_name="codex.turn.completed"))

    assert reducer.overview == ActivityOverview()


def test_terminal_error_message_is_bounded() -> None:
    reducer = ActivityReducer()

    reducer.apply(
        TurnFailed(
            event_name="codex.turn.failed",
            error_kind="provider_failed",
            error_message="x" * 5_000,
        )
    )

    overview = reducer.overview
    assert overview is not None
    assert overview.error_message is not None
    assert len(overview.error_message) == 1_000
    assert overview.error_message.endswith("…")


def test_usage_totals_accumulate_across_runtime_attempts() -> None:
    reducer = ActivityReducer()

    reducer.apply(TurnStarted(event_name="codex.turn.started"))
    reducer.apply(
        UsageUpdated(
            total=TokenUsage(input_tokens=100, cached_input_tokens=40, output_tokens=10)
        )
    )
    reducer.apply(TurnStarted(event_name="claudecode.turn.started"))
    reducer.apply(
        UsageUpdated(
            total=TokenUsage(input_tokens=50, cached_input_tokens=0, output_tokens=5)
        )
    )
    reducer.apply(TurnCompleted(event_name="claudecode.turn.completed"))

    overview = reducer.overview
    assert overview is not None
    assert overview.input_tokens == 150
    assert overview.cached_input_tokens == 40
    assert overview.output_tokens == 15
