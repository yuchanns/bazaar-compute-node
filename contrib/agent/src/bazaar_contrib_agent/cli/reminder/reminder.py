from __future__ import annotations

import click

from ._client import ReminderClient, run
from ._format import (
    echo,
    serialize_reminder_cancel,
    serialize_reminder_list,
    serialize_reminder_schedule,
    serialize_reminder_snooze,
    serialize_reminder_update,
)


@click.group(help="Reminder operations")
@click.pass_context
def reminder(ctx: click.Context) -> None:
    ctx.obj = ReminderClient(ctx.obj.request)


@reminder.command(
    short_help="Schedule a one-time or recurring Reminder.",
    help=(
        "Schedule a persistent Reminder anchored to an inbound message in the "
        "current session. --title and --message-id are required. Provide at least "
        "one of --delay-seconds, --fire-at, or --repeat; --delay-seconds and "
        "--fire-at cannot be combined."
    ),
    epilog=(
        "Recurrence rules: every:15m | every:2h | every:1d | daily@09:00 | "
        "weekly:mon,fri@09:00. Use --tz with calendar recurrences when a specific "
        "IANA timezone is required."
    ),
)
@click.option(
    "--title",
    metavar="<t>",
    help="Required short description of what the Reminder is about.",
)
@click.option(
    "--delay-seconds",
    type=int,
    metavar="<n>",
    help="Fire this many seconds from command evaluation time.",
)
@click.option(
    "--fire-at",
    metavar="<iso>",
    help="Absolute ISO-8601 timestamp for the first fire.",
)
@click.option(
    "--repeat",
    metavar="<rule>",
    help="Optional recurrence rule; see supported grammar below.",
)
@click.option(
    "--tz",
    metavar="<iana>",
    help="IANA timezone for calendar recurrence, for example Asia/Shanghai.",
)
@click.option(
    "--message-id",
    metavar="<uuid>",
    help="Required full uuid for the local inbound message used as anchor.",
)
@click.pass_obj
@run
async def schedule(
    client: ReminderClient,
    title: str | None,
    delay_seconds: int | None,
    fire_at: str | None,
    repeat: str | None,
    tz: str | None,
    message_id: str | None,
) -> None:
    echo(
        serialize_reminder_schedule(
            await client.request(
                "reminder",
                "schedule",
                {
                    "title": title,
                    "delay_seconds": delay_seconds,
                    "fire_at": fire_at,
                    "repeat_rule": repeat,
                    "timezone": tz,
                    "message_id": message_id,
                },
            )
        )
    )


@reminder.command(
    "list", help="List your own reminders (defaults to scheduled and fired)"
)
@click.option("--all", "all_", is_flag=True, help="Include canceled reminders")
@click.option(
    "--status",
    metavar="<scheduled,fired,canceled>",
    help=(
        "Comma-separated statuses (scheduled,fired,canceled). Default: scheduled,fired"
    ),
)
@click.pass_obj
@run
async def list_reminders(
    client: ReminderClient, all_: bool, status: str | None
) -> None:
    echo(
        serialize_reminder_list(
            await client.request("reminder", "list", {"all": all_, "status": status})
        )
    )


@reminder.command(help="Snooze a scheduled or fired reminder")
@click.option("--id", "reminder_id", metavar="<id>", help="Reminder id (full uuid)")
@click.option("--by", metavar="<duration>", help="Snooze duration, e.g. 30m, 2h, 1d")
@click.pass_obj
@run
async def snooze(
    client: ReminderClient, reminder_id: str | None, by: str | None
) -> None:
    echo(
        serialize_reminder_snooze(
            await client.request(
                "reminder", "snooze", {"reminder_id": reminder_id, "by": by}
            )
        )
    )


@reminder.command(help="Update one field on a scheduled reminder")
@click.option("--id", "reminder_id", metavar="<id>", help="Reminder id (full uuid)")
@click.option("--fire-at", metavar="<iso>", help="New absolute next fire time")
@click.option(
    "--in",
    "in_duration",
    metavar="<duration>",
    help="New relative next fire time, e.g. 30m, 2h",
)
@click.option(
    "--cadence",
    metavar="<rule>",
    help="New recurrence rule: every:15m | daily@09:00 | weekly:mon,fri@09:00",
)
@click.option("--title", metavar="<text>", help="New reminder title")
@click.pass_obj
@run
async def update(
    client: ReminderClient,
    reminder_id: str | None,
    fire_at: str | None,
    in_duration: str | None,
    cadence: str | None,
    title: str | None,
) -> None:
    echo(
        serialize_reminder_update(
            await client.request(
                "reminder",
                "update",
                {
                    "reminder_id": reminder_id,
                    "fire_at": fire_at,
                    "in": in_duration,
                    "cadence": cadence,
                    "title": title,
                },
            )
        )
    )


@reminder.command(help="Cancel a scheduled reminder by id (full uuid)")
@click.option("--id", "reminder_id", metavar="<id>", help="Reminder id (full uuid)")
@click.pass_obj
@run
async def cancel(client: ReminderClient, reminder_id: str | None) -> None:
    echo(
        serialize_reminder_cancel(
            await client.request("reminder", "cancel", {"reminder_id": reminder_id})
        )
    )


__all__ = ["reminder"]
