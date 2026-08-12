# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.4 - 2026-08-12

### Changed

- Release version commits are created through GitHub's GraphQL API and must
  carry a valid GitHub signature before packaging starts.
- Release finalization verifies the exact annotated tag object before pushing
  it after a successful PyPI publication.

### Removed

- Runtime stream events are no longer persisted in SQLite. Existing databases
  remove the obsolete event storage and reclaim its unused space during
  migration, while durable message and turn boundaries remain available.

## 0.1.3 - 2026-08-12

### Added

- A manually dispatched, OIDC-backed release workflow publishes reproducible
  wheel and source distributions to PyPI without a long-lived API token.
- Package smoke tests verify the installed CLI and version from both wheel and
  source distributions before publication.

### Changed

- The CLI version is read from installed package metadata instead of a second
  hard-coded runtime constant.

## 0.1.2 - 2026-08-11

### Changed

- High-volume runtime stream deltas are delivered as transient channel updates
  instead of being persisted individually, while lifecycle boundaries remain
  durable.
- The README documents Agent and Channel update behavior.

## 0.1.1 - 2026-08-11

### Added

- WeCom inbound messages preserve quoted-message context through the common
  message model and runtime prompt.
- `bcc` check output exposes complete message UUIDs that can be reused directly
  by read and reply operations.

### Changed

- Runtime recovery happens before a new turn starts and no longer blocks
  inbound message persistence.
- Codex App Server JSONL output is consumed incrementally to avoid pipe
  backpressure on long-running turns.
- Codex resumes a conversation without replaying stored turn history as a new
  prompt.

## 0.1.0 - 2026-08-10

### Added

- An asynchronous node application with runtime-neutral session orchestration,
  lifecycle state, approvals, audit events, and correlation identifiers.
- A Codex App Server runtime with a persistent workspace, local process
  lifecycle management, and sandbox policy configuration.
- WeCom inbound and outbound message delivery.
- Durable SQLite storage for sessions, messages, runtime attempts, consumer
  cursors, and outbound delivery state.
- Local `bcc` commands for runtime operations, inbox inspection, message reads,
  and guarded sends over cross-platform local IPC.
- Cross-platform daemon lifecycle support and user-facing installation and
  operation documentation.
