# Runtime Pre-start Recovery

## Goal

Recover the current inbound notification when an idle runtime process has exited
before `turn/start` is written to the provider.

## Constraints

- Reuse the existing `FAILED -> STARTING -> resume_session()` orchestration path.
- Retry the existing turn execution path at most once.
- Do not add background process watchers, lifecycle streams, idle TTLs, or a
  second implementation of session resume or turn start.
- Do not replay a turn after the provider request may have been written; those
  outcomes remain `UNKNOWN`.

## Design

Add a provider-neutral `RuntimeSessionUnavailable` exception to the runtime
contract. The Codex adapter raises it only when its local connection is absent
or already stopped before calling the provider.

`SessionTurnCoordinator` preserves this exception instead of converting it to
an ordinary failed turn. `SessionOrchestrator` catches it, advances AgentState to
`FAILED`, and re-enters the existing session ensure and turn execution sequence.
A second pre-start failure completes the turn as failed instead of looping.

## Task 1: Implement and verify the bounded recovery path

- Add the provider-neutral exception and translate the Codex pre-start check.
- Re-enter existing session recovery and turn execution once from the current
  notification.
- Add coverage proving the same inbound resumes the persisted provider thread,
  completes once, and does not create a second runtime attempt.
- Add coverage proving a repeated pre-start failure stops after one retry.
- Run focused tests, the full test suite, Ruff format/check, Pyright,
  compileall, lock verification, diff checks, and LSP diagnostics for changed
  production files.
