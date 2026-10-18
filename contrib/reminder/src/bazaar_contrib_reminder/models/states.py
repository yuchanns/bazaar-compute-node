from enum import StrEnum


class ReminderState(StrEnum):
    SCHEDULED = "scheduled"
    FIRED = "fired"
    CANCELED = "canceled"
