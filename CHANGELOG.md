# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.12 - 2026-08-18

## What's Changed
* feat: localize approval responses and add pyright checks by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/24


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.11...v0.1.12

## 0.1.11 - 2026-08-18

## What's Changed
* docs: mark multi-agent support complete by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/22
* fix: enforce UTF-8 in Windows bcc wrappers by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/23


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.10...v0.1.11

## 0.1.10 - 2026-08-18

## What's Changed
* feat: add multi-agent support by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/18
* fix: complete Windows cross-platform compatibility by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/19
* feat: add channel and sender identities by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/20
* feat: report terminal runtime errors by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/21


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.9...v0.1.10

## 0.1.9 - 2026-08-17

## What's Changed
* docs: update README overview and feature matrix by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/15
* fix: render bcc message times in local timezone by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/16
* feat: add Telegram channel support by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/17


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.8...v0.1.9

## 0.1.8 - 2026-08-15

## What's Changed
* chore: upgrade GitHub Actions by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/10
* feat: add bcc reminder support by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/12


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.7...v0.1.8

## 0.1.7 - 2026-08-14

## What's Changed
* Keep runtime sessions process-local with idle recycling by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/8
* Expire live runtimes when Codex context changes by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/9


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.6...v0.1.7

## 0.1.6 - 2026-08-13

## What's Changed
* 支持 channel-neutral 主动发送附件 by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/6
* 默认排除端到端测试 by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/7


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.5...v0.1.6

## 0.1.5 - 2026-08-12

## What's Changed
* Steer active runtime turns and generate release changelog by @yuchanns in https://github.com/yuchanns/bazaar-compute-node/pull/5


**Full Changelog**: https://github.com/yuchanns/bazaar-compute-node/compare/v0.1.4...v0.1.5

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
