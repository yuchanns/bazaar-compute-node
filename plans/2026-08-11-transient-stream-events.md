# Transient Stream Events

## Goal

Keep high-volume runtime stream updates out of durable storage and audit paths so
provider terminal events are not delayed behind thousands of UI-oriented deltas.

## Constraints

- Preserve durable turn, item lifecycle, error, approval, and terminal facts.
- Do not persist or audit text, reasoning, command output, patch, or progress
  updates individually.
- Keep the runtime contract provider-neutral and do not expose Codex protocol
  types outside the Codex adapter.
- Let each channel decide whether to render, coalesce, queue, or discard transient
  stream events. WeCom discards them.
- Stream event delivery is fire-and-forget. The coordinator must not await a
  response, and channel handling cannot change the durable turn outcome.
- Remove historical transient progress rows from SQLite without deleting turn or
  item lifecycle, error, approval, or terminal facts.
- Do not add the empty-delta watchdog or recovery turn until the transient path is
  deployed and raw payload behavior has been observed without storage backlog.

## Design

Add provider-neutral `StreamEvent` and `StreamEventKind` models, then let
`IRuntimeTurnStream` yield a `RuntimeEvent | StreamEvent` union.
`RuntimeEvent` remains the only durable stream item. It carries turn lifecycle
and terminal events, errors, and authoritative `item/started` and
`item/completed` lifecycle facts.

The Codex adapter maps `turn/progress` and non-lifecycle `item/*` notifications to
generic agent-message, plan, reasoning-summary, reasoning-text, command-output,
file-change, tool-progress, item-progress, and turn-progress event kinds. A stream
event contains only its kind, creation time, normalized session routing identity,
an optional `stream_id` for coalescing interleaved output, and normalized content.
Events retain provider order and never expose a runtime session, provider turn,
Codex item, content index, method, or raw provider payload to a channel. Unknown
future non-lifecycle `item/*` notifications use the generic item-progress kind
instead of silently entering the durable event pipeline.

Add a synchronous fire-and-forget channel offer. A channel may only accept the
event, enqueue it locally in a bounded structure, or discard it; it cannot await
provider I/O or return a delivery result in the turn-consumption path. The
coordinator isolates channel exceptions and immediately continues consuming the
runtime stream. `WeComChannel` implements an explicit no-op. Test channels record
updates for contract verification.

`SessionTurnCoordinator` continues to apply `RuntimeEvent` through storage,
state transitions, and audit. It offers `StreamEvent` only to the channel
handoff. Consequently, a terminal event arriving after a large delta burst is no
longer serialized behind one transaction and one audit append per delta.

Add schema migration 8 to delete historical `codex.turn.progress` rows whose
`metadata_json.provider_method` has the same transient classification used by the
Codex adapter: `turn/progress` and non-lifecycle `item/*` methods. Preserve
`turn/started`, `item/started`, `item/completed`, auto-approval lifecycle methods,
and all errors and terminal events. The migration changes no event sequence or
foreign-key identity; gaps in the append-only sequence remain historical facts.

## Task 1: Implement and verify the transient stream event boundary

- Add `StreamEvent`, its provider-neutral kind enum, and update the runtime
  stream and channel contracts.
- Split Codex notification mapping into durable lifecycle events and transient
  updates while preserving normalized correlation and content in memory.
- Dispatch transient updates through the non-blocking channel handoff and make
  WeCom discard them explicitly.
- Add migration 8 and migration coverage proving only historical transient rows
  are deleted while durable lifecycle and terminal rows remain intact.
- Add coverage proving a large delta burst creates no durable delta events or
  audits, preserves update order at the channel boundary, and reaches the
  terminal event without channel latency or failures changing the turn outcome.
- Add coverage proving `item/started`, `item/completed`, errors, and terminal
  events remain durable.
- Run focused tests, the non-real-home suite, Ruff format/check, Pyright,
  compileall, lock verification, diff checks, and LSP diagnostics for changed
  production files.
