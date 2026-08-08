# Task 0: Async Application Bootstrap

## Scope

Convert the packaged CLI entrypoint to an async application boundary without adding Channel,
runtime, SQLite, or IPC behavior.

## Implementation

- Keep argument parsing, help, and version handling in the CLI layer.
- Add one async application entrypoint for the normal command path.
- Keep the synchronous console entrypoint as a thin wrapper around one `asyncio.run(...)` call.
- Add direct async, synchronous wrapper, and real-process CLI coverage.
- Add the development test and lint tools to the uv dependency group.

## Acceptance

- `bcn` and `bcn --help` keep the existing command behavior.
- The async entrypoint can run under a controlled asyncio event loop.
- The packaged module entrypoint works in a real subprocess.
- No provider, storage, or transport implementation is introduced.
