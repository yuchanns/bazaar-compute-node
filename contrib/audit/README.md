# bazaar-contrib-audit

Publish events through the shared `AUDIT_EVENT` contract. Sanitize business metadata before publishing.

```python
from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent

await ctx.announce(
    AUDIT_EVENT,
    AuditEvent.capture(
        event_name="agent.work.completed",
        metadata={"result": "saved"},
    ),
)
```

`capture()` records the contrib name, instance ID, module, file, line, function,
and occurrence time automatically. Outside contrib execution, the module identifies
the source and the instance ID is `None`.
Use `stacklevel=2` in a wrapper to record the wrapper's caller. The event preserves
its source and time through asynchronous delivery and JSON serialization.

Subscribe to events from a contrib:

```python
from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_core import Context, Contrib


async def report(ctx: Context, event: AuditEvent):
    print(event.event_name, event.source.id, event.metadata)


reporter = Contrib(listen={AUDIT_EVENT: report})
```

Enable `logging` to write audit events to stderr or files. Use Python's standard
`logging` module for ordinary diagnostic messages.
