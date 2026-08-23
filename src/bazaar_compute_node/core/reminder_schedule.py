from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_MILLISECONDS_PER_SECOND = 1_000
_MILLISECONDS_PER_MINUTE = 60 * _MILLISECONDS_PER_SECOND
_MILLISECONDS_PER_HOUR = 60 * _MILLISECONDS_PER_MINUTE
_MILLISECONDS_PER_DAY = 24 * _MILLISECONDS_PER_HOUR
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DURATION_PATTERN = re.compile(r"(?P<count>[0-9]+)(?P<unit>[mhd])\Z")
_EVERY_PATTERN = re.compile(r"every:(?P<count>[0-9]+)(?P<unit>[mhd])\Z")
_DAILY_PATTERN = re.compile(r"daily@(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})\Z")
_WEEKLY_PATTERN = re.compile(
    r"weekly:(?P<weekdays>[a-z]+(?:,[a-z]+)*)@"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})\Z"
)
_WEEKDAY_NUMBERS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_WEEKDAY_NAMES = tuple(_WEEKDAY_NUMBERS)


class RecurrenceKind(StrEnum):
    EVERY = "every"
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class ReminderDuration:
    milliseconds: int
    canonical: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical, str) or not self.canonical:
            raise ValueError("canonical must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ReminderRecurrence:
    kind: RecurrenceKind
    canonical: str
    interval_ms: int | None = None
    local_time: time | None = None
    weekdays: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RecurrenceKind):
            raise TypeError("kind must be a RecurrenceKind")
        if not isinstance(self.canonical, str) or not self.canonical:
            raise ValueError("canonical must be a non-empty string")
        if self.kind is RecurrenceKind.EVERY:
            if self.interval_ms is None:
                raise ValueError("an every recurrence requires interval_ms")
            if self.local_time is not None or self.weekdays:
                raise ValueError("an every recurrence cannot contain calendar fields")
        elif self.kind is RecurrenceKind.DAILY:
            if self.local_time is None:
                raise ValueError("a daily recurrence requires local_time")
            if self.interval_ms is not None or self.weekdays:
                raise ValueError("a daily recurrence has invalid fields")
        else:
            if self.local_time is None or not self.weekdays:
                raise ValueError("a weekly recurrence requires time and weekdays")
            if self.interval_ms is not None:
                raise ValueError("a weekly recurrence cannot contain interval_ms")
            if any(weekday < 0 or weekday > 6 for weekday in self.weekdays):
                raise ValueError("weekly recurrence weekday is invalid")
            if tuple(sorted(set(self.weekdays))) != self.weekdays:
                raise ValueError("weekly recurrence weekdays must be unique and sorted")

    def first_after(self, timestamp_ms: int, timezone_name: str) -> int:
        timezone = _load_timezone(timezone_name)
        if self.kind is RecurrenceKind.EVERY:
            if self.interval_ms is None:
                raise AssertionError("an every recurrence has no interval")
            return timestamp_ms + self.interval_ms
        return _next_calendar_slot(self, timestamp_ms, timezone)

    def next_after(self, scheduled_for_ms: int, timezone_name: str) -> int:
        return self.first_after(scheduled_for_ms, timezone_name)


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    next_fire_at_ms: int
    repeat_rule: str | None
    timezone: str

    def __post_init__(self) -> None:
        if self.repeat_rule is not None and not self.repeat_rule:
            raise ValueError("repeat_rule must be non-empty when provided")
        if self.repeat_rule is not None:
            recurrence = parse_repeat_rule(self.repeat_rule)
            if recurrence.canonical != self.repeat_rule:
                raise ValueError("repeat_rule must use its canonical form")
        if canonical_timezone(self.timezone) != self.timezone:
            raise ValueError("timezone must use its canonical form")


def parse_duration(value: object) -> ReminderDuration:
    if not isinstance(value, str):
        raise TypeError("duration must be a string")
    match = _DURATION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("duration must match <positive-int><m|h|d>")
    count = int(match.group("count"))
    if count <= 0:
        raise ValueError("duration count must be positive")
    unit = match.group("unit")
    unit_ms = {
        "m": _MILLISECONDS_PER_MINUTE,
        "h": _MILLISECONDS_PER_HOUR,
        "d": _MILLISECONDS_PER_DAY,
    }[unit]
    return ReminderDuration(count * unit_ms, f"{count}{unit}")


def parse_repeat_rule(value: object) -> ReminderRecurrence:
    if not isinstance(value, str):
        raise TypeError("repeat rule must be a string")
    every = _EVERY_PATTERN.fullmatch(value)
    if every is not None:
        count = int(every.group("count"))
        if count <= 0:
            raise ValueError("repeat interval must be positive")
        unit = every.group("unit")
        unit_ms = {
            "m": _MILLISECONDS_PER_MINUTE,
            "h": _MILLISECONDS_PER_HOUR,
            "d": _MILLISECONDS_PER_DAY,
        }[unit]
        return ReminderRecurrence(
            kind=RecurrenceKind.EVERY,
            canonical=f"every:{count}{unit}",
            interval_ms=count * unit_ms,
        )
    daily = _DAILY_PATTERN.fullmatch(value)
    if daily is not None:
        local_time = _parse_local_time(daily.group("hour"), daily.group("minute"))
        return ReminderRecurrence(
            kind=RecurrenceKind.DAILY,
            canonical=f"daily@{local_time:%H:%M}",
            local_time=local_time,
        )
    weekly = _WEEKLY_PATTERN.fullmatch(value)
    if weekly is not None:
        local_time = _parse_local_time(weekly.group("hour"), weekly.group("minute"))
        names = weekly.group("weekdays").split(",")
        if any(name not in _WEEKDAY_NUMBERS for name in names):
            raise ValueError("weekly repeat rule contains an invalid weekday")
        weekdays = tuple(sorted({_WEEKDAY_NUMBERS[name] for name in names}))
        if not weekdays:
            raise ValueError("weekly repeat rule requires at least one weekday")
        canonical_names = ",".join(_WEEKDAY_NAMES[weekday] for weekday in weekdays)
        return ReminderRecurrence(
            kind=RecurrenceKind.WEEKLY,
            canonical=f"weekly:{canonical_names}@{local_time:%H:%M}",
            local_time=local_time,
            weekdays=weekdays,
        )
    raise ValueError("repeat rule is invalid")


def canonical_timezone(value: str | None) -> str:
    timezone_name = "UTC" if value is None else value
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("timezone must be a non-empty IANA name")
    return _load_timezone(timezone_name).key


def parse_fire_at(value: object) -> int:
    if not isinstance(value, str):
        raise TypeError("fire_at must be a string")
    if "T" not in value:
        raise ValueError("fire_at must be an ISO-8601 timestamp with an offset")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(
            "fire_at must be an ISO-8601 timestamp with an offset"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("fire_at must include an explicit UTC offset")
    timestamp_ms = _datetime_to_ms(parsed.astimezone(UTC))
    return timestamp_ms


def format_utc_timestamp(timestamp_ms: int) -> str:
    value = _EPOCH + timedelta(milliseconds=timestamp_ms)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_schedule(
    *,
    evaluated_at_ms: int,
    delay_seconds: int | None = None,
    fire_at: str | None = None,
    repeat_rule: str | None = None,
    timezone: str | None = None,
) -> ReminderSchedule:
    if delay_seconds is not None and fire_at is not None:
        raise ValueError("delay_seconds and fire_at are mutually exclusive")
    if delay_seconds is None and fire_at is None and repeat_rule is None:
        raise ValueError("a reminder requires a fire time or repeat rule")
    timezone_name = canonical_timezone(timezone)
    recurrence = parse_repeat_rule(repeat_rule) if repeat_rule is not None else None
    if delay_seconds is not None:
        next_fire_at_ms = evaluated_at_ms + delay_seconds * _MILLISECONDS_PER_SECOND
    elif fire_at is not None:
        next_fire_at_ms = parse_fire_at(fire_at)
    else:
        if recurrence is None:
            raise AssertionError("repeat-only schedule has no recurrence")
        next_fire_at_ms = recurrence.first_after(evaluated_at_ms, timezone_name)
    if next_fire_at_ms <= evaluated_at_ms:
        raise ValueError("reminder fire time must be in the future")
    return ReminderSchedule(
        next_fire_at_ms=next_fire_at_ms,
        repeat_rule=recurrence.canonical if recurrence is not None else None,
        timezone=timezone_name,
    )


def next_recurrence_ms(
    *,
    scheduled_for_ms: int,
    repeat_rule: str,
    timezone: str,
) -> int:
    recurrence = parse_repeat_rule(repeat_rule)
    timezone_name = canonical_timezone(timezone)
    return recurrence.next_after(scheduled_for_ms, timezone_name)


def _next_calendar_slot(
    recurrence: ReminderRecurrence,
    timestamp_ms: int,
    timezone: ZoneInfo,
) -> int:
    if recurrence.local_time is None:
        raise AssertionError("calendar recurrence has no local time")
    current = (_EPOCH + timedelta(milliseconds=timestamp_ms)).astimezone(timezone)
    local_date = current.date()
    for offset in range(8):
        candidate_date = local_date + timedelta(days=offset)
        if (
            recurrence.kind is RecurrenceKind.WEEKLY
            and candidate_date.weekday() not in recurrence.weekdays
        ):
            continue
        naive = datetime.combine(candidate_date, recurrence.local_time)
        candidate = _resolve_local_datetime(naive, timezone)
        candidate_ms = _datetime_to_ms(candidate.astimezone(UTC))
        if candidate_ms > timestamp_ms:
            return candidate_ms
    raise RuntimeError("calendar recurrence could not find a next slot")


def _resolve_local_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    first = value.replace(tzinfo=timezone, fold=0)
    if _roundtrips_to(first, value, timezone):
        return first
    probe = value
    for _ in range(24 * 60):
        probe += timedelta(minutes=1)
        candidate = probe.replace(tzinfo=timezone, fold=0)
        if _roundtrips_to(candidate, probe, timezone):
            return candidate
    raise ValueError("timezone transition gap exceeds one day")


def _roundtrips_to(candidate: datetime, naive: datetime, timezone: ZoneInfo) -> bool:
    roundtrip = candidate.astimezone(UTC).astimezone(timezone)
    return roundtrip.replace(tzinfo=None) == naive


def _parse_local_time(hour: str, minute: str) -> time:
    hour_value = int(hour)
    minute_value = int(minute)
    if hour_value > 23 or minute_value > 59:
        raise ValueError("calendar repeat time is invalid")
    return time(hour_value, minute_value)


def _load_timezone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value:
        raise ValueError("timezone must be a non-empty IANA name")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"timezone is invalid: {value}") from error


def _datetime_to_ms(value: datetime) -> int:
    delta = value.astimezone(UTC) - _EPOCH
    return (
        delta.days * _MILLISECONDS_PER_DAY
        + delta.seconds * _MILLISECONDS_PER_SECOND
        + delta.microseconds // 1_000
    )
