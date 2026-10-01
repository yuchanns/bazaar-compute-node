"""Page query boundaries for the node's message search."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = ""
    target: Annotated[str, Field(min_length=1)] | None = None
    sender: Annotated[str, Field(min_length=1)] | None = None
    after_ms: int | None = None
    before_ms: int | None = None
    sort: Literal["time", "relevance"] = "time"
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    offset: NonNegativeInt = 0
    version: NonNegativeInt = 0
    review: Literal["pending", "approved", "denied"] | None = None

    @model_validator(mode="after")
    def filters(self) -> Self:
        self.query = " ".join(self.query.split()[:5])
        if self.sender is not None:
            self.sender = self.sender.strip()
            if not self.sender or self.sender == "@":
                raise ValueError("sender must be a handle, id, or self")
        if (
            self.after_ms is not None
            and self.before_ms is not None
            and self.after_ms > self.before_ms
        ):
            raise ValueError("after must be at or before before")
        if (
            not any((self.query, self.target, self.sender))
            and self.after_ms is None
            and self.before_ms is None
        ):
            raise ValueError("search requires a query or filter")
        return self


class SearchOptionsQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: Annotated[int, Field(ge=1, le=50)] = 20
    offset: NonNegativeInt = 0


__all__ = ["SearchOptionsQuery", "SearchQuery"]
