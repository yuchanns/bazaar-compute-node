# Shared Python Service Supervisor

## Goal

Run Linux, macOS and Windows services through one persistent Python supervisor.
Each child launch copies the installed `bcn` entry point to `bcn.running` (or
`bcn.running.exe` on Windows), runs the copy with the registered configuration,
and waits for it to exit before repeating. BCN installs its own upgrades and
then exits normally; the same supervision loop starts the updated entry point.
The supervisor is a stable bootstrap layer intended to remain installed across future
BCN releases. Its interface stays limited to the entry-point and configuration
paths; version decisions and application behavior stay inside BCN.

## Design and references

- Ship `resources/system_service/supervisor.py` as a standard-library-only resource.
  Service installation copies it to `BCN_HOME/.bcn/supervisor.py` with the
  existing managed-file writer. Launch it with the absolute base interpreter
  (`sys._base_executable`), outside the replaceable BCN tool environment.
- Store the running entry point beside the installed supervisor. Copy on every child
  launch, inherit the platform's existing environment and log streams, close
  child stdin, and wait for termination. A one-second retry interval prevents
  repeated startup failures from spinning. On service termination, stop the
  supervised child and end the loop.
- Preserve systemd environment-file loading, launchd's shell environment wrapper,
  and Windows' hidden PowerShell launcher and stream forwarding. Replace their
  execution target with the base interpreter, installed supervisor, source entry point
  and configuration. Windows stop also finds the installed supervisor process by its
  script and configuration paths, so ending the task cannot leave supervision
  running outside the task.
- `UpgradeService` still owns uv installation and durable follow-up creation;
  install the pinned release into the existing tool environment without `--force`,
  preserving the Python interpreter that Windows still has open.
  its final callback requests ordinary node shutdown. Remove the Windows upgrade
  exclusion, the restart flag, the special exit-code property and `core/restart.py`.
- [uv's tool documentation](https://docs.astral.sh/uv/concepts/tools/#tool-executables)
  says tool entry points are copied on Windows and symlinked on Unix. Copy the
  entry-point contents into the service directory; do not create a second symlink.
- [uv's launcher](https://github.com/astral-sh/uv/blob/main/crates/uv-trampoline/src/bounce.rs)
  starts Python from the embedded interpreter path and forwards its exit status.
  The running entry-point copy therefore retains the installed tool environment.
  [uv installation](https://github.com/astral-sh/uv/blob/main/crates/uv/src/commands/tool/install.rs)
  recreates that environment for `tool install --force`. The isolated Windows CI
  confirmed that removing the running environment fails with access denied at
  `Scripts`; ordinary `tool install` reuses the existing environment. Explicit
  local directory requirements are rebuilt and reinstalled by uv, so use a real
  local reinstall while the service is running to verify package and entry-point
  replacement on every platform.
- Verify recopying through execution: install BCN into another isolated uv tool
  environment and replace the registered source entry point with its real launcher.
  After normal or forced child exit, the same supervisor must start a healthy node
  whose Python process uses that new environment. For an in-place reinstall, verify
  that the same supervisor starts a healthy node in the existing environment.

## Task 1: Integrate the supervisor and verify the service lifecycle

1. Add and install the shared resource; route all native service launchers through
   it and include its installed file in service uninstall cleanup.
2. Make upgrade completion shut down BCN normally on all three platforms.
3. Extend real native-service tests under `pytest.mark.system`: verify a normal
   exit and forced child termination both cause another launch, the supervisor
   survives child restarts, the running entry point is recopied, and an isolated
   uv reinstall can update the tool while its running copy is active. Preserve
   existing registered-path and environment tests.
4. Run ordinary local tests, Ruff, the root Pyright/LSP check and package build.
   Commit with an English message and signature, push the feature branch, and
   open a draft pull request to trigger the existing three-platform workflow.
   Run native service tests only in isolated CI; inspect every platform's result
   and resolve failures before reporting completion.
5. Stop for review once this integration task and its CI checks are complete.
