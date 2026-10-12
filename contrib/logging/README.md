# bazaar-contrib-logging

Enable the shared audit logger in a node configuration:

```toml
[[contribs]]
contrib = "logging"

[contribs.config]
level = "info"
path = "/path/to/node.jsonl"
retention_days = 7
max_size = "20 MiB"

# Optional locations for selected levels.
[contribs.config.paths]
info = "/path/to/info/node.jsonl"
error = "/path/to/error/node.jsonl"
```

Omit `path` to write to stderr. The default format is text for stderr and JSON
lines for files. Set `format = "text"` or `format = "json"` to choose the format
for all destinations. Levels without a `paths` override use `path` or stderr;
critical records use the error destination.

Use Python logging for ordinary messages:

```python
import logging

logger = logging.getLogger(__name__)
logger.info("Task completed")
```

Use `logger.exception()` inside an exception handler to include the traceback.
Enable logging before publishing contribs to capture their startup messages.
Accepted records finish writing during normal shutdown.

Publish structured events with `AuditEvent.capture()` and `ctx.announce()` from
`bazaar-contrib-audit`. Contribs that need a direct asynchronous output service
can accept `LOG_OUTPUT` and call `append(event)`.

Text uses the event's occurrence time in ISO 8601 with milliseconds and the local
timezone, a five-character level (`WARN` for warnings), the publishing contrib
or Python module, and the caller's file and line:

```text
[2026-10-13T00:23:09.123+08:00][INFO ][bazaar_compute_node][__init__.py:47] node.started
```

JSON audit records retain the event fields, source, and readable `timestamp`.
Ordinary JSON records include `message`, `logger`, `source`, and an
`exception` field when present. Source information includes the concrete instance ID and is captured automatically.

Files rotate at local midnight or before the next write after reaching
`max_size`. A complete log line can exceed the threshold. Archives use a
`YYYY-MM-DD` suffix and numbered segments within a day. `retention_days` includes
today; expired archives are removed at startup and rollover. Defaults are seven
days and 20 MiB per file.
