# 2026-08-16 Telegram Channel Plan

## 状态

- 模式：Plan
- 状态：待 review；review 通过后只进入 Phase 1 Task 1A。
- 基线：`main`，当前提交为 `f7fabcd5e480f1ada40a22cff7e6e37348486db7`。
- 当前更新定义 Channel builder、通用 Channel 配置、Telegram polling、thread/topic、
  inbound、reply、approval、Rich Markdown、附件和验证边界。
- 所有 Task 按本文顺序串行实施；每完成一个 Task，运行 focused checks，发送业务 diff，
  并停在 review。

## 1. 目标

在现有 provider-neutral Channel、message inbox、thread following、reply reference、
approval、runtime stream event 和 outbound delivery contract 基础上增加 Telegram Channel。

完整业务链路：

```text
bcn selects channel=telegram
    -> generic config loader selects [channel.telegram]
    -> TelegramBuilder creates TelegramChannel
    -> TelegramChannel calls getMe
    -> getUpdates receives message and callback_query updates
    -> chat id + topic id resolve one BCN thread
    -> inbound message enters the existing durable inbox
    -> existing DM/following/mention policy decides runtime notice
    -> runtime reads the inbox and performs one turn
    -> runtime StreamEvent continues to be offered to TelegramChannel
    -> approval request is rendered as a Telegram inline keyboard when required
    -> runtime produces a final outbound message
    -> TelegramChannel sends Rich Markdown to the same chat/topic
```

产品语义：

1. Channel entry point 暴露 `IChannelBuilder`。
2. `WeComBuilder` 和 `TelegramBuilder` 分别拥有各自的配置读取与 Channel 构造逻辑。
3. App 配置层按选中的 channel name 传递对应 `[channel.<name>]` table。
4. DM 使用 `ChannelTargetKind.DM`。
5. Group 与 supergroup 使用 `ChannelTargetKind.GROUP`。
6. Group 中每个 topic 对应一个独立 BCN thread。
7. Group thread 通过显式 mention 或回复当前 bot 进入 following。
8. `bcc thread unfollow` 继续使用现有 `ChannelSession.following`。
9. Channel 构造时记录启动时间；早于启动时间的消息仅在 activation 成立时触发 runtime
   notice。
10. 回复当前 bot 与显式 mention 使用相同 activation 语义。
11. 回复消息携带的原消息先作为 history 进入 inbox，当前消息再建立 reply reference。
12. 顶层 sender 为当前 bot 的 update 在 Telegram ingress 过滤；引用中的当前 bot 消息正常
    进入 history。
13. 其他 bot 与人类统一映射为 inbound sender ID。
14. Runtime `StreamEvent` 继续传递到 Channel；TelegramChannel 当前实现接收后立即返回。
15. 出站正文使用 `sendRichMessage` 与 Rich Markdown。
16. Approval 使用同一 chat/topic 中的 inline keyboard。
17. Telegram Bot API transport 直接使用项目现有 `aiohttp`。
18. `getUpdates` long-poll timeout 固定为 50 秒。

## 2. Channel Builder 与通用配置

### 2.1 `IChannelBuilder`

在 `core/channel.py` 增加：

```python
class IChannelBuilder(Protocol):
    def build(self, context: ChannelContext) -> IChannel:
        """Build one configured channel adapter."""
        ...
```

`AdapterFactories` 调整为：

```python
@dataclass(frozen=True, slots=True)
class AdapterFactories:
    channel: IChannelBuilder
    runtime: RuntimeFactory
    storage: StorageFactory
    audit: AuditFactory
    control: ControlFactory | None = None
```

`AdapterRegistry`：

1. 从 `bazaar_compute_node.channels` 加载 entry point object。
2. 验证 object 提供 callable `build`。
3. 将 builder 放入 `AdapterFactories.channel`。
4. `NodeApplication` 创建 `ChannelContext` 后调用：

```python
self.channel = factories.channel.build(
    ChannelContext(
        attachments=self._attachment_materializer,
        options=dict(channel_options or {}),
        workspace=self._workspace_path,
    )
)
```

Channel entry points：

```toml
[project.entry-points."bazaar_compute_node.channels"]
wecom = "bazaar_compute_node.contrib.wecom.plugin:builder"
telegram = "bazaar_compute_node.contrib.telegram.plugin:builder"
```

### 2.2 `WeComBuilder`

`contrib/wecom/plugin.py` 提供：

```python
class WeComBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel: ...


builder = WeComBuilder()
```

Builder 读取：

```text
bot_id
websocket_url
BCN_WECOM_BOT_SECRET
```

`bot_id`、`websocket_url` 的类型、默认值和 required 语义继续由 WeCom contrib 管理。
Mapping 中其余字段保持无操作。

### 2.3 `TelegramBuilder`

`contrib/telegram/plugin.py` 提供：

```python
class TelegramBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel: ...


builder = TelegramBuilder()
```

Builder 读取：

```text
BCN_TELEGRAM_BOT_TOKEN
```

Transport 常量由 Telegram contrib 内部维护：

```python
_API_BASE_URL = "https://api.telegram.org"
_POLL_TIMEOUT_SECONDS = 50
_POLL_HTTP_TIMEOUT_SECONDS = 60
_CONNECT_TIMEOUT_SECONDS = 10
```

`ChannelContext.options` 中其余字段保持无操作。

### 2.4 Generic Channel configuration

`NodeConfiguration` 使用：

```python
channel_options: Mapping[str, Mapping[str, object]]
```

配置：

```toml
[node]
channel = "telegram"
runtime = "codex"

[channel.telegram]

[channel.wecom]
bot_id = "wecom-bot"
websocket_url = "wss://openws.work.weixin.qq.com"
```

`load_node_configuration()`：

1. 读取 `[channel]` table。
2. 保存每个 `[channel.<name>]` 的 mapping。
3. 保持 TOML-native value。
4. 返回 immutable outer/inner mapping。

CLI 在 channel flag 和 node configuration 合并完成后选择：

```python
args.channel_options = dict(configuration.channel_options.get(args.channel, {}))
```

`_run_node()` 将 `args.channel_options` 直接传给 `NodeApplication`。

Daemon child 继续通过同一个 config path 重新加载 selected channel options。

## 3. Provider-neutral Approval 路由

### 3.1 `ChannelApprovalRequest`

在 `core/channel.py` 增加：

```python
@dataclass(frozen=True, slots=True)
class ChannelApprovalRequest:
    approval: ApprovalRequest
    target_kind: ChannelTargetKind
    provider_thread_id: str
    provider_reply_to_message_id: str | None = None
    provider_sender_id: str | None = None
```

字段语义：

```text
approval:
    runtime 提交的 provider-neutral approval 内容

target_kind:
    DM 或 GROUP

provider_thread_id:
    Channel 自己可解析的 thread route

provider_reply_to_message_id:
    触发当前 turn 的 provider message id

provider_sender_id:
    触发当前 turn 的 provider sender id
```

`IChannel.request_approval` 调整为：

```python
async def request_approval(
    self,
    request: ChannelApprovalRequest,
    *,
    timeout: float,
) -> ApprovalResult: ...
```

`SessionTurnCoordinator.approval_handler()` 使用当前已有的：

```text
InboundMessage
ChannelSession
BcnSession
RuntimeSession
RuntimeTurn
```

构造 `ChannelApprovalRequest` 后调用 Channel。

### 3.2 User-facing approval description

`ApprovalRequest` 增加：

```python
description: str | None = None
```

`description` 是 runtime adapter 生成的 bounded、provider-neutral、用户可读文本。

Codex adapter 根据 approval type 构造：

```text
command_execution:
    reason
    command
    cwd

file_change:
    reason
    grant root 或相关文件说明

permissions:
    reason
    cwd
    requested permission profile
```

`action` 保持 machine-readable identity，`description` 用于 Channel UI。

### 3.3 Existing Channel adaptation

`WeComChannel.request_approval()` 接收 `ChannelApprovalRequest`，并沿用当前 decision policy。

Test Channel 记录完整 `ChannelApprovalRequest`，用于验证：

```text
thread route
reply message
sender binding
request correlation
timeout
decision
```

## 4. Telegram transport 与 lifecycle

### 4.1 API client

`contrib/telegram/api.py` 实现薄 Bot API client：

```text
JSON POST
multipart POST
file download stream
Telegram response envelope
retry_after
request deadline
token redaction
```

首批 method：

```text
getMe
getUpdates
getFile
sendRichMessage
sendDocument
answerCallbackQuery
```

每个 request 使用共享 `aiohttp.ClientSession`。

Transport error 保留：

```text
HTTP status
Telegram error_code
bounded description
retry_after
request attempted state
```

日志、health 和 exception message 使用无 token 的 method name。

### 4.2 Construction

`TelegramChannel.__init__()`：

1. 保存 token。
2. 保存固定 API 和 timeout constants。
3. 记录 `started_at_s`。
4. 创建 inbound queue。
5. 创建 stopping event。
6. 创建 pending approval map。
7. 初始化 health counters。

### 4.3 Start

`start()`：

1. 创建共享 `aiohttp.ClientSession`。
2. 调用 `getMe`。
3. 保存 bot ID 和 username。
4. 创建 polling task。
5. 等待 polling task 进入 ready 状态。
6. 更新 channel health。

### 4.4 Polling

Polling request：

```python
getUpdates(
    offset=offset,
    limit=100,
    timeout=50,
    allowed_updates=["message", "callback_query"],
)
```

处理循环：

```text
message update
    -> validate identity
    -> filter current bot top-level message
    -> build zero, one, or two inbound messages
    -> enqueue inbound messages

callback_query update
    -> resolve pending approval
    -> answer callback query

update completed
    -> offset = update_id + 1
```

Transport reconnect 使用 bounded exponential backoff。

`receive()` 持续读取 inbound queue；callback query 只进入 approval path。

### 4.5 Current bot filtering

对顶层 `Update.message`：

```python
sender = message.get("from")

if isinstance(sender, Mapping) and sender.get("id") == self._bot_id:
    return
```

该判断只作用于当前 update 的顶层 message。

`reply_to_message` 中 sender 为当前 bot 时：

```text
保留 quoted message
计算 reply-to-bot activation
建立本地 reply reference
```

### 4.6 Stream events

```python
def offer_stream_event(self, event: StreamEvent) -> None:
    return None
```

Channel contract 和 runtime stream pipeline 保持现状。

## 5. Telegram Thread Identity

### 5.1 Provider thread ID

使用：

```text
telegram:<bot-id>:<chat-id>:<topic-id>
```

其中：

```text
bot-id:
    getMe 返回的当前 bot id

chat-id:
    Message.chat.id

topic-id:
    Message.message_thread_id
    默认 thread 使用 0
```

示例：

```text
telegram:123456789:99887766:0
telegram:123456789:-1001122334455:42
```

### 5.2 Target kind

```text
private:
    DM

group:
    GROUP

supergroup:
    GROUP
```

Telegram 的 `message_thread_id` 与 `sendRichMessage.message_thread_id` 使用同一个 topic
路由值。

### 5.3 Local IDs

```text
thread identity:
    telegram:bot:<bot-id>:chat:<chat-id>:topic:<topic-id>

channel_session_id:
    uuid5(NAMESPACE_URL, thread identity)

bcn_session_id:
    uuid5(NAMESPACE_URL, "bcn:" + thread identity)

message identity:
    telegram:bot:<bot-id>:chat:<chat-id>:message:<message-id>

message_id:
    uuid5(NAMESPACE_URL, message identity)

provider_message_id:
    Telegram Message.message_id converted to text
```

### 5.4 Outbound route parsing

`TelegramChannel.send()` 与 `request_approval()` 从 `provider_thread_id` 解析：

```text
bot id
chat id
topic id
```

Embedded bot ID 与当前 `getMe` bot ID 一致后，构造 Telegram request。

## 6. Inbound Message Mapping

### 6.1 Sender

优先级：

```python
if message.from exists:
    sender = str(message.from.id)
elif message.sender_chat exists:
    sender = str(message.sender_chat.id)
else:
    sender = None
```

人类与其他 bot 均使用该 sender 字段。

Metadata 可以记录：

```text
sender_is_bot
sender_chat
chat_type
telegram_update_id
telegram_topic_id
```

Routing、following 和 runtime notice 只依赖 sender ID、activation 和现有 core 状态。

### 6.2 Body

正文来源：

```text
text
caption
rich_message
```

Rich Message 遍历产生 canonical Markdown：

```text
paragraph
heading
preformatted block
list
blockquote
table
details
rich text formatting
```

同一遍历同时提取 rich mention 和 media references。

### 6.3 Explicit mention

Text/caption entities：

```text
mention:
    entity text matches current bot username

text_mention:
    entity user id matches current bot id

bot_command:
    command target matches current bot username
```

Entity offset 与 length 按 UTF-16 code units 解析。

Rich Message mention：

```text
RichTextMention
RichTextTextMention
RichTextBotCommand
```

### 6.4 Reply activation

```python
reply = message.get("reply_to_message")

reply_to_current_bot = (
    isinstance(reply, Mapping)
    and isinstance(reply.get("from"), Mapping)
    and reply["from"].get("id") == self._bot_id
)
```

Activation：

```python
mentions_agent = explicit_mention or reply_to_current_bot
```

现有 core 根据 `mentions_agent` 完成：

```text
new group thread following
unfollowed group thread refollow
runtime notification
```

### 6.5 Startup cutoff

```python
historical = message["date"] < self._started_at_s
activation = explicit_mention or reply_to_current_bot

notifies_runtime = (not historical) or activation
provider_time_ms = message["date"] * 1000
```

行为矩阵：

| Message | Activation | Adapter notification |
|---|---:|---:|
| 启动前普通消息 | false | false |
| 启动前 mention | true | true |
| 启动前回复当前 bot | true | true |
| 启动后普通消息 | false | true |
| 启动后 mention | true | true |
| 启动后回复当前 bot | true | true |

Group 的最终 notification 继续由 core 的 following 表达式决定。

### 6.6 Reply backfill

对 B 的 `reply_to_message=A`：

```text
1. 构造 A 的 deterministic local message id
2. 构造 A inbound
3. A.mentions_agent = false
4. A.notifies_runtime = false
5. enqueue A
6. 构造 B
7. B.reply_to_message_id = A.message_id
8. enqueue B
```

A 使用真实 Telegram provider message ID。

同一个 A 再次出现在其他 reply update 时，deterministic ID 和现有 provider-message
Deduplication 保持幂等。

### 6.7 Attachments

Inbound 支持：

```text
photo
document
video
animation
audio
voice
video_note
rich message media
```

流程：

```text
select Telegram file id
    -> getFile
    -> stream download
    -> ChannelContext.attachments.materialize
    -> InboundAttachment
```

Photo 选择最大尺寸。

Quoted message 使用同一附件处理逻辑。

## 7. Telegram Inline Keyboard Approval

### 7.1 Approval prompt

`TelegramChannel.request_approval()`：

1. 解析 `ChannelApprovalRequest.provider_thread_id`。
2. 生成随机 callback token。
3. 创建 pending approval。
4. 调用 `sendRichMessage`。
5. 在同一 chat/topic 中回复触发当前 turn 的消息。
6. 等待 callback 或 timeout。
7. 返回 `ApprovalResult`。

Prompt：

```markdown
## Approval required

**Action:** command execution

<provider-neutral description>
```

Inline keyboard：

```text
[Approve] [Reject]
```

### 7.2 Pending approval state

```python
@dataclass(slots=True)
class _PendingApproval:
    request_id: str
    token: str
    chat_id: int
    topic_id: int
    prompt_message_id: int
    expected_sender_id: str | None
    future: asyncio.Future[ApprovalResult]
```

Maps：

```text
token -> pending approval
request id -> token
```

Callback data 仅包含：

```text
bcn:approve:<opaque-token>
bcn:reject:<opaque-token>
```

Provider/runtime identifiers 保留在内存 map 中。

### 7.3 Callback validation

Callback handler 校验：

```text
token exists
pending future is active
callback message belongs to expected chat
callback message belongs to expected topic
callback sender id matches provider_sender_id
decision is approve or reject
```

首个有效 callback 完成 future。

每个 callback 调用 `answerCallbackQuery`，使 Telegram client 结束按钮进度状态。

结果：

```text
approve:
    ApprovalDecision.APPROVED

reject:
    ApprovalDecision.REJECTED

timeout:
    ApprovalDecision.REJECTED
    reason = approval_timeout
```

Resolved token 的后续 callback 返回 resolved notification。

### 7.4 Lifecycle

Channel stop 时完成所有 pending approval future，并清理 maps。

Polling reconnect 不影响 pending approval，因为 callback token 与 future 保存在
TelegramChannel 实例中。

## 8. Rich Markdown Outbound

### 8.1 Text delivery

正文 payload：

```json
{
  "chat_id": 123,
  "message_thread_id": 42,
  "rich_message": {
    "markdown": "final response"
  }
}
```

Reply：

```json
{
  "reply_parameters": {
    "message_id": 456
  }
}
```

### 8.2 Markdown splitting

Telegram Markdown splitter 按 block boundary 处理：

```text
paragraph
heading
list
blockquote
table
details
fenced code
```

处理步骤：

1. 解析 block boundary。
2. 计算每个 part 的 provider limits。
3. 优先保持完整 block。
4. 对超长 fenced code 生成多个闭合 fence。
5. 发送前完成全部 part preflight。
6. 按顺序调用 `sendRichMessage`。

第一条 visible part 携带 reply parameters。

### 8.3 Formatting fallback

Telegram 明确返回 Rich Markdown formatting rejection 时：

```text
markdown part
    -> convert to plain InputRichMessage blocks
    -> sendRichMessage
```

Fallback receipt 记录：

```text
part ordinal
format = blocks
provider message id
```

### 8.4 Attachments

Outbound attachment：

```text
resolve workspace file
    -> provider size preflight
    -> sendDocument multipart
    -> provider Message receipt
```

顺序：

```text
Rich Markdown part(s)
document part(s)
```

第一条 visible part 使用 reply parameters。

所有 part 使用相同 chat/topic route。

### 8.5 Delivery receipt

单 part：

```text
provider_message_id
```

Multi-part receipt：

```text
total_parts
confirmed_parts
parts:
    ordinal
    kind
    provider_message_id
    format
```

Provider outcome 映射：

```text
all confirmed:
    CONFIRMED

confirmed prefix + later failure:
    PARTIAL

uncertain provider outcome:
    UNKNOWN

explicit provider rejection before confirmation:
    FAILED
```

## 9. 实施顺序

### Phase 1：Channel extension contract

目标：固定 builder、generic configuration 和 approval routing。

#### Task 1A：`IChannelBuilder` 与 `WeComBuilder`

修改：

```text
src/bazaar_compute_node/core/channel.py
src/bazaar_compute_node/app/registry.py
src/bazaar_compute_node/app/application.py
src/bazaar_compute_node/contrib/wecom/plugin.py
pyproject.toml
tests/app/test_registry.py
tests/app/test_composition.py
tests/contrib/test_wecom.py
```

实施：

- 增加 `IChannelBuilder`。
- Registry 加载 builder object。
- `AdapterFactories.channel` 使用 builder。
- `NodeApplication` 调用 `builder.build(ChannelContext)`。
- 实现 `WeComBuilder`。
- WeCom entry point 指向 `builder`。
- Builder 读取已知配置字段并构造 `WeComChannel`。
- 更新 test builders。

Focused tests：

- builder entry point load；
- missing build method；
- `ChannelContext` 传递；
- WeCom bot ID；
- WeCom websocket URL；
- WeCom secret；
- extra options；
- NodeApplication lifecycle composition。

完成条件：

```text
focused pytest
ruff check
ruff format --check
pyright
python -m compileall
LSP diagnostics clean
```

发送业务 diff并停在 review。

依赖：本计划 review。  
产出：Channel builder contract。

#### Task 1B：Generic Channel configuration

修改：

```text
src/bazaar_compute_node/app/config.py
src/bazaar_compute_node/cli.py
tests/test_cli.py
README.md
```

实施：

- `NodeConfiguration` 保存 channel option tables。
- TOML loader 读取 `[channel.<name>]`。
- CLI 选择当前 channel section。
- NodeApplication 收到 raw selected options。
- Daemon child 复用 config path。
- README 更新 generic channel configuration。

Focused tests：

- multiple channel sections；
- selected channel lookup；
- CLI channel override；
- empty section；
- nested TOML-native values；
- extra option preservation；
- daemon configuration parity；
- runtime/storage/audit precedence。

完成条件：focused checks 和 LSP 通过，发送业务 diff并停在 review。

依赖：Task 1A。  
产出：provider-owned Channel configuration。

#### Task 1C：Channel approval routing

修改：

```text
src/bazaar_compute_node/core/channel.py
src/bazaar_compute_node/core/models/entities.py
src/bazaar_compute_node/core/orchestration/turn.py
src/bazaar_compute_node/contrib/codex/approval.py
src/bazaar_compute_node/contrib/wecom/channel.py
tests/core/
tests/contrib/test_codex.py
tests/contrib/test_orchestration.py
tests/support/src/bcn_test_support/channel.py
```

实施：

- 增加 `ChannelApprovalRequest`。
- `IChannel.request_approval` 使用 routing wrapper。
- `ApprovalRequest` 增加 description。
- Coordinator 传递 thread、reply message 和 sender。
- Codex adapter 构造 command/file/permission description。
- WeCom 和 TestChannel 适配新签名。
- Approval audit correlation 保持 request identity。

Focused tests：

- thread route；
- reply route；
- sender route；
- request correlation；
- description rendering；
- command approval；
- file approval；
- permission approval；
- WeCom decision；
- timeout and exception propagation。

完成条件：focused checks 和 LSP 通过，发送业务 diff并停在 review。

依赖：Task 1B。  
产出：Channel 可渲染的 approval contract。

Phase 验收：

```text
generic config
    -> selected builder
    -> configured channel

runtime approval
    -> ChannelApprovalRequest
    -> channel receives current route and sender
```

### Phase 2：Telegram ingress

目标：实现 Telegram builder、Bot API client、polling、DM/group/topic、activation 和 reply。

#### Task 2A：TelegramBuilder、API client 与 polling lifecycle

修改：

```text
pyproject.toml
src/bazaar_compute_node/contrib/telegram/__init__.py
src/bazaar_compute_node/contrib/telegram/plugin.py
src/bazaar_compute_node/contrib/telegram/api.py
src/bazaar_compute_node/contrib/telegram/channel.py
tests/contrib/test_telegram.py
uv.lock
```

实施：

- 注册 Telegram builder。
- 实现 `TelegramBuilder`。
- 读取 bot token。
- 实现 `getMe`。
- 实现 `getUpdates`。
- 固定 50 秒 long polling。
- 订阅 message 和 callback_query。
- 实现 process-local offset。
- 实现 transport reconnect。
- 实现 receive queue。
- 实现 lifecycle health。
- 实现 `offer_stream_event` immediate return。

Focused tests：

- entry point；
- token；
- getMe；
- fixed polling request；
- message update；
- callback update dispatch；
- offset progression；
- reconnect；
- cancellation；
- health；
- token redaction；
- current bot top-level filter。

完成条件：focused checks、lock verification 和 LSP 通过，发送业务 diff并停在 review。

依赖：Phase 1。  
产出：Telegram Channel lifecycle。

#### Task 2B：Thread identity、activation 与 reply backfill

修改：

```text
src/bazaar_compute_node/contrib/telegram/identity.py
src/bazaar_compute_node/contrib/telegram/channel.py
tests/contrib/test_telegram.py
tests/contrib/test_orchestration.py
```

实施：

- provider thread serialization/parsing；
- DM/group/supergroup target kind；
- default topic 与 forum topic；
- deterministic session/message IDs；
- generic sender；
- UTF-16 entity parsing；
- mention detection；
- reply-to-current-bot activation；
- startup cutoff；
- quoted message sequencing；
- reply reference；
- current bot top-level filtering；
- quoted current bot preservation。

Focused tests：

- DM；
- group default topic；
- multiple forum topics；
- deterministic identity；
- human sender；
- other bot sender；
- current bot top-level message；
- current bot quoted message；
- mention；
- text mention；
- targeted command；
- emoji UTF-16 offset；
- reply current bot；
- startup boundary；
- following/refollow；
- A-before-membership/B-replies-A；
- duplicate quote backfill；
- referenced message read/check。

完成条件：focused checks 和 LSP 通过，发送业务 diff并停在 review。

依赖：Task 2A。  
产出：Telegram inbox semantics。

#### Task 2C：Rich inbound 与 attachments

修改：

```text
src/bazaar_compute_node/contrib/telegram/markdown.py
src/bazaar_compute_node/contrib/telegram/attachments.py
src/bazaar_compute_node/contrib/telegram/channel.py
tests/contrib/test_telegram.py
```

实施：

- Rich Message traversal；
- canonical Markdown；
- rich mention；
- media extraction；
- `getFile`；
- streaming download；
- shared attachment materializer；
- quoted attachment processing；
- failed attachment descriptor。

Focused tests：

- paragraphs/headings/lists/tables/details；
- nested rich text；
- rich mention；
- document/photo/video/audio/voice；
- largest photo；
- file name；
- file size；
- download stream；
- download failure；
- quoted media；
- text + media。

完成条件：focused checks 和 LSP 通过，发送业务 diff并停在 review。

依赖：Task 2B。  
产出：完整 Telegram inbound content。

Phase 验收：

```text
DM -> runtime notice
new group topic ordinary -> durable silent history
group mention -> following + runtime
unfollow -> ordinary silent -> reply bot refollow
startup backlog ordinary -> durable silent history
startup backlog activation -> runtime
reply to pre-membership message -> quoted history + reply edge
```

### Phase 3：Approval 与 outbound

目标：实现 Telegram inline approval、Rich Markdown final response 和 attachments。

#### Task 3A：Inline keyboard approval

修改：

```text
src/bazaar_compute_node/contrib/telegram/api.py
src/bazaar_compute_node/contrib/telegram/channel.py
tests/contrib/test_telegram.py
tests/contrib/test_orchestration.py
```

实施：

- `sendRichMessage` transport；
- inline keyboard prompt；
- callback token；
- pending approval maps；
- callback route validation；
- callback sender validation；
- `answerCallbackQuery`；
- approve/reject result；
- timeout result；
- stop cleanup；
- audit correlation verification。

Focused tests：

- DM approval；
- group topic approval；
- reply-to-trigger-message；
- command description；
- file description；
- permission description；
- triggering sender approve；
- different sender callback；
- approve；
- reject；
- timeout；
- duplicate callback；
- stale callback；
- reconnect while pending；
- channel stop while pending。

完成条件：focused checks 和 LSP 通过，发送业务 diff并停在 review。

依赖：Phase 2。  
产出：Telegram human approval flow。

#### Task 3B：Rich Markdown outbound

修改：

```text
src/bazaar_compute_node/contrib/telegram/markdown.py
src/bazaar_compute_node/contrib/telegram/outbound.py
src/bazaar_compute_node/contrib/telegram/channel.py
tests/contrib/test_telegram.py
tests/contrib/test_orchestration.py
```

实施：

- Rich Markdown payload；
- topic route；
- reply parameters；
- block-aware splitting；
- fenced code splitting；
- formatting fallback；
- provider receipts；
- partial and unknown outcomes；
- first visible part reply behavior。

Focused tests：

- DM send；
- group default topic；
- forum topic；
- direct reply；
- Markdown formatting；
- CJK/emoji；
- long body；
- fenced code；
- table/details；
- formatting fallback；
- confirmed；
- partial；
- unknown；
- receipt ordering。

完成条件：focused checks 和 LSP 通过，发送业务 diff并停在 review。

依赖：Task 3A。  
产出：Telegram final text delivery。

#### Task 3C：Outbound attachments 与 end-to-end verification

修改：

```text
src/bazaar_compute_node/contrib/telegram/attachments.py
src/bazaar_compute_node/contrib/telegram/outbound.py
src/bazaar_compute_node/contrib/telegram/channel.py
README.md
tests/contrib/test_telegram.py
tests/e2e/test_telegram.py
```

实施：

- multipart document send；
- body + attachment ordering；
- attachment-only delivery；
- topic/reply propagation；
- deadline accounting；
- retry_after；
- multi-part receipt；
- README Telegram setup；
- real provider DM/group/topic/approval scenarios。

Focused and final checks：

```text
focused pytest
full pytest
ruff check
ruff format --check
pyright
python -m compileall
uv lock verification
git diff --check
all modified Python files: LSP diagnostics clean
```

完成条件：

- 提供业务 diff；
- 提供测试终态；
- 提供真实 Telegram DM、topic、reply、approval 和 Rich Markdown 证据；
- 停在 review。

依赖：Task 3B。  
产出：完整 Telegram Channel review candidate。

## 10. 验证原则

- Builder tests 从 entry point 和 `NodeApplication` composition 进入。
- Config tests 使用真实 TOML。
- Telegram transport tests 使用 local `aiohttp.web` server。
- Inbound tests 从真实 Update JSON 进入 `receive()`。
- Following tests 通过 `SessionOrchestrator` 和 storage port 验证。
- Reply tests 同时验证 quoted history、current message 和 referenced lookup。
- Approval tests 通过 runtime approval handler、poller callback 和 Telegram API request 完成。
- Sender tests 覆盖 human、other bot 和 current bot。
- Rich Markdown tests 验证最终 provider payload。
- Attachment tests 使用真实临时 workspace 与 shared materializer。
- 每个 Task 完成后运行 focused tests、Ruff、Pyright、compileall 和 LSP。
- 每个 Task 完成后发送业务 diff并等待 review。

## 11. 关键风险与处理

1. **Builder 与 app 配置耦合**  
   App 只选择 channel section；Builder 解释字段。

2. **当前 bot 消息闭环**  
   顶层 `from.id == bot_id` 在 polling ingress 过滤；quoted message 保留。

3. **Topic 路由碰撞**  
   Provider thread ID 同时包含 bot、chat 和 topic。

4. **Telegram message ID scope**  
   Local message identity 同时包含 bot 和 chat。

5. **UTF-16 entities**  
   Mention extraction 使用 UTF-16 offset conversion 和 emoji golden tests。

6. **Quoted history 重复**  
   Deterministic message ID 与 provider deduplication 保持幂等。

7. **Activation 与 quote 顺序**  
   Reply-to-bot activation 从 provider quoted sender 计算，再构造 synthetic history。

8. **Approval actor**  
   Pending token 绑定 chat、topic、triggering sender 和 request ID。

9. **Approval callback 状态**  
   First valid callback 完成 future；后续 callback 读取 resolved 状态。

10. **Approval timeout**  
    Timeout 产生明确 rejected result 并清理 pending state。

11. **Long-poll cancellation**  
    Channel stop 取消当前 HTTP wait 并关闭共享 session。

12. **Token redaction**  
    API client 日志和异常只记录 method 与 bounded provider error。

13. **Rich Markdown split**  
    全部 part 在首次发送前完成结构和 limit preflight。

14. **Multi-part delivery**  
    Receipt 记录每个 confirmed part，terminal outcome 反映 partial 或 unknown。

15. **Attachment streaming**  
    Provider metadata preflight 与 shared materializer 共同控制大小和路径。

## 12. Code 模式入口条件

Review 通过后从 **Phase 1 Task 1A** 开始。

Task 顺序：

```text
1A IChannelBuilder + WeComBuilder
1B Generic Channel configuration
1C Channel approval routing
2A Telegram lifecycle
2B Telegram thread and reply semantics
2C Rich inbound and attachments
3A Inline keyboard approval
3B Rich Markdown outbound
3C Attachments and end-to-end verification
```

每个 Task 完成后执行 focused checks、发送业务 diff并停在 review。
