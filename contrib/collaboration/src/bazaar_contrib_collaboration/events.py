"""Business events emitted after a durable admission decision."""

from dataclasses import dataclass

from bazaar_contrib_messages import Agent, Thread
from bazaar_contrib_messages.states import Review
from bazaar_core import EventKey


@dataclass(frozen=True, slots=True)
class ContactReviewed:
    agent: Agent
    thread: Thread
    review: Review


CONTACT_REVIEWED = EventKey[ContactReviewed]("collaboration.contact.reviewed")
