# Changelog Generation Plan

## Goal

Add a release-facing `CHANGELOG.md` whose historical sections are traceable to
the repository's annotated release tags, then add one changelog-generation
step to the existing `Release` workflow.

The current release state machine remains unchanged: a maintainer manually
dispatches `Release` with a stable version, the workflow updates the project
version, creates the release commit, builds and publishes the distributions,
and finally pushes the annotated tag. Changelog generation happens immediately
before the existing release commit.

## Evidence and boundaries

- The authoritative history contains five annotated tags: `v0.1.0` through
  `v0.1.4`.
- Historical release contents are reconstructed from each tag's peeled commit,
  the previous-tag diff, first-parent history, merged changes, and the relevant
  plans. Tag dates provide the release dates.
- `feat/inbox-notice-turn-steer` is not part of `main` or any released tag. Its
  changes must not appear in historical sections.
- The current `Release` workflow remains a manually dispatched workflow with a
  stable `X.Y.Z` input.
- This task does not create a release pull request, enable branch protection,
  create a GitHub Release object, change PyPI publication authority, merge a
  branch, publish a package, or rewrite an existing tag.
- Changelog generation uses GitHub's official generate-notes API. The API
  response is used as Markdown content only; invoking it does not create a
  GitHub Release.
- No standalone changelog script, changelog-specific test module, LLM content
  generation, or commit-history fallback is added.

## Changelog format

Use a compact released-version-only structure:

```text
# Changelog

## 0.1.4 - 2026-08-12

### Changed

- ...
```

There is no `Unreleased` section and no reference-link table. Historical
sections are curated manually. For a future release, the workflow adds a plain
`## X.Y.Z - YYYY-MM-DD` heading near the top of the file and places the GitHub
generate-notes response body below it without reclassifying or regenerating the
content.

## Historical reconstruction

### 0.1.0 - 2026-08-10

- async application, session orchestration, and runtime-neutral core contracts;
- Codex App Server runtime with persistent workspace and approval bridge;
- WeCom inbound/outbound delivery;
- durable SQLite message/session/turn boundaries and local `bcc` IPC commands;
- cross-platform daemon lifecycle and user-facing installation documentation.

### 0.1.1 - 2026-08-11

- recover runtime state before starting a turn and decouple inbound persistence
  from runtime recovery;
- support quoted WeCom messages;
- expose complete message identifiers through `bcc`;
- improve Codex JSONL streaming and prevent stale turn history from being
  resumed as a new prompt.

### 0.1.2 - 2026-08-11

- keep high-volume runtime stream deltas transient while retaining durable
  lifecycle boundaries;
- document Agent and Channel updates in the README.

### 0.1.3 - 2026-08-12

- publish installable distributions to PyPI through the manually dispatched,
  OIDC-backed `Release` workflow;
- derive the CLI version from installed package metadata and smoke-test both
  wheel and source distributions.

### 0.1.4 - 2026-08-12

- remove runtime-event persistence and migrate existing SQLite databases to
  reclaim the unused space;
- make release version commits GitHub-signed and independently verify tag
  provenance before publication finalization.

## Existing workflow insertion point

For a new release only, extend the existing `prepare` job without changing its
surrounding state transitions:

1. validate the requested stable version and current release state;
2. run the existing project verification;
3. update `pyproject.toml` and `uv.lock` with `uv version`;
4. call GitHub's generate-notes API with the new tag name, current `main` HEAD as
   the target commit, and the latest annotated release tag as the previous tag;
5. prepend the new version heading and returned Markdown body to `CHANGELOG.md`;
6. create the existing GitHub-verified release commit, now containing exactly
   `CHANGELOG.md`, `pyproject.toml`, and `uv.lock`;
7. continue the existing build, smoke-test, PyPI publish, annotated-tag
   verification, and tag push steps unchanged.

The generate-notes call and file update live inside the existing workflow. They
do not introduce a new workflow or an external release-preparation process.

## Retry contract

The existing same-version retry remains the only retry path:

- the release commit must still be the current `main` HEAD with subject
  `Release vX.Y.Z`;
- its exact changed-path set becomes `CHANGELOG.md`, `pyproject.toml`, and
  `uv.lock`;
- the target changelog heading must occur exactly once;
- retry reuses the committed changelog and does not call generate-notes or
  insert the version section again;
- the existing tag-object and PyPI retry checks remain unchanged.

## Tasks

### Task 1: Add the historical changelog

- Create `CHANGELOG.md` with curated released sections for `0.1.0` through
  `0.1.4`.
- Verify every entry against the exact annotated tag ranges.
- Validate every heading date against its annotated tag.
- Stop for review.

### Task 2: Add changelog generation to the existing release workflow

- Add the generate-notes call and deterministic insertion immediately before
  the existing verified release commit.
- Extend the new-release and retry changed-path checks to include
  `CHANGELOG.md`.
- Extend the GraphQL commit additions to include `CHANGELOG.md`.
- Verify the generated heading uniqueness and preserve the existing workflow's
  build, publish, and annotated-tag state machine.
- Run actionlint, embedded shell/JavaScript syntax checks, an isolated
  no-side-effect workflow simulation, and the repository validation suite.
- Stop for review before any commit, push, workflow dispatch, publication, or
  tag creation.

## Risks and controls

- **Historical overclaiming:** use annotated tag diffs as the source of truth
  and keep bullets at product-contract level.
- **Missing direct commits in generated notes:** accept GitHub generate-notes as
  the selected source for future sections; do not silently supplement it from
  commit subjects or another generator.
- **Duplicate section on retry:** generate only for a new version, then require
  exactly one committed version heading on retry.
- **Partial release commit:** require the exact three-file changed-path set and
  include all three files in the existing atomic GraphQL commit.
- **Workflow expansion:** keep manual dispatch, release commit, build, PyPI
  publication, annotated tag, and final tag push semantics unchanged.
