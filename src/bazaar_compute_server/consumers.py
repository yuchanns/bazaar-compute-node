"""Who in the server wants to hear of which events, as nodes report them."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

from .protocol import Event
from .storage import Computer

# told of one event from one computer, once it is on record
type Consumer = Callable[[Computer, Event], None]


class Consumers:
    def __init__(self) -> None:
        self._by_name: defaultdict[str, list[Consumer]] = defaultdict(list)

    def on(self, event_name: str, consumer: Consumer) -> None:
        """Have the consumer told of every event of this name."""

        self._by_name[event_name].append(consumer)

    def consume(self, computer: Computer, events: Sequence[Event]) -> None:
        for event in events:
            for consumer in self._by_name.get(event.event_name, ()):
                consumer(computer, event)


__all__ = ["Consumer", "Consumers"]
