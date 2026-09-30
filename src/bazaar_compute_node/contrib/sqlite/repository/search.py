from __future__ import annotations

import re
from math import log
from typing import Literal, cast

from ....core.command import (
    MessageSearchHit,
    MessageSearchRequest,
    MessageSearchResult,
    MessageSearchTimeoutError,
)
from ....core.models import Review
from ....core.storage import InboxTargetResolutionError
from ..codec import message_from_row
from ..executor import SqliteQueryTimeoutError
from .base import RepositoryBase

_SEARCH_FROM = (
    "FROM messages AS message "
    "JOIN threads AS thread ON thread.agent_id = message.agent_id "
    "AND thread.id = message.thread_id "
    "JOIN channel_sessions AS channel ON channel.agent_id = message.agent_id "
    "AND channel.id = thread.channel_session_id "
)
_SEARCH_TIME = (
    "COALESCE(message.provider_time_ms, message.received_at_ms, message.created_at_ms)"
)


class SearchOperations(RepositoryBase):
    async def search_messages(
        self,
        request: MessageSearchRequest,
        *,
        thread_id: str | None = None,
        review: Review | None = Review.APPROVED,
    ) -> MessageSearchResult:
        query = request.query.strip()
        fragments = query.split()
        phrases = [
            '"' + fragment.replace('"', '""') + '"'
            for fragment in fragments
            if len(fragment) >= 3
        ]
        sort: Literal["time", "relevance"] = request.sort if phrases else "time"
        try:
            async with self._session.query_budget(5.0):
                predicates = [
                    "message.agent_id = /*agent_id*/?",
                    (
                        "(message.direction = 'inbound' OR "
                        "(message.direction = 'outbound' AND message.delivery_state IN ('queued', 'sent')))"
                    ),
                ]
                parameters: list[object] = []
                if thread_id is not None:
                    predicates.append("message.thread_id = ?")
                    parameters.append(thread_id)
                if request.raw_target is not None:
                    target = await self.resolve_inbox_target(request.raw_target)
                    if thread_id is not None and target.thread.id != thread_id:
                        raise InboxTargetResolutionError(
                            f"inbox target is not this conversation: {request.raw_target}"
                        )
                    if (
                        review is not None
                        and target.channel_session.review is not review
                    ):
                        raise InboxTargetResolutionError(
                            f"inbox target is not {review.value}: {request.raw_target}"
                        )
                    if thread_id is None:
                        predicates.append("message.thread_id = ?")
                        parameters.append(target.thread.id)
                if review is not None:
                    predicates.append("channel.review = ?")
                    parameters.append(review.value)
                if request.sender == "self":
                    predicates.append("message.direction = 'outbound'")
                elif request.sender is not None:
                    token = request.sender.removeprefix("@")
                    predicates.append(
                        "message.direction = 'inbound' AND "
                        "(lower(message.sender) = lower(?) OR "
                        "(message.sender IS NULL AND message.sender_id = ?))"
                    )
                    parameters.extend((token, token))
                if request.after_ms is not None:
                    predicates.append(f"{_SEARCH_TIME} >= ?")
                    parameters.append(request.after_ms)
                if request.before_ms is not None:
                    predicates.append(f"{_SEARCH_TIME} <= ?")
                    parameters.append(request.before_ms)
                frequencies = []
                weights: list[float] = []
                average = 1.0
                terms = list(
                    dict.fromkeys(
                        fragment for fragment in fragments if len(fragment) >= 3
                    )
                )
                if sort == "relevance":
                    statistics = await self.fetchone(
                        "SELECT COUNT(*) AS documents, AVG(length(message.body)) AS average, "
                        + ", ".join(
                            f"SUM(instr(lower(message.body), lower(?)) > 0) AS frequency_{index}"
                            for index in range(len(terms))
                        )
                        + f" {_SEARCH_FROM}WHERE {' AND '.join(predicates)}",
                        (*terms, *parameters),
                    )
                    assert statistics is not None
                    average = statistics["average"] or 1.0
                    weights = [
                        log(
                            1
                            + (
                                statistics["documents"]
                                - (statistics[f"frequency_{index}"] or 0)
                                + 0.5
                            )
                            / ((statistics[f"frequency_{index}"] or 0) + 0.5)
                        )
                        for index in range(len(terms))
                    ]
                    frequencies = [
                        f"(length(lower(message.body)) - length(replace(lower(message.body), lower(?), ''))) / (1.0 * length(?)) AS frequency_{index}"
                        for index in range(len(terms))
                    ]
                source = _SEARCH_FROM
                if phrases:
                    source += (
                        "JOIN message_search ON message_search.rowid = message.seq "
                    )
                    predicates.append("message_search MATCH ?")
                    parameters.append(" AND ".join(phrases))
                for fragment in fragments:
                    if len(fragment) < 3:
                        predicates.append("instr(lower(message.body), lower(?)) > 0")
                        parameters.append(fragment)
                select = (
                    f"SELECT message.*, {_SEARCH_TIME} AS search_at_ms"
                    + (", " + ", ".join(frequencies) if frequencies else "")
                    + f" {source}WHERE {' AND '.join(predicates)}"
                )
                order = ""
                if sort == "relevance":
                    parameters = [
                        value for term in terms for value in (term, term)
                    ] + parameters
                    select = (
                        f"WITH hits AS ({select}) SELECT *, "
                        + " + ".join(
                            f"? * frequency_{index} * 2.2 / (frequency_{index} + 1.2 * (0.25 + 0.75 * length(body) / ?))"
                            for index in range(len(terms))
                        )
                        + " AS search_rank FROM hits"
                    )
                    parameters.extend(
                        value for weight in weights for value in (weight, average)
                    )
                    order = "search_rank DESC, "
                rows = await self.fetchall(
                    f"{select} ORDER BY {order}search_at_ms DESC, seq DESC LIMIT ? OFFSET ?",
                    (*parameters, request.limit + 1, request.offset),
                )
                has_more = len(rows) > request.limit
                targets: dict[str, str] = {}
                hits: list[MessageSearchHit] = []
                for row in rows[: request.limit]:
                    message = message_from_row(row)
                    if message.target not in targets:
                        target = await self.resolve_inbox_target(message.target)
                        targets[message.target] = target.display_target
                    body = " ".join(message.body.split())
                    positions = [
                        match.start()
                        for fragment in fragments
                        if (
                            match := re.search(re.escape(fragment), body, re.IGNORECASE)
                        )
                    ]
                    start = max(0, min(positions, default=0) - 80)
                    end = min(len(body), start + 240 - int(start > 0))
                    if end < len(body):
                        end -= 1
                    snippet = (
                        ("…" if start else "")
                        + body[start:end]
                        + ("…" if end < len(body) else "")
                    )
                    hits.append(
                        MessageSearchHit(
                            message_id=message.message_id,
                            thread_id=message.thread_id,
                            target=targets[message.target],
                            canonical_target=message.target,
                            channel=cast(str, message.channel),
                            target_kind=message.target_kind,
                            direction=message.direction,
                            sender=message.sender,
                            sender_kind=message.sender_kind,
                            at_ms=cast(int, row["search_at_ms"]),
                            body=message.body,
                            snippet=snippet,
                        )
                    )
                return MessageSearchResult(
                    query=query,
                    sort=sort,
                    messages=tuple(hits),
                    offset=request.offset,
                    has_more=has_more,
                    next_offset=request.offset + len(hits) if has_more else None,
                )
        except SqliteQueryTimeoutError as error:
            hint = "Search exceeded its execution budget; narrow the keywords or time range"
            if thread_id is None and request.raw_target is None:
                hint += ", or specify a target"
            raise MessageSearchTimeoutError(hint) from error
