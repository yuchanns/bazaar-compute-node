from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from time import monotonic, time_ns
from unicodedata import category

import aiohttp

from ...core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelDeliveryReceipt,
    ChannelIdentity,
    ChannelSendRequest,
    IChannel,
)
from ...core.models import (
    ApprovalDecision,
    ApprovalResult,
    ChannelTargetKind,
    ChannelTargetPresentation,
    ContentDelta,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    InboundAttachment,
    Message,
    MessageDirection,
    RuntimeOutputEvent,
    SenderIdentity,
    SenderKind,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallInteraction,
    ToolCallPatchUpdated,
    ToolCallStarted,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
    UsageUpdated,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.timerwheel import TimerWheel
from ...core.utils.clock import remaining
from ...i18n import ENGLISH, create_translator
from .activity import LarkActivityProjector, LarkActivityRoute
from .api import LarkApi
from .attachments import (
    LarkMention,
    LarkResourceCache,
    LarkResourceDescriptor,
    project_lark_content,
)
from .identity import LarkBotIdentity, LarkThreadIdentity, parse_bot_info
from .outbound import send_outbound
from .transport import LarkTransport

_STOP = object()
_EVENT_TYPE = "im.message.receive_v1"
_CONTACT_CACHE_TTL_SECONDS = 24 * 60 * 60
_CONTACT_FAILURE_CACHE_TTL_SECONDS = 5 * 60
_CONTACT_CACHE_MAX_ENTRIES = 256
_CONTACT_TIMEOUT_SECONDS = 5.0
_CHAT_CACHE_TTL_SECONDS = 24 * 60 * 60
_CHAT_FAILURE_CACHE_TTL_SECONDS = 5 * 60
_CHAT_CACHE_MAX_ENTRIES = 256
_CHAT_TIMEOUT_SECONDS = 5.0
_PARENT_TIMEOUT_SECONDS = 10.0
_RESOURCE_TIMEOUT_SECONDS = 60.0
_LOGGER = logging.getLogger(__name__)


class LarkChannel(IChannel):
    def __init__(
        self,
        context: ChannelContext,
        *,
        app_id: str,
        app_secret: str,
        region: str,
        base_url: str,
        timer_wheel: TimerWheel,
    ) -> None:
        self._context = context
        self._app_id = app_id
        self._app_secret = app_secret
        self._region = region
        self._base_url = base_url
        self._timer_wheel = timer_wheel
        self._inbound: asyncio.Queue[Message | object] = asyncio.Queue()
        self._lifecycle_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._api: LarkApi | None = None
        self._transport: LarkTransport | None = None
        self._identity: LarkBotIdentity | None = None
        self._state = "stopped"
        self._stop_sent = False
        self._token_refresh_failures = 0
        self._connection_generation = 0
        self._last_message_disposition: str | None = None
        self._last_message_filter_reason: str | None = None
        self._contact_cache: OrderedDict[
            tuple[str, str, str], tuple[float, str | None]
        ] = OrderedDict()
        self._contact_inflight: dict[
            tuple[str, str, str], asyncio.Task[str | None]
        ] = {}
        self._contact_lock = asyncio.Lock()
        self._contact_lookup_requests = 0
        self._contact_lookup_failures = 0
        self._contact_cache_hits = 0
        self._chat_cache: OrderedDict[
            tuple[str, str, str], tuple[float, str | None]
        ] = OrderedDict()
        self._chat_inflight: dict[tuple[str, str, str], asyncio.Task[str | None]] = {}
        self._chat_lock = asyncio.Lock()
        self._chat_lookup_requests = 0
        self._chat_lookup_failures = 0
        self._chat_cache_hits = 0
        self._resource_cache = LarkResourceCache(context.attachments)
        self._resources_materialized = 0
        self._resource_failures = 0
        self._last_resource_disposition: str | None = None
        self._send_lock = asyncio.Lock()
        self._stream_routes: dict[str, str] = {}
        self._stream_route_threads: dict[str, bool] = {}
        self._activity_turns: dict[str, str] = {}
        self._terminal_activity_turns: set[tuple[str, str]] = set()
        self._degraded_activity_turns: set[tuple[str, str]] = set()
        self._translator = context.translator or create_translator(ENGLISH)
        self._activity = LarkActivityProjector(
            timer_wheel=timer_wheel,
            translator=self._translator,
            report_degraded=lambda session_id, turn_id: (
                self._degraded_activity_turns.add((session_id, turn_id))
                if self._activity_turns.get(session_id) == turn_id
                else None
            ),
        )

    @property
    def name(self) -> str:
        return "lark"

    @property
    def health(self) -> Mapping[str, object]:
        transport = self._transport
        transport_health = transport.health if transport is not None else {}
        state = self._state
        if transport is not None and state == "connected":
            state = str(transport_health.get("state", state))
        identity = self._identity
        return {
            "state": state,
            "region": self._region,
            "bot_open_id": identity.open_id if identity is not None else None,
            "bot_name": identity.name if identity is not None else None,
            "connection_generation": transport_health.get(
                "connection_generation", self._connection_generation
            ),
            "connected_at_ms": transport_health.get("connected_at_ms"),
            "last_event_at_ms": transport_health.get("last_event_at_ms"),
            "last_disconnect_kind": transport_health.get("last_disconnect_kind"),
            "events_received": transport_health.get("events_received", 0),
            "messages_queued": transport_health.get("messages_queued", 0),
            "messages_filtered": transport_health.get("messages_filtered", 0),
            "message_mapping_failures": transport_health.get(
                "message_mapping_failures", 0
            ),
            "last_message_disposition": self._last_message_disposition
            or transport_health.get("last_message_disposition"),
            "last_message_filter_reason": self._last_message_filter_reason
            or transport_health.get("last_message_filter_reason"),
            "contact_lookup_requests": self._contact_lookup_requests,
            "contact_lookup_failures": self._contact_lookup_failures,
            "contact_cache_hits": self._contact_cache_hits,
            "chat_lookup_requests": self._chat_lookup_requests,
            "chat_lookup_failures": self._chat_lookup_failures,
            "chat_cache_hits": self._chat_cache_hits,
            "resources_materialized": self._resources_materialized,
            "resource_failures": self._resource_failures,
            "last_resource_disposition": self._last_resource_disposition,
            "activity_turns": self._activity.active_turns,
            "activity_tasks_pending": self._activity.tasks_pending,
            "activity_cards_created": self._activity.cards_created,
            "activity_coalesced_updates": self._activity.coalesced_updates,
            "activity_elements_updated": self._activity.elements_updated,
            "activity_failures": self._activity.failures,
            "activity_rate_limit_retries": self._activity.rate_limit_retries,
            "token_refresh_failures": (
                self._api.token_refresh_failures
                if self._api is not None
                else self._token_refresh_failures
            ),
        }

    def get_identity(self) -> ChannelIdentity | None:
        identity = self._identity
        return identity.as_channel_identity() if identity is not None else None

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise TimeoutError("Lark channel startup deadline expired")
        async with self._lifecycle_lock:
            if self._transport is not None:
                return
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            session: aiohttp.ClientSession | None = None
            api: LarkApi | None = None
            transport: LarkTransport | None = None
            self._state = "starting"
            self._stop_sent = False
            self._inbound = asyncio.Queue()
            self._stream_routes.clear()
            self._stream_route_threads.clear()
            self._activity_turns.clear()
            self._terminal_activity_turns.clear()
            self._degraded_activity_turns.clear()
            try:
                session = aiohttp.ClientSession()
                api = LarkApi(
                    session,
                    app_id=self._app_id,
                    app_secret=self._app_secret,
                    base_url=self._base_url,
                    timer_wheel=self._timer_wheel,
                )
                await api.start()
                bot_info = await api.get_bot_info(timeout=remaining(deadline))
                identity = parse_bot_info(bot_info)
                transport = LarkTransport(
                    api,
                    timer_wheel=self._timer_wheel,
                    on_message=self._handle_event,
                )
                self._session = session
                self._api = api
                self._transport = transport
                self._identity = identity
                await transport.start(timeout=remaining(deadline))
                generation = transport.health.get("connection_generation", 0)
                self._connection_generation = (
                    generation if isinstance(generation, int) else 0
                )
                self._state = "connected"
            except BaseException:
                self._state = "stopping"
                await self._activity.close()
                if transport is not None:
                    await transport.stop(timeout=remaining(deadline))
                if api is not None:
                    await api.stop()
                if session is not None:
                    await session.close()
                self._session = None
                self._api = None
                self._transport = None
                self._identity = None
                self._state = "stopped"
                raise

    async def stop(self, *, timeout: float) -> None:
        if timeout < 0:
            raise ValueError("Lark channel shutdown timeout must not be negative")
        async with self._lifecycle_lock:
            transport = self._transport
            session = self._session
            api = self._api
            self._state = "stopping"
            if transport is not None:
                await transport.stop(timeout=timeout)
            await self._activity.close()
            await self._resource_cache.close()
            await self._close_contact_cache()
            await self._close_chat_cache()
            if api is not None:
                self._token_refresh_failures = api.token_refresh_failures
                await api.stop()
            if session is not None:
                await session.close()
            self._transport = None
            self._api = None
            self._session = None
            self._identity = None
            self._stream_routes.clear()
            self._stream_route_threads.clear()
            self._activity_turns.clear()
            self._terminal_activity_turns.clear()
            self._degraded_activity_turns.clear()
            self._state = "stopped"
            if not self._stop_sent:
                self._inbound.put_nowait(_STOP)
                self._stop_sent = True

    async def receive(self) -> AsyncIterator[Message]:
        inbound = self._inbound
        while True:
            item = await inbound.get()
            if item is _STOP:
                return
            if not isinstance(item, Message):
                raise TypeError("Lark inbound queue contained an invalid message")
            yield item

    async def _quoted_message(
        self,
        parent_id: str,
        *,
        chat_id: str,
        chat_type: str,
        thread_identity: LarkThreadIdentity,
        target_kind: ChannelTargetKind,
        presentation: ChannelTargetPresentation | None,
        tenant_key: str,
        received_at_ms: int,
    ) -> tuple[Message | None, str | None]:
        """Fetch and map the message a reply quotes, or say why it could not be."""

        parent, failure = await self._fetch_parent(parent_id, chat_id=chat_id)
        if parent is None:
            return None, failure
        try:
            quoted = await self._build_inbound(
                parent,
                thread_identity=thread_identity,
                target_kind=target_kind,
                presentation=presentation,
                sender_payload=_message_sender(parent),
                tenant_key=tenant_key,
                mentions_agent=False,
                notifies_runtime=False,
                received_at_ms=received_at_ms,
                provider_payload_metadata={
                    "quoted_backfill": True,
                    "lark_chat_type": chat_type,
                    "threaded": thread_identity.thread_id != "0",
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return None, f"mapping_failed:{type(error).__name__}"
        return quoted, failure

    async def _queue_inbound(
        self,
        current: Message,
        quoted: Message | None,
        *,
        provider_message_id: str,
        threaded: bool,
        target_kind: ChannelTargetKind,
    ) -> None:
        """Hand the message, and whatever it quotes, to the inbound queue."""

        if quoted is not None:
            await self._inbound.put(quoted)
        await self._inbound.put(current)
        self._stream_routes[current.session_id] = provider_message_id
        self._stream_route_threads[current.session_id] = threaded
        self._last_message_disposition = "queued"
        self._last_message_filter_reason = None
        self._observe(
            "lark.message.queued",
            target_kind=target_kind.value,
            message_type=current.message_type,
            quoted=quoted is not None,
        )

    async def _handle_event(
        self,
        message_type: str,
        payload: Mapping[str, object],
        frame: object,
    ) -> bool:
        del message_type, frame
        fields = _read_event(payload, self._identity)
        if isinstance(fields, str):
            return self._filter_event(fields)
        thread_id, readable = _optional_reference(fields.message.get("thread_id"))
        if not readable:
            return self._filter_event("invalid_thread_id")
        thread_id = thread_id or "0"
        parent_id, readable = _optional_reference(fields.message.get("parent_id"))
        if not readable or parent_id == fields.provider_message_id:
            return self._filter_event("invalid_parent_id")
        root_id, readable = _optional_reference(fields.message.get("root_id"))
        if not readable:
            return self._filter_event("invalid_root_id")

        tenant_key = (
            _provider_text(fields.header.get("tenant_key"))
            or _provider_text(fields.sender.get("tenant_key"))
            or ""
        )
        target_kind = (
            ChannelTargetKind.DM
            if fields.chat_type == "p2p"
            else ChannelTargetKind.GROUP
        )
        thread_identity = LarkThreadIdentity(
            bot_open_id=fields.identity.open_id,
            chat_id=fields.chat_id,
            thread_id=thread_id,
        )
        presentation = None
        if target_kind is ChannelTargetKind.GROUP:
            name = await self._chat_name(
                tenant_key=tenant_key,
                chat_id=fields.chat_id,
            )
            if name is not None:
                presentation = ChannelTargetPresentation(display_name=name)
        received_at_ms = time_ns() // 1_000_000
        metadata = _event_metadata(fields, thread_id, root_id)

        quoted: Message | None = None
        if parent_id is not None:
            quoted, failure = await self._quoted_message(
                parent_id,
                chat_id=fields.chat_id,
                chat_type=fields.chat_type,
                thread_identity=thread_identity,
                target_kind=target_kind,
                presentation=presentation,
                tenant_key=tenant_key,
                received_at_ms=received_at_ms,
            )
            if quoted is None:
                metadata["reply_fetch_failed"] = True
                if failure is not None:
                    metadata["reply_failure_kind"] = failure

        current = await self._build_inbound(
            fields.message,
            thread_identity=thread_identity,
            target_kind=target_kind,
            presentation=presentation,
            sender_payload=fields.sender,
            tenant_key=tenant_key,
            mentions_agent=target_kind is ChannelTargetKind.GROUP,
            notifies_runtime=True,
            received_at_ms=received_at_ms,
            reply_to_message_id=quoted.message_id if quoted is not None else None,
            provider_payload_metadata=metadata,
        )
        await self._queue_inbound(
            current,
            quoted,
            provider_message_id=fields.provider_message_id,
            threaded=thread_identity.thread_id != "0",
            target_kind=target_kind,
        )
        return True

    async def _build_inbound(
        self,
        message: Mapping[str, object],
        *,
        thread_identity: LarkThreadIdentity,
        target_kind: ChannelTargetKind,
        presentation: ChannelTargetPresentation | None,
        sender_payload: Mapping[str, object] | None,
        tenant_key: str,
        mentions_agent: bool,
        notifies_runtime: bool,
        received_at_ms: int,
        reply_to_message_id: str | None = None,
        provider_payload_metadata: Mapping[str, object] | None = None,
    ) -> Message:
        provider_message_id = _provider_text(message.get("message_id"))
        raw_message_type = _provider_text(message.get("message_type"))
        if provider_message_id is None or raw_message_type is None:
            raise ValueError("Lark message is missing provider identity")
        identity = self._identity
        if identity is None:
            raise RuntimeError("Lark bot identity is not initialized")
        mentions = _parse_mentions(message.get("mentions"))
        projection = project_lark_content(
            raw_message_type,
            message.get("content"),
            mentions=mentions,
            bot_open_id=identity.open_id,
        )
        attachments = await self._materialize_resources(
            provider_message_id,
            projection.resources,
        )
        sender = await self._sender_identity(
            sender_payload,
            tenant_key=tenant_key,
        )
        metadata = dict(provider_payload_metadata or {})
        sender_type = (
            _provider_text(sender_payload.get("sender_type"))
            if isinstance(sender_payload, Mapping)
            else None
        )
        if sender_type == "user":
            sender_kind = SenderKind.HUMAN
        elif sender_type in {"app", "bot", "system"}:
            sender_kind = SenderKind.AGENT
        else:
            sender_kind = SenderKind.UNKNOWN
        metadata.setdefault("sender_kind", sender_kind.value)
        if projection.content_error:
            metadata["content_parse_failed"] = True
        return Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id=thread_identity.message_id(provider_message_id),
            session_id=thread_identity.session_id,
            channel_session_id=thread_identity.channel_session_id,
            channel=self.name,
            provider_thread_id=thread_identity.provider_thread_id,
            provider_message_id=provider_message_id,
            received_at_ms=received_at_ms,
            sender=sender,
            message_type=projection.message_type,
            target=f"{target_kind.value}:{thread_identity.channel_session_id}",
            body=projection.body,
            target_kind=target_kind,
            target_presentation=presentation,
            mentions_agent=mentions_agent,
            notifies_runtime=notifies_runtime,
            attachments=attachments,
            provider_time_ms=_provider_time_ms(message.get("create_time")),
            reply_to_message_id=reply_to_message_id,
            metadata=metadata,
        )

    async def _fetch_parent(
        self,
        parent_id: str,
        *,
        chat_id: str,
    ) -> tuple[Mapping[str, object] | None, str | None]:
        api = self._api
        if api is None:
            return None, "api_unavailable"
        try:
            response = await api.get_message(
                parent_id,
                timeout=_PARENT_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._observe(
                "lark.reply.fetch_failed",
                error_kind=type(error).__name__,
            )
            return None, f"request_failed:{type(error).__name__}"
        data = response.get("data")
        items = data.get("items") if isinstance(data, Mapping) else None
        if (
            not isinstance(items, list)
            or not items
            or not isinstance(items[0], Mapping)
        ):
            return None, "malformed_response"
        parent = _normalize_parent_message(items[0])
        if _provider_text(parent.get("message_id")) is None:
            return None, "missing_parent_message_id"
        parent_chat_id = parent.get("chat_id")
        if parent_chat_id is not None and _provider_text(parent_chat_id) != chat_id:
            return None, "parent_chat_mismatch"
        return parent, None

    async def _sender_identity(
        self,
        sender_payload: Mapping[str, object] | None,
        *,
        tenant_key: str,
    ) -> SenderIdentity | None:
        if not isinstance(sender_payload, Mapping):
            return None
        sender_id = sender_payload.get("sender_id")
        if not isinstance(sender_id, Mapping):
            sender_id = sender_payload.get("id")
        if not isinstance(sender_id, Mapping):
            return None
        open_id = _provider_text(sender_id.get("open_id"))
        if open_id is None:
            return None
        sender_type = _provider_text(sender_payload.get("sender_type"))
        if sender_type != "user":
            return SenderIdentity(id=open_id)
        return SenderIdentity(
            id=open_id,
            display_name=await self._contact_name(
                tenant_key=tenant_key, open_id=open_id
            ),
        )

    async def _contact_name(self, *, tenant_key: str, open_id: str) -> str | None:
        key = (self._app_id, tenant_key, open_id)
        now = monotonic()
        async with self._contact_lock:
            cached = self._contact_cache.get(key)
            if cached is not None:
                expires_at, name = cached
                if expires_at > now:
                    self._contact_cache_hits += 1
                    self._contact_cache.move_to_end(key)
                    return name
                self._contact_cache.pop(key, None)
            task = self._contact_inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._load_contact_name(key, open_id),
                    name="bcn-lark-contact",
                )
                self._contact_inflight[key] = task
        return await asyncio.shield(task)

    async def _load_contact_name(
        self,
        key: tuple[str, str, str],
        open_id: str,
    ) -> str | None:
        name: str | None = None
        completed = False
        try:
            api = self._api
            if api is None:
                completed = True
                return None
            self._contact_lookup_requests += 1
            response = await api.get_user(open_id, timeout=_CONTACT_TIMEOUT_SECONDS)
            name = _contact_display_name(response)
            completed = True
            return name
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._contact_lookup_failures += 1
            self._observe(
                "lark.contact.lookup_failed",
                error_kind=type(error).__name__,
            )
            completed = True
            return None
        finally:
            async with self._contact_lock:
                self._contact_inflight.pop(key, None)
                if completed:
                    self._contact_cache[key] = (
                        monotonic()
                        + (
                            _CONTACT_CACHE_TTL_SECONDS
                            if name is not None
                            else _CONTACT_FAILURE_CACHE_TTL_SECONDS
                        ),
                        name,
                    )
                    self._contact_cache.move_to_end(key)
                    while len(self._contact_cache) > _CONTACT_CACHE_MAX_ENTRIES:
                        self._contact_cache.popitem(last=False)

    async def _close_contact_cache(self) -> None:
        async with self._contact_lock:
            tasks = tuple(self._contact_inflight.values())
            self._contact_inflight.clear()
            self._contact_cache.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _chat_name(self, *, tenant_key: str, chat_id: str) -> str | None:
        key = (self._app_id, tenant_key, chat_id)
        now = monotonic()
        async with self._chat_lock:
            cached = self._chat_cache.get(key)
            if cached is not None:
                expires_at, name = cached
                if expires_at > now:
                    self._chat_cache_hits += 1
                    self._chat_cache.move_to_end(key)
                    return name
                self._chat_cache.pop(key, None)
            task = self._chat_inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._load_chat_name(key, chat_id),
                    name="bcn-lark-chat",
                )
                self._chat_inflight[key] = task
        return await asyncio.shield(task)

    async def _load_chat_name(
        self,
        key: tuple[str, str, str],
        chat_id: str,
    ) -> str | None:
        name: str | None = None
        completed = False
        try:
            api = self._api
            if api is None:
                completed = True
                return None
            self._chat_lookup_requests += 1
            response = await api.get_chat(chat_id, timeout=_CHAT_TIMEOUT_SECONDS)
            data = response.get("data")
            raw_name = data.get("name") if isinstance(data, Mapping) else None
            if (
                isinstance(raw_name, str)
                and raw_name.strip()
                and "]" not in raw_name
                and not any(
                    category(character) in {"Cc", "Zl", "Zp"} for character in raw_name
                )
            ):
                name = raw_name
            else:
                self._chat_lookup_failures += 1
                self._observe(
                    "lark.chat.lookup_failed",
                    error_kind="invalid_name",
                )
            completed = True
            return name
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._chat_lookup_failures += 1
            self._observe(
                "lark.chat.lookup_failed",
                error_kind=type(error).__name__,
            )
            completed = True
            return None
        finally:
            async with self._chat_lock:
                self._chat_inflight.pop(key, None)
                if completed:
                    self._chat_cache[key] = (
                        monotonic()
                        + (
                            _CHAT_CACHE_TTL_SECONDS
                            if name is not None
                            else _CHAT_FAILURE_CACHE_TTL_SECONDS
                        ),
                        name,
                    )
                    self._chat_cache.move_to_end(key)
                    while len(self._chat_cache) > _CHAT_CACHE_MAX_ENTRIES:
                        self._chat_cache.popitem(last=False)

    async def _close_chat_cache(self) -> None:
        async with self._chat_lock:
            tasks = tuple(self._chat_inflight.values())
            self._chat_inflight.clear()
            self._chat_cache.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _materialize_resources(
        self,
        provider_message_id: str,
        resources: tuple[LarkResourceDescriptor, ...],
    ) -> tuple[InboundAttachment, ...]:
        if not resources:
            return ()
        api = self._api
        attachments = []
        for resource in resources:
            try:
                if api is None:
                    raise RuntimeError("api_unavailable")
                attachment = await self._resource_cache.materialize(
                    api,
                    provider_message_id=provider_message_id,
                    resource=resource,
                    timeout=_RESOURCE_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                attachment = self._context.attachments.failed(
                    name=resource.name,
                    kind=resource.resource_type,
                    error=f"resource_download_failed:{type(error).__name__}",
                    media_type=resource.media_type,
                )
            attachments.append(attachment)
            if attachment.state == "ready":
                self._resources_materialized += 1
                self._last_resource_disposition = "materialized"
                self._observe(
                    "lark.resource.materialized",
                    resource_type=resource.resource_type,
                )
            else:
                self._resource_failures += 1
                self._last_resource_disposition = "failed"
                self._observe(
                    "lark.resource.failed",
                    resource_type=resource.resource_type,
                )
        return tuple(attachments)

    def _filter_event(self, reason: str) -> bool:
        self._last_message_disposition = "filtered"
        self._last_message_filter_reason = reason
        self._observe("lark.message.filtered", reason=reason)
        return False

    @staticmethod
    def _observe(event_name: str, **metadata: object) -> None:
        _LOGGER.info(
            "%s",
            json.dumps(
                {
                    "event_name": event_name,
                    "created_at_ms": time_ns() // 1_000_000,
                    "metadata": metadata,
                },
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
        )

    def anchor_turn(self, session_id: str, anchor: Message) -> None:
        provider_message_id = anchor.provider_message_id
        if provider_message_id is None:
            return
        self._stream_routes[session_id] = provider_message_id
        self._stream_route_threads[session_id] = anchor.metadata.get("threaded") is True

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        provider_message_id = self._stream_routes.get(item.envelope.session_id)
        self._activity.accept(
            item,
            route=(
                LarkActivityRoute(
                    message_id=provider_message_id,
                    reply_in_thread=self._stream_route_threads.get(
                        item.envelope.session_id, False
                    ),
                )
                if provider_message_id is not None
                else None
            ),
            api=self._api,
        )
        match item.payload:
            case TurnStarted():
                turn_id = item.envelope.turn_id
                previous_turn_id = self._activity_turns.get(session_id)
                if previous_turn_id is not None:
                    self._terminal_activity_turns.discard(
                        (session_id, previous_turn_id)
                    )
                    self._degraded_activity_turns.discard(
                        (session_id, previous_turn_id)
                    )
                self._activity_turns[session_id] = turn_id
                return
            case TurnCompleted():
                turn_id = item.envelope.turn_id
                if self._activity_turns.get(session_id) == turn_id:
                    self._activity_turns.pop(session_id)
                    self._degraded_activity_turns.discard((session_id, turn_id))
                self._stream_routes.pop(session_id, None)
                self._stream_route_threads.pop(session_id, None)
                return
            case TurnFailed() | TurnCancelled() | TurnUnknown():
                turn_id = item.envelope.turn_id
                if self._activity_turns.get(session_id) == turn_id:
                    self._terminal_activity_turns.add((session_id, turn_id))
                self._stream_routes.pop(session_id, None)
                self._stream_route_threads.pop(session_id, None)
                return
            case (
                ContentDelta()
                | ContextCompactionStarted()
                | ContextCompactionCompleted()
                | ToolCallStarted()
                | ToolCallCompleted()
                | ToolCallFailed()
                | ToolCallTextDelta()
                | ToolCallPatchUpdated()
                | ToolCallInteraction()
                | UsageUpdated()
            ):
                return

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        turn_id = self._activity_turns.get(request.session_id)
        activity_key = (request.session_id, turn_id) if turn_id is not None else None
        if timeout <= 0:
            self._retire_terminal_activity(activity_key)
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="delivery_timeout",
                error_message="Lark delivery deadline expired",
            )
        api = self._api
        identity = self._identity
        transport = self._transport
        if (
            api is None
            or identity is None
            or transport is None
            or self._state not in {"connected", "ready"}
            or transport.state != "connected"
        ):
            self._retire_terminal_activity(activity_key)
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="channel_unavailable",
                error_message="Lark channel is not available",
            )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        if turn_id is not None:
            try:
                await self._activity.drain(
                    request.session_id,
                    turn_id,
                    timeout=max(0.0, (deadline - loop.time()) / 2),
                )
            except asyncio.CancelledError:
                self._retire_terminal_activity(activity_key)
                raise
            except TimeoutError:
                self._degraded_activity_turns.add((request.session_id, turn_id))
        if activity_key in self._degraded_activity_turns:
            request = replace(
                request,
                body=(
                    f"{request.body}\n\n"
                    f"{self._translator.text('activity.final_incomplete')}"
                ),
            )
        try:
            await asyncio.wait_for(
                self._send_lock.acquire(),
                timeout=remaining(deadline),
            )
        except asyncio.CancelledError:
            self._retire_terminal_activity(activity_key)
            raise
        except TimeoutError:
            self._retire_terminal_activity(activity_key)
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="delivery_timeout",
                error_message="Lark delivery timed out waiting for the send lock",
            )
        try:
            if (
                self._api is not api
                or self._identity is not identity
                or self._transport is not transport
                or self._state not in {"connected", "ready"}
                or transport.state != "connected"
            ):
                return ProviderCallResult(
                    status=ProviderCallStatus.FAILED,
                    error_kind="channel_unavailable",
                    error_message="Lark channel is not available",
                )
            result = await send_outbound(
                api,
                identity=identity,
                workspace=self._context.workspace(),
                request=request,
                timeout=remaining(deadline),
            )
            if (
                result.status
                in {
                    ProviderCallStatus.CONFIRMED,
                    ProviderCallStatus.PARTIAL,
                }
                and activity_key is not None
            ):
                self._degraded_activity_turns.discard(activity_key)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind=type(error).__name__,
                error_message=str(error) or "Lark outbound delivery failed",
            )
        finally:
            self._send_lock.release()
            self._retire_terminal_activity(activity_key)

    def _retire_terminal_activity(
        self,
        activity_key: tuple[str, str] | None,
    ) -> None:
        if activity_key is None or activity_key not in self._terminal_activity_turns:
            return
        self._terminal_activity_turns.discard(activity_key)
        self._degraded_activity_turns.discard(activity_key)
        session_id, turn_id = activity_key
        if self._activity_turns.get(session_id) == turn_id:
            self._activity_turns.pop(session_id)

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        del timeout
        return ApprovalResult(
            request_id=request.approval.request_id,
            decision=ApprovalDecision.REJECTED,
            decided_at_ms=time_ns() // 1_000_000,
            reason="lark_approval_unavailable",
        )


def _provider_text(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
        return None
    return value


def _provider_time_ms(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_mentions(value: object) -> dict[str, LarkMention]:
    if not isinstance(value, list):
        return {}
    mentions: dict[str, LarkMention] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = _provider_text(item.get("key"))
        if key is None:
            continue
        mention_id = item.get("id")
        open_id = (
            _provider_text(mention_id.get("open_id"))
            if isinstance(mention_id, Mapping)
            else None
        )
        display_name = _provider_text(item.get("name")) or open_id or key
        mentions[key] = LarkMention(
            key=key,
            open_id=open_id,
            display_name=display_name,
        )
    return mentions


@dataclass(frozen=True, slots=True)
class _EventFields:
    header: Mapping[str, object]
    message: Mapping[str, object]
    sender: Mapping[str, object]
    identity: LarkBotIdentity
    provider_message_id: str
    chat_id: str
    chat_type: str
    sender_type: str


def _read_event(
    payload: Mapping[str, object], identity: LarkBotIdentity | None
) -> _EventFields | str:
    """Read a message event, or name the reason it cannot be taken."""

    schema = payload.get("schema")
    header = payload.get("header")
    event = payload.get("event")
    if (
        schema != "2.0"
        or not isinstance(header, Mapping)
        or not isinstance(event, Mapping)
    ):
        return "invalid_envelope"
    if header.get("event_type") != _EVENT_TYPE:
        return "unsupported_event_type"

    message = event.get("message")
    sender = event.get("sender")
    if not isinstance(message, Mapping) or not isinstance(sender, Mapping):
        return "invalid_message_event"
    if identity is None:
        return "identity_unavailable"

    provider_message_id = _provider_text(message.get("message_id"))
    chat_id = _provider_text(message.get("chat_id"))
    chat_type = _provider_text(message.get("chat_type"))
    raw_message_type = _provider_text(message.get("message_type"))
    sender_id = sender.get("sender_id")
    sender_open_id = (
        _provider_text(sender_id.get("open_id"))
        if isinstance(sender_id, Mapping)
        else None
    )
    if provider_message_id is None:
        return "invalid_message_id"
    if chat_id is None:
        return "invalid_chat_id"
    if chat_type not in {"p2p", "group", "topic"}:
        return "unsupported_chat_type"
    if raw_message_type is None:
        return "invalid_message_type"
    if sender_open_id is None:
        return "invalid_sender"
    sender_type = _provider_text(sender.get("sender_type")) or "unknown"
    if sender_open_id == identity.open_id:
        return "current_bot_message"
    return _EventFields(
        header=header,
        message=message,
        sender=sender,
        identity=identity,
        provider_message_id=provider_message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        sender_type=sender_type,
    )


def _event_metadata(
    fields: _EventFields, thread_id: str, root_id: str | None
) -> dict[str, object]:
    """Carry the provider's own view of an event alongside the message."""

    metadata: dict[str, object] = {
        "lark_event_type": _EVENT_TYPE,
        "lark_chat_type": fields.chat_type,
        "lark_sender_type": fields.sender_type,
        "lark_threaded": thread_id != "0",
        "threaded": thread_id != "0",
    }
    event_id = _provider_text(fields.header.get("event_id"))
    if event_id is not None:
        metadata["lark_event_id"] = event_id
    if root_id is not None:
        metadata["lark_root_id"] = root_id
    return metadata


def _optional_reference(value: object) -> tuple[str | None, bool]:
    """Read an id the provider may omit; the flag says whether it was readable."""

    if value is None or value == "":
        return None, True
    text = _provider_text(value)
    return text, text is not None


def _message_sender(message: Mapping[str, object]) -> Mapping[str, object] | None:
    sender = message.get("sender")
    if isinstance(sender, Mapping):
        return sender
    sender_id = message.get("sender_id")
    if isinstance(sender_id, Mapping):
        return {
            "sender_id": sender_id,
            "sender_type": _provider_text(message.get("sender_type")) or "unknown",
            "tenant_key": _provider_text(message.get("tenant_key")) or "",
        }
    return None


def _normalize_parent_message(message: Mapping[str, object]) -> dict[str, object]:
    """Adapt the message REST response to the event message shape."""
    normalized = dict(message)
    if _provider_text(normalized.get("message_type")) is None:
        message_type = _provider_text(normalized.get("msg_type"))
        if message_type is not None:
            normalized["message_type"] = message_type
    if normalized.get("content") is None:
        body = normalized.get("body")
        if isinstance(body, Mapping) and "content" in body:
            normalized["content"] = body["content"]
    sender = normalized.get("sender")
    if isinstance(sender, Mapping) and not isinstance(sender.get("sender_id"), Mapping):
        sender_id = _provider_text(sender.get("id"))
        sender_id_type = _provider_text(sender.get("id_type")) or "open_id"
        if sender_id is not None and sender_id_type == "open_id":
            normalized_sender = dict(sender)
            normalized_sender["sender_id"] = {"open_id": sender_id}
            normalized["sender"] = normalized_sender
    return normalized


def _contact_display_name(response: Mapping[str, object]) -> str | None:
    data = response.get("data")
    user = data.get("user") if isinstance(data, Mapping) else None
    if not isinstance(user, Mapping):
        return None
    for field_name in ("name", "en_name", "nickname"):
        value = _provider_text(user.get(field_name))
        if value is not None:
            return value
    return None


__all__ = ["LarkChannel"]
