# bcc Full Message ID

## Goal

Make every message identifier emitted by `bcc message check` directly usable by
`bcc message read --around` and `bcc message send --reply-to`.

## Constraints

- Keep the canonical message identity as the complete UUID.
- Do not add short-ID lookup or make sequence numbers part of reply identity.
- Preserve the existing read serializer and cursor semantics.

## Task 1: Emit the canonical UUID from check

- Render `message_id` instead of `short_message_id` in check headers.
- Remove `short_message_id` from the application response and bcc serializer
  contract.
- Update serializer and real SQLite command-process coverage.
- Validate with focused tests, the full non-real-home suite, Ruff, Pyright,
  compileall, lock checking, diff checking, and LSP diagnostics.
