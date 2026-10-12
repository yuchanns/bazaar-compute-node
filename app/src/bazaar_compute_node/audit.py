"""Publish node-owned lifecycle changes through the public audit contract."""

from bazaar_core import CONTRIB_CHANGED, Contrib
lazy from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent, LogLevel
lazy from bazaar_core import Blocked, Context, ContribChange, Failed, Stopping


async def _changed(ctx: Context, change: ContribChange) -> None:
    for record in change.snapshots:
        await ctx.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name=f"contrib.{type(record.state).__name__.lower()}",
                level=LogLevel.ERROR
                if isinstance(record.state, Failed)
                else LogLevel.WARNING
                if isinstance(record.state, Blocked)
                else LogLevel.INFO,
                metadata={
                    "id": str(record.id),
                    "contrib": record.name,
                    "location": record.location,
                    "parent": str(record.parent) if record.parent is not None else None,
                    "phase": record.state.phase
                    if isinstance(record.state, Stopping)
                    else None,
                },
            ),
        )
    for id in change.removed:
        await ctx.announce(
            AUDIT_EVENT,
            AuditEvent.capture(
                event_name="contrib.removed",
                metadata={"id": str(id)},
            ),
        )


audit = Contrib(listen={CONTRIB_CHANGED: _changed})
