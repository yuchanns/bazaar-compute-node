from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from time import monotonic, time
from typing import Annotated, Literal, Self
from uuid import uuid4

import aiohttp
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

Id = Annotated[str, Field(min_length=1)]


class Member(BaseModel):
    type: Literal[0, 1]
    userid: Id | None = None
    aibotid: Id | None = Field(
        default=None, validation_alias=AliasChoices("aibotid", "botid")
    )

    @model_validator(mode="after")
    def identity_matches_type(self) -> Self:
        if (self.type == 0 and self.userid is None) or (
            self.type == 1 and self.aibotid is None
        ):
            raise ValueError("member identity must match its type")
        return self

    @property
    def id(self) -> str:
        if self.type == 0:
            assert self.userid is not None
            return self.userid
        assert self.aibotid is not None
        return self.aibotid


class Actor(BaseModel):
    userid: Id | None = None
    aibotid: Id | None = None

    @model_validator(mode="after")
    def has_one_identity(self) -> Self:
        if (self.userid is None) == (self.aibotid is None):
            raise ValueError("event actor requires one identity")
        return self


class AtEvent(BaseModel):
    post_id: Id
    reply_id: Id | None = None


class PostEvent(AtEvent):
    type: Literal[1, 2, 3]


class ReplyEvent(BaseModel):
    post_id: Id
    reply_id: Id
    type: Literal[1, 2, 3]


class MembershipEvent(BaseModel):
    change_type: Literal[1, 2]


class Event(BaseModel):
    eventtype: str
    topic_at_bot: AtEvent | None = None
    topic_event: PostEvent | None = None
    topic_reply_event: ReplyEvent | None = None
    topic_bot_join_leave: MembershipEvent | None = None

    @model_validator(mode="after")
    def has_event_body(self) -> Self:
        if (
            self.eventtype
            in {
                "topic_at_bot",
                "topic_event",
                "topic_reply_event",
                "topic_bot_join_leave",
            }
            and getattr(self, self.eventtype) is None
        ):
            raise ValueError("topic event requires its event body")
        return self


class TopicEvent(BaseModel):
    msgid: Id
    create_time: int
    aibotid: Id
    topic_id: Id
    actor: Actor = Field(alias="from")
    msgtype: Literal["event"]
    event: Event


class Download(BaseModel):
    download_url: SecretStr
    cookie_name: str = ""
    cookie_value: SecretStr = SecretStr("")


class InlineFile(Download):
    placeholder: Id
    file_name: Id


class Mention(BaseModel):
    placeholder: Id
    member: Member


class Content(BaseModel):
    creator: Member = Field(validation_alias=AliasChoices("creator", "creator_info"))
    create_time: int
    content: str = ""
    is_recalled: bool = False
    is_modified: bool = False
    modify_time: int = 0
    inline_file_list: tuple[InlineFile, ...] = ()
    inline_mention_list: tuple[Mention, ...] = ()
    image_list: tuple[Download, ...] = ()


class Post(Content):
    post_id: Id


class Reply(Content):
    reply_id: Id
    parent_reply_id: Id | None = None


class ApiResponse(BaseModel):
    errcode: int
    errmsg: str = ""


class TokenResponse(ApiResponse):
    access_token: SecretStr


class Page(ApiResponse):
    has_more: bool = False
    next_cursor: str = ""

    @model_validator(mode="after")
    def continued_page_has_cursor(self) -> Self:
        if self.has_more and not self.next_cursor:
            raise ValueError("continued page requires a cursor")
        return self


class Posts(Page):
    post_list: tuple[Post, ...]


class Replies(Page):
    reply_list: tuple[Reply, ...]


class Topic(BaseModel):
    topic_id: Id
    topic_name: str


class Topics(Page):
    topic_list: tuple[Topic, ...]


class Members(Page):
    member_list: tuple[Member, ...]


class SentPost(ApiResponse):
    post_id: Id


class SentReply(ApiResponse):
    reply_id: Id


class UploadStarted(ApiResponse):
    upload_id: Id


class UploadFinished(ApiResponse):
    media_id: Id


class TopicApiError(Exception):
    """A sanitized provider failure; URLs, tokens and response bodies stay private."""

    def __init__(self, operation: str, code: int | str, *, unknown: bool = False):
        self.operation = operation
        self.code = code
        self.unknown = unknown
        super().__init__(f"WeCom {operation} failed ({code})")

    @property
    def transient(self) -> bool:
        return self.code in {
            "connection_failed",
            "transport_failed",
            "http_408",
            "http_429",
        } or (isinstance(self.code, str) and self.code.startswith("http_5"))


class TopicClient:
    def __init__(self, bot_id: str, secret: str) -> None:
        self._bot_id = bot_id
        self._secret = secret
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    def start(self) -> None:
        self._closed = False

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise TopicApiError("request", "client_closed")
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30, connect=10),
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._session

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _post[ResponseT: ApiResponse](
        self,
        operation: str,
        body: Mapping[str, object],
        model: type[ResponseT],
        *,
        token: str | None = None,
        visible: bool = False,
    ) -> ResponseT:
        try:
            async with self.session.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/aibot/{operation}",
                params={"access_token": token} if token is not None else None,
                json=body,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise TopicApiError(
                        operation,
                        f"http_{response.status}",
                        unknown=visible and response.status >= 500,
                    )
                body = await response.json(content_type=None)
            result = ApiResponse.model_validate(body)
            if result.errcode:
                raise TopicApiError(operation, result.errcode)
            return model.model_validate(body)
        except aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError:
            raise TopicApiError(operation, "connection_failed") from None
        except aiohttp.ClientError, TimeoutError, OSError:
            raise TopicApiError(
                operation, "transport_failed", unknown=visible
            ) from None
        except ValidationError, json.JSONDecodeError, UnicodeError:
            raise TopicApiError(
                operation, "invalid_response", unknown=visible
            ) from None

    async def call[ResponseT: ApiResponse](
        self,
        operation: str,
        body: Mapping[str, object],
        model: type[ResponseT],
        *,
        visible: bool = False,
    ) -> ResponseT:
        async with self._token_lock:
            if self._token is None or monotonic() >= self._expires_at:
                timestamp = int(time())
                nonce = uuid4().hex
                signature = hashlib.sha256(
                    f"{self._secret}{self._bot_id}{timestamp}{nonce}".encode()
                ).hexdigest()
                result = await self._post(
                    "get_token",
                    {
                        "bot_id": self._bot_id,
                        "time": timestamp,
                        "nonce": nonce,
                        "signature": signature,
                    },
                    TokenResponse,
                )
                self._token = result.access_token.get_secret_value()
                self._expires_at = monotonic() + 86_340
        return await self._post(
            operation, body, model, token=self._token, visible=visible
        )

    async def post(self, topic_id: str, post_id: str) -> Post:
        result = await self.call(
            "topic/get_post_list",
            {"topic_id": topic_id, "type": 0, "post_id_list": [post_id]},
            Posts,
        )
        for post in result.post_list:
            if post.post_id == post_id:
                return post
        raise TopicApiError("topic/get_post_list", "post_unavailable")

    async def reply(self, topic_id: str, post_id: str, reply_id: str) -> Reply:
        result = await self.call(
            "topic/get_reply_list",
            {
                "topic_id": topic_id,
                "post_id": post_id,
                "type": 0,
                "reply_id_list": [reply_id],
            },
            Replies,
        )
        for reply in result.reply_list:
            if reply.reply_id == reply_id:
                return reply
        raise TopicApiError("topic/get_reply_list", "reply_unavailable")

    async def members(self, topic_id: str) -> tuple[Member, ...]:
        members: list[Member] = []
        cursor = ""
        while True:
            page = await self.call(
                "topic/get_topic_member_list",
                {"topic_id": topic_id, "cursor": cursor, "limit": 200},
                Members,
            )
            members.extend(page.member_list)
            if not page.has_more:
                return tuple(members)
            if page.next_cursor == cursor:
                raise TopicApiError("topic/get_topic_member_list", "repeated_cursor")
            cursor = page.next_cursor
