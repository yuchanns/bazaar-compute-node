from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from time import monotonic, time_ns

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
    InboundAttachment,
    InboundMessage,
    RuntimeEvent,
    RuntimeEventState,
    SenderIdentity,
    StreamEvent,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.runtime import RuntimeStreamItem
from ...core.timerwheel import TimerWheel
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
_CONTACT_CACHE_TTL_SECONDS = 5 * 60
_CONTACT_CACHE_MAX_ENTRIES = 256
_CONTACT_TIMEOUT_SECONDS = 5.0
_PARENT_TIMEOUT_SECONDS = 10.0
_RESOURCE_TIMEOUT_SECONDS = 60.0
_TYPING_TIMEOUT_SECONDS = 3.0
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _TypingState:
    message_id: str
    add_queued: bool = False
    attempted: bool = False
    reaction_id: str | None = None
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class _TypingCommand:
    session_id: str
    message_id: str


_TYPING_STOP = object()


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
        self._inbound: asyncio.Queue[InboundMessage | object] = asyncio.Queue()
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
        self._resource_cache = LarkResourceCache(context.attachments)
        self._resources_materialized = 0
        self._resource_failures = 0
        self._last_resource_disposition: str | None = None
        self._send_lock = asyncio.Lock()
        self._stream_routes: dict[str, str] = {}
        self._typing_queue: asyncio.Queue[_TypingCommand | object] = asyncio.Queue()
        self._typing_runner: asyncio.Task[None] | None = None
        self._typing_states: dict[str, _TypingState] = {}
        self._typing_stopping = False
        self._typing_requests = 0
        self._typing_failures = 0

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
            "resources_materialized": self._resources_materialized,
            "resource_failures": self._resource_failures,
            "last_resource_disposition": self._last_resource_disposition,
            "typing_requests": self._typing_requests,
            "typing_failures": self._typing_failures,
            "typing_sessions": sum(
                not state.terminal for state in self._typing_states.values()
            ),
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
            self._typing_queue = asyncio.Queue()
            self._typing_states.clear()
            self._typing_stopping = False
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
                bot_info = await api.get_bot_info(timeout=_remaining(deadline))
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
                self._typing_runner = asyncio.create_task(
                    self._run_typing_dispatcher(),
                    name="bcn-lark-typing",
                )
                await transport.start(timeout=_remaining(deadline))
                generation = transport.health.get("connection_generation", 0)
                self._connection_generation = (
                    generation if isinstance(generation, int) else 0
                )
                self._state = "connected"
            except BaseException:
                self._state = "stopping"
                typing_runner = self._typing_runner
                self._typing_runner = None
                self._typing_stopping = True
                if typing_runner is not None:
                    typing_runner.cancel()
                    await asyncio.gather(typing_runner, return_exceptions=True)
                self._typing_states.clear()
                if transport is not None:
                    await transport.stop(timeout=_remaining(deadline))
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
            await self._stop_typing(timeout=timeout)
            await self._resource_cache.close()
            await self._close_contact_cache()
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
            self._state = "stopped"
            if not self._stop_sent:
                self._inbound.put_nowait(_STOP)
                self._stop_sent = True

    async def receive(self) -> AsyncIterator[InboundMessage]:
        inbound = self._inbound
        while True:
            item = await inbound.get()
            if item is _STOP:
                return
            if not isinstance(item, InboundMessage):
                raise TypeError("Lark inbound queue contained an invalid message")
            yield item

    async def _handle_event(
        self,
        message_type: str,
        payload: Mapping[str, object],
        frame: object,
    ) -> bool:
        del message_type, frame
        schema = payload.get("schema")
        header = payload.get("header")
        event = payload.get("event")
        if (
            schema != "2.0"
            or not isinstance(header, Mapping)
            or not isinstance(event, Mapping)
        ):
            return self._filter_event("invalid_envelope")
        if header.get("event_type") != _EVENT_TYPE:
            return self._filter_event("unsupported_event_type")

        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, Mapping) or not isinstance(sender, Mapping):
            return self._filter_event("invalid_message_event")
        identity = self._identity
        if identity is None:
            return self._filter_event("identity_unavailable")

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
            return self._filter_event("invalid_message_id")
        if chat_id is None:
            return self._filter_event("invalid_chat_id")
        if chat_type not in {"p2p", "group", "topic"}:
            return self._filter_event("unsupported_chat_type")
        if raw_message_type is None:
            return self._filter_event("invalid_message_type")
        if sender_open_id is None:
            return self._filter_event("invalid_sender")
        sender_type = _provider_text(sender.get("sender_type")) or "unknown"
        if sender_open_id == identity.open_id:
            return self._filter_event("current_bot_message")

        raw_thread_id = message.get("thread_id")
        if raw_thread_id is None or raw_thread_id == "":
            thread_id = "0"
        else:
            thread_id = _provider_text(raw_thread_id)
            if thread_id is None:
                return self._filter_event("invalid_thread_id")
        raw_parent_id = message.get("parent_id")
        if raw_parent_id is None or raw_parent_id == "":
            parent_id = None
        else:
            parent_id = _provider_text(raw_parent_id)
            if parent_id is None or parent_id == provider_message_id:
                return self._filter_event("invalid_parent_id")
        raw_root_id = message.get("root_id")
        if raw_root_id is None or raw_root_id == "":
            root_id = None
        else:
            root_id = _provider_text(raw_root_id)
            if root_id is None:
                return self._filter_event("invalid_root_id")

        tenant_key = (
            _provider_text(header.get("tenant_key"))
            or _provider_text(sender.get("tenant_key"))
            or ""
        )
        target_kind = (
            ChannelTargetKind.DM if chat_type == "p2p" else ChannelTargetKind.GROUP
        )
        thread_identity = LarkThreadIdentity(
            bot_open_id=identity.open_id,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        received_at_ms = time_ns() // 1_000_000
        metadata = {
            "lark_event_type": _EVENT_TYPE,
            "lark_chat_type": chat_type,
            "lark_sender_type": sender_type,
            "lark_threaded": thread_id != "0",
        }
        event_id = _provider_text(header.get("event_id"))
        if event_id is not None:
            metadata["lark_event_id"] = event_id
        if root_id is not None:
            metadata["lark_root_id"] = root_id

        quoted: InboundMessage | None = None
        if parent_id is not None:
            parent, failure = await self._fetch_parent(
                parent_id,
                chat_id=chat_id,
            )
            if parent is not None:
                try:
                    quoted = await self._build_inbound(
                        parent,
                        thread_identity=thread_identity,
                        target_kind=target_kind,
                        sender_payload=_message_sender(parent),
                        tenant_key=tenant_key,
                        mentions_agent=False,
                        notifies_runtime=False,
                        received_at_ms=received_at_ms,
                        provider_payload_metadata={
                            "quoted_backfill": True,
                            "lark_chat_type": chat_type,
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001
                    failure = f"mapping_failed:{type(error).__name__}"
            if quoted is None:
                metadata["reply_fetch_failed"] = True
                if failure is not None:
                    metadata["reply_failure_kind"] = failure

        current = await self._build_inbound(
            message,
            thread_identity=thread_identity,
            target_kind=target_kind,
            sender_payload=sender,
            tenant_key=tenant_key,
            mentions_agent=target_kind is ChannelTargetKind.GROUP,
            notifies_runtime=True,
            received_at_ms=received_at_ms,
            reply_to_message_id=quoted.message_id if quoted is not None else None,
            provider_payload_metadata=metadata,
        )
        if quoted is not None:
            await self._inbound.put(quoted)
        await self._inbound.put(current)
        self._stream_routes[current.session_id] = provider_message_id
        self._last_message_disposition = "queued"
        self._last_message_filter_reason = None
        self._observe(
            "lark.message.queued",
            target_kind=target_kind.value,
            message_type=current.message_type,
            quoted=quoted is not None,
        )
        return True

    async def _build_inbound(
        self,
        message: Mapping[str, object],
        *,
        thread_identity: LarkThreadIdentity,
        target_kind: ChannelTargetKind,
        sender_payload: Mapping[str, object] | None,
        tenant_key: str,
        mentions_agent: bool,
        notifies_runtime: bool,
        received_at_ms: int,
        reply_to_message_id: str | None = None,
        provider_payload_metadata: Mapping[str, object] | None = None,
    ) -> InboundMessage:
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
        if projection.content_error:
            metadata["content_parse_failed"] = True
        return InboundMessage(
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
            canonical_target=f"{target_kind.value}:{thread_identity.channel_session_id}",
            body=projection.body,
            target_kind=target_kind,
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
            name=await self._contact_name(tenant_key=tenant_key, open_id=open_id),
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
                        monotonic() + _CONTACT_CACHE_TTL_SECONDS,
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

    def accept_turn_event(
        self,
        item: RuntimeStreamItem,
        *,
        session_id: str,
    ) -> None:
        if isinstance(item, RuntimeEvent):
            if item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                self._stream_routes.pop(session_id, None)
                state = self._typing_states.get(session_id)
                if state is not None:
                    state.terminal = True
                    # Reactions are durable stream-start markers; terminal only
                    # ends local tracking and never removes the provider marker.
                    if state.reaction_id is not None:
                        self._typing_states.pop(session_id, None)
            return
        if not isinstance(item, StreamEvent) or self._typing_stopping:
            return
        provider_message_id = self._stream_routes.get(item.session_id)
        if provider_message_id is None or self._typing_runner is None:
            return
        state = self._typing_states.get(item.session_id)
        if state is None:
            state = _TypingState(message_id=provider_message_id)
            self._typing_states[item.session_id] = state
        if not state.add_queued and not state.attempted:
            state.add_queued = True
            self._typing_queue.put_nowait(
                _TypingCommand(
                    session_id=item.session_id,
                    message_id=provider_message_id,
                )
            )

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        if timeout <= 0:
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
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="channel_unavailable",
                error_message="Lark channel is not available",
            )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            await asyncio.wait_for(
                self._send_lock.acquire(),
                timeout=max(0.0, deadline - loop.time()),
            )
        except TimeoutError:
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
            return await send_outbound(
                api,
                identity=identity,
                workspace=self._context.workspace(),
                request=request,
                timeout=max(0.0, deadline - loop.time()),
            )
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

    async def _run_typing_dispatcher(self) -> None:
        while True:
            command = await self._typing_queue.get()
            if command is _TYPING_STOP:
                return
            if not isinstance(command, _TypingCommand):
                continue
            await self._add_typing_reaction(command)

    async def _add_typing_reaction(self, command: _TypingCommand) -> None:
        state = self._typing_states.get(command.session_id)
        if state is None or state.attempted or self._typing_stopping:
            return
        state.attempted = True
        api = self._api
        if api is None:
            self._typing_states.pop(command.session_id, None)
            return
        self._typing_requests += 1
        try:
            reaction_id = await api.create_reaction(
                command.message_id,
                emoji_type="Typing",
                timeout=_TYPING_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._typing_failures += 1
            self._typing_states.pop(command.session_id, None)
            return
        state = self._typing_states.get(command.session_id)
        if state is None:
            return
        state.add_queued = False
        state.reaction_id = reaction_id
        if state.terminal or self._typing_stopping:
            self._typing_states.pop(command.session_id, None)

    async def _stop_typing(self, *, timeout: float) -> None:
        self._typing_stopping = True
        runner = self._typing_runner
        self._typing_runner = None
        if runner is None:
            self._typing_states.clear()
            return
        # Keep provider reactions when the local dispatcher shuts down.
        self._typing_queue.put_nowait(_TYPING_STOP)
        try:
            await asyncio.wait_for(
                runner,
                timeout=max(0.0, timeout),
            )
        except TimeoutError:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        self._typing_states.clear()

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


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


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
