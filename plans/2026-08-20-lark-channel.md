# 2026-08-20 Lark Channel Plan

## 状态

- 模式：Plan。
- 状态：调研完成，已按 Plan review 补充 identity 与通讯录 display-name；尚未修改生产代码、测试、依赖或 lock。
- 工作分支：`feature/lark-channel`。
- 基线：`origin/main@9e84701`，对应 `v0.1.17`。
- 当前分支只新增本文；每个 Task 串行开发，完成 focused verification 后停下 review。
- commit、push、PR、merge、发布、部署与真实飞书应用配置均不在当前授权范围。

## 1. 目标

在现有 provider-neutral Channel、durable inbox、following、reply reference、attachment、
approval、runtime stream event 与 outbound delivery contract 基础上增加 Lark / 飞书
Channel adapter。

首版完整链路为：

```text
bcn selects channel=lark
    -> LarkBuilder reads app configuration and secret
    -> LarkChannel starts the provider long-connection protocol
    -> im.message.receive_v1 enters the adapter
    -> adapter maps provider identity, body, mention, reply, and resources
    -> existing durable inbox and following policy decide runtime notification
    -> runtime reads messages through bcc and performs one turn
    -> outbound is sent or replied to the original chat/thread
    -> provider receipt is mapped to BCN delivery outcome
```

产品语义：

1. Channel name 和 entry point 固定为 `lark`；同一 adapter 支持中国飞书和国际 Lark。
2. 首版使用飞书官方长连接接收事件，不增加公网 HTTP webhook server。
3. App ID 是非敏感配置；App Secret 只从配置指定的环境变量读取。
4. DM 使用 `ChannelTargetKind.DM`；普通群、话题群与消息线程使用
   `ChannelTargetKind.GROUP`。
5. provider thread route 同时包含当前 bot、chat 与 thread identity；切换凭据后不能复用旧
   route 误投递。
6. sender authorization 使用 provider `open_id`；可读 name 仅用于显示，不参与 route、
   dedupe 或 approval authorization。
7. 与 WeCom 对齐，adapter 按最大投递模型处理所有已收到的群消息，每条群消息
   都具有相同的 activation 语义。
8. 现有 core 继续拥有 durable dedupe、following、cursor、unread、fresh-check 和 delivery
   state；provider transport 不得先行接管这些语义。
9. 首版 approval 使用飞书交互式消息卡片，完整对齐 Telegram 的 approve/reject、
   sender authorization、correlation 与 first-writer-wins 语义。
10. runtime stream 正文不直接发送到飞书；活跃 stream 可映射为触发消息上的 `Typing`
    reaction，terminal event 到达后移除。
11. 通讯录 display name 只作为可选展示 enrich，失败不影响消息入站。

## 2. 已确认边界

- 当前 `IChannel` 已覆盖 lifecycle、identity、receive、send、approval 与 runtime stream
  event，不需要新增 Lark-specific core method。
- 当前 `ChannelContext` 已提供 options、workspace 与 attachment materializer，不需要新增
  provider URL、transport object 或 token 字段。
- 当前 `InboundMessage` 已覆盖 sender、mention、provider time、reply、attachment 与 provider
  metadata，不需要 schema migration。
- 当前 `ChannelSendRequest` 已包含 provider thread 和 provider reply message ID，可以分别
  映射主动发送和 reply API。
- 当前 `AgentScopedChannel` 已按 Agent namespace 转换 channel/session ID；Lark adapter 只生成
  provider-local deterministic identity。
- generic `agent.channel` options 可以承载 `app_id`、`app_secret_env` 与 `region`，不需要修改
  config model。
- `pyproject.toml` 注册新的 entry point 并增加 `protobug==1.0.0`；`uv.lock`
  记录对应 dependency resolution。

## 3. 协议实现

### 3.1 协议基线

实现契约以飞书开放平台正式文档与实际 protocol/API response 为基线。
固定参考：

- 接收事件与长连接：
  <https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case>
- 卡片交互：
  <https://open.feishu.cn/document/common-capabilities/message-card/add-card-interaction/interaction-module>

reconnect 次数、heartbeat interval、fragment expiry 与 event retry 作为 client protocol
parameter 实现和测试；Plan 中的 deadline 都是 BCN 本地 contract。

### 3.2 Async transport

`contrib/lark/transport.py` 使用项目现有 `aiohttp`、标准库与
[`protobug==1.0.0`](https://github.com/yt-dlp/protobug) 实现长连接：

- 用已有 `aiohttp.ClientSession` 请求 `/callback/ws/endpoint` 并建立 `wss` connection；
- 在 BCN 现有 asyncio loop 内完成 receive、heartbeat、ack、reconnect 和 stop，不引入
  thread 或 nested loop；
- `frame.py` 用 `@protobug.message` 声明 `Header` 与 `Frame` 的 field number/type；
- 实现有界 fragment reassembly，不引入 provider 高层 event dispatcher；
- event payload 使用标准库 JSON 解析，ack 从已校验的 frame identity、headers 和
  response payload 重新构造；
- reconnect/heartbeat 参数接受 provider 下发值，但先做类型和安全上限校验。

`ClientConfig` 数值契约固定为：`PingInterval` 与 `ReconnectInterval` 默认
120 秒，`ReconnectNonce` 默认 30 秒，三者接受
`1..86400`；`ReconnectCount` 默认 `-1`，接受 `-1..10000`。fragment reassembly
最多同时保留 128 个 message、每个 message 64 片 / 8 MiB，expiry 为 5 秒；
WebSocket binary message 上限为 8 MiB。

`protobug` 解码时跳过 unknown fields；ack 只编码第 12 节列出的已知 schema。
checked-in golden binary fixture 验证 encode/decode 互操作。

### 3.3 HTTP OpenAPI client

token、bot info、send/reply、resource download 与 upload 使用 `contrib/lark` 内基于现有
`aiohttp` 的薄 client：

- application tenant token 在 adapter 内存中按 provider expiry 提前刷新；
- concurrent refresh 使用单一 lock，失败不缓存空 token；
- HTTP timeout 使用调用 deadline，不使用无界默认值；
- 非 2xx、provider code、rate limit、timeout、disconnect 与 malformed response 保留不同分类；
- 不在 transport 内自动重发可能已经到达 provider 的 request；
- App Secret、tenant token、dynamic WS URL、`access_key`、`ticket` 与 authorization header 不
  进入 exception、audit、health、SQLite 或 Runtime environment。

## 4. 配置与 composition

### 4.1 Entry point

新增 Channel entry point：

```toml
lark = "bazaar_compute_node.contrib.lark.plugin:builder"
```

`LarkBuilder` 只读取当前 `ChannelContext.options`，不根据其他 Channel 或 Runtime 分支。

### 4.2 配置字段

首版字段：

| 字段 | 必填 | 语义 |
| --- | --- | --- |
| `app_id` | 是 | 飞书/Lark 应用 ID，非 secret |
| `app_secret_env` | 是 | 保存 App Secret 的环境变量名 |
| `region` | 否 | `feishu` 或 `lark`，默认 `feishu` |

约束：

- `app_id`、`app_secret_env` 必须是非空无换行文本；
- `app_secret_env` 的环境变量必须在 build 时存在且值非空；
- `region` 只允许固定枚举，不能接受 arbitrary URL；
- 未选择 `lark` 时不读取配置或凭据；
- options 中未知字段沿用当前 builder 规则保持无操作，不在 app config 增加 provider-specific
  校验；
- CLI 示例使用 `bcn agent add --channel lark` 与 `--set` 添加字段，不增加专用 CLI flags。

### 4.3 Credential boundary

App Secret 只由 builder 与 Lark adapter 持有。Runtime subprocess 仍按现有封闭环境构造，不
自动继承 secret。operator 若把同一环境变量显式加入 runtime `env_include`，属于现有显式
授权语义，本 Task 不修改该规则。

adapter 必须执行结构性泄密检查：

- dynamic WebSocket URL 不作为日志参数，只记录校验后的 host 与 connection state；
- log filter 对 `access_key`、`ticket`、App Secret、tenant token 和 authorization value 脱敏；
- exception 只保留 method、HTTP status、provider code 与 bounded safe message；
- health 只包含 state、counter、timestamp、error kind 与 bot non-secret identity；
- SQLite metadata 不保存 raw event、token、resource download URL 或 card callback token；
- Runtime environment、bcc wrapper 与 developer instruction 不包含任何 Lark credential。

## 5. Lifecycle、health 与可观测性

### 5.1 Start

`LarkChannel.start()`：

1. 校验尚无 active lifecycle task；
2. 创建 application-scoped `aiohttp.ClientSession`；
3. 获取 tenant token 并调用 bot info，保存 bot `open_id` 与可选 name；
4. 创建 transport receive/reconnect task；
5. 在当前 asyncio loop 中建立长连接；
6. 等待明确 ready signal；
7. 在 startup deadline 内失败时关闭 HTTP session、connection 与所有 lifecycle task；
8. 只有上述步骤完成后暴露 connected state。

启动不依赖首条业务 event。authentication failure 是 clear startup failure；network failure 做
有界 backoff，不能让 `start(timeout=...)` 无限等待。

### 5.2 Reconnect

- transport close 后进入 reconnecting；
- reconnect 期间 outbound 在 send deadline 内 fail clear `channel_unavailable`，不在 adapter
  内无界排队；
- provider connection limit/authentication failure 与 transient network failure 使用不同 health
  kind；
- provider 未提供 replay cursor 或补发保证时，不宣称能恢复断线窗口消息；
- reconnect event 可能重投同一 `message_id`，最终仍由 BCN durable dedupe 保证幂等；
- fragment cache 与 connection state 只在 transport instance 内存在，并受严格的数量、字节
  和 expiry 上限约束。

### 5.3 Stop

- stop 是幂等的；
- 停止新 event handoff，再终止 long connection；
- 等待已经进入 BCN loop 的 inbound mapping/attachment task 到 terminal state；
- 取消 typing leases 与 pending HTTP calls；
- 完成或拒绝所有 pending approval；
- 关闭 `aiohttp.ClientSession`；
- lifecycle task 在 timeout 内未退出时记录 degraded shutdown 并完成 bounded teardown；
- 最后向 inbound queue 写一次 sentinel。

### 5.4 Health

至少暴露：

```text
state
region
bot_open_id
bot_name
connection_generation
connected_at_ms
last_event_at_ms
last_disconnect_kind
events_received
messages_queued
messages_filtered
message_mapping_failures
last_message_disposition
last_message_filter_reason
token_refresh_failures
typing_failures
```

audit event 不记录正文或 raw payload，至少覆盖 connection state、event received、message
queued/filtered、resource materialized/failed、outbound outcome 与 approval decision。

## 6. Bot、sender 与 thread identity

### 6.1 Channel identity

启动后通过 `GET /open-apis/bot/v3/info` 获取：

- `ChannelIdentity.id = bot.open_id`；
- `ChannelIdentity.name = bot.app_name`，兼容性 fallback 为 `bot.name`，再缺失时只返回 ID；
- start 前和 stop 后返回 `None`；
- app ID 不替代 bot open_id；
- provider display name 不参与 route、dedupe 或 authorization；
- generic developer instruction 继续把 provider identity 作为 bot name、把配置中的 agent name
  作为 A.K.A. identity，两者不混用。

### 6.2 Sender identity

inbound sender 使用 provider event 的 sender ID：

- `SenderIdentity.id = open_id`；
- `SenderIdentity.name` 可通过通讯录 best-effort enrich；成功时读取
  `GET /open-apis/contact/v3/users/:user_id?user_id_type=open_id` 的 `data.user.name`，
  再按需 fallback 到 `en_name`/`nickname`；
- 只对 `sender_type=user` 查询通讯录，bot/app/system sender 直接保留 `open_id`；
- 通讯录无权限或用户不可见时静默保留 `name = None`，不阻塞、重试或拒绝当前 event；错误按
  HTTP status 与 provider business code（例如 `41050`）联合分类；
- 结果使用进程内有界内存缓存，key 为 `app_id + tenant_key + sender_open_id`，成功和负结果
  都设置 TTL，并合并同 key 的并发查询；不持久化 raw contact response；
- event 缺少非空 `open_id` 时记录 `invalid_sender` disposition 并结束该 event；
- app/bot 自己发送的 event 按 bot open_id 过滤；
- sender type 可进入 bounded metadata 用于诊断，但不改变 core contract；display name 不参与
  route、dedupe、authorization 或 approval sender 校验。

### 6.3 Provider thread identity

canonical form：

```text
lark:bot:<bot-open-id>:chat:<chat-id>:thread:<thread-id-or-zero>
```

三个 provider ID segment 都使用 UTF-8 percent-encoding，`urllib.parse.quote(value, safe="")`
生成 uppercase percent escape，parser 使用 `unquote_to_bytes()` 后严格 UTF-8 decode，并要求
decode 后再 encode 完全等于原 segment，从而保证 canonical spelling。

普通 p2p 和普通群消息的 thread 使用 `0`；event `thread_id` 存在时使用该值隔离
话题/线程。`root_id` 表示对话根消息，`parent_id` 表示直接父消息，两者均不参与
provider thread route。

不变量：

- 同一 chat 的不同 topic/thread 产生不同 channel session；
- 同一 thread 的多级 reply 保持同一 channel session；
- `reply_to_message_id` 由 `parent_id` 生成，指向直接 parent 的本地 deterministic
  message ID；
- `root_id` 或 thread identity 只用于 route，不冒充直接 reply target；
- provider route parser 必须校验嵌入的 bot open_id 等于当前 identity；
- decode 后的 ID 必须为非空无换行文本，`thread=0` 只表示主会话。

### 6.4 Local deterministic IDs

沿用 Telegram 模式：

- `channel_session_id = uuid5(NAMESPACE_URL, provider thread identity)`；
- `session_id = uuid5(NAMESPACE_URL, "bcn:" + provider thread identity)`；
- `message_id = uuid5(NAMESPACE_URL, "lark:bot:<encoded-bot-id>:message:<encoded-message-id>")`；
- provider message ID 原样保存为 `provider_message_id`；
- provider event ID 可以放入 bounded metadata，但不替代 message ID dedupe。

## 7. Inbound mapping

### 7.1 Event admission

只消费 `im.message.receive_v1`。未知 event 计数并忽略，不伪装成用户消息。每条 event 按以下顺序
处理：

1. bounded parse event envelope；
2. 校验 message ID、chat ID、chat type、sender 与 message type；
3. 过滤 current bot 自发消息；
4. 解析 thread/reply identity；
5. 解析正文、mention 与 resource descriptor；
6. 在 BCN loop 下载并 materialize resource；
7. 构造一个 `InboundMessage`；
8. 放入 adapter inbound queue；
9. durable append/dedupe/following 继续由现有 orchestration 完成。

raw payload 不持久化；只保存有明确消费方的 non-sensitive metadata。

### 7.2 Target kind 与 notification

- `p2p` 映射 DM，DM 不依赖 mention；
- group/topic 映射 GROUP；
- 与 WeCom 一致，所有已投递的 group/topic message 都设置 `mentions_agent = true`，
  `p2p` 设置 `false`；
- `notifies_runtime` 统一使用默认通知语义；
- provider create time 明确为毫秒或可可靠转换后写 `provider_time_ms`；无法确认时不猜单位。

### 7.3 Group ingress 边界

`im.message.receive_v1` 投递的每条合法群消息都按完整群消息处理。group/topic
message 统一设置 `mentions_agent = true` 并进入现有 durable inbox 与 notification 流程，
与 WeCom 的最大投递模型保持一致。

### 7.4 Body

首版支持：

- text；
- post/rich text；
- image；
- file；
- audio；
- media/video；
- sticker。

text 中 provider mention placeholder 转成可读 `@name` 或 `@open_id`；移除当前 bot mention 时
只基于结构化 placeholder/mention key，不按渲染后的名称做字符串替换。post 递归遍历官方 AST，
保留标题、段落、链接、代码、列表、引用、图片/resource placeholder 和其他用户 mention。

unsupported type 生成稳定的可读 classification，不丢成空 body；未知字段不使 reader 崩溃。

### 7.5 Reply

event 包含直接 parent 时：

- adapter 使用 `GET /open-apis/im/v1/messages/:parent_id?user_id_type=open_id` 取得父消息；
- 父消息按同一 provider thread identity 映射为 `notifies_runtime = false` 的 quoted backfill，
  其 `reply_to_message_id = None`，先于当前消息入队；
- 当前消息的 `reply_to_message_id` 指向父消息的 deterministic local ID；
- parent fetch 返回 clear failure 或 malformed item 时，当前消息仍入队，
  `reply_to_message_id = None`，metadata 只记录 safe `reply_fetch_failed` disposition；
- outbound reply 使用 `POST /open-apis/im/v1/messages/:message_id/reply`；route 含真实
  `thread_id` 时传 `reply_in_thread = true`，主会话传 `false`。

### 7.6 Attachments

resource download 由 adapter 使用 application token 完成：

- resource key 与 message ID 是 provider request input，不写入日志正文；
- 下载使用 bounded async stream，不先无界读入内存；
- provider content type、filename 与 size 只作为不可信 metadata；
- filename 执行路径分隔符、控制字符、换行与长度校验；
- bytes stream 交给 `ChannelContext.attachments.materialize`；
- materializer 返回的 relative path 才进入 `InboundAttachment`；
- download/provider rejection 生成 failed attachment，普通正文仍可进入 inbox；
- resource side effect 使用有界 5 分钟 / 256 项 attachment-result cache，key 为
  `(provider_message_id, file_key, resource_type)`；并发重投共用同一 in-flight future，已完成
  重投复用同一 `InboundAttachment` descriptor。每个 provider event 仍按原样进入 core，持久化
  幂等仍由 BCN durable dedupe 唯一决定。

## 8. Outbound 与 delivery outcome

### 8.1 Agent 正文

`bcc message send` 提交的文本内容原样进入 Markdown outbound pipeline；不按正文
内容区分 plain text 与 Markdown，只在 provider 单消息上限处做 transport chunking：

- `msg_type = "post"`；
- 以 3500 Unicode code points 为单条 part 上限，发送前完成全部 part preflight；
- 优先在段落、其次在换行边界分割；跨 part 的 fenced code 显式闭合并重开，
  新增的 fence 字符计入 part limit；
- 每个 part 的 `content` 都是 JSON string，解码后固定为
  `{"zh_cn":{"title":"","content":[[{"tag":"md","text":"<markdown part>"}]]}}`；
- 除 transport chunking、fence continuity 与 JSON wire encoding 外，adapter 不解析或重写
  Markdown 语法，不做 structured-node 转换或 text 降级；
- 支持范围、客户端差异与最终显示效果由飞书的 Markdown renderer 决定；
- provider 接受则保留其显示结果，provider rejection 根据 response evidence 映射为
  `FAILED`、`PARTIAL` 或 `UNKNOWN`。

首个 visible part 应用 `provider_reply_to_message_id`；主会话的后续 part 使用 create
endpoint，话题 thread 的后续 part 使用上一个已确认 outbound message ID 作为 reply
anchor 并传 `reply_in_thread = true`。话题 thread request 缺少 reply anchor 时，preflight
返回 `FAILED / missing_thread_anchor`。

### 8.2 Attachments

outbound attachment：

1. 沿用现有 workspace path、symlink、regular file、size 与 digest preflight；
2. image 使用 image upload，其他支持类型使用 file upload；
3. 获得 media key 后发送相应 message；
4. 正文消息在前，attachment part 按 request 顺序发送；
5. upload success 不等于 message delivery success，两者 receipt 分开记录；
6. 任一 unknown 后停止后续 part，不从头重发。

### 8.3 Receipt mapping

- API success 且返回 provider message ID：`CONFIRMED`；
- provider 在发送前或明确非零 code 拒绝且无可能接收：`FAILED`；
- 已确认前缀后遇到 clear failure：`PARTIAL`；
- request write 后 timeout、connection loss、malformed success response 或无法判断 provider 是否
  接收：`UNKNOWN`；
- `PARTIAL` 与 `UNKNOWN` 禁止 adapter 自动重发；
- rate limit 只有明确未接收且 provider contract 允许时才可由调用方后续重试，首版 adapter 不
  内置 retry loop；
- multi-part receipt 保存总 part、已确认前缀与每个 provider message ID 的 bounded JSON ref，
  不保存正文或 token。

## 9. Runtime stream 与 Typing reaction

`LarkChannel` 保存 `session_id -> trigger provider message ID` route：

- inbound enqueue 时建立 session route；
- 首个 `StreamEvent` 为 trigger message 添加 `Typing` reaction；
- 一个 session 同时最多一个 active typing reaction；
- terminal `RuntimeEvent` 移除 reaction 并清理 route；
- reaction API 失败只增加 health counter，不改变 turn 或 outbound outcome；
- stop 清理所有 active reaction；
- 不为每个 delta 创建 task，不发送 stream 正文；
- Typing reaction API 失败时记录 health counter，turn 与 outbound 主链路继续执行。

Task 3 e2e 验证 reaction create response 的 `data.reaction_id`、terminal delete 与重连后清理
符合上述契约。

## 10. Approval

`request_approval()` 创建与 Telegram 一致的 pending state：`request_id`、随机 URL-safe token、
provider thread route、原始 sender `open_id`、prompt card message ID 与 result future。
标题、action label、button、toast 和 feedback 复用 `ChannelContext.translator` 已有的
Telegram approval translation keys。

卡片使用 CardKit 2.0 JSON，通过 `msg_type = interactive` 发送；OpenAPI `content`
字段是下列 card object 的 compact JSON string。正文展示 action 和 description，并提供
approve/reject 两个 button：

```json
{
  "schema": "2.0",
  "config": {"update_multi": true},
  "header": {
    "template": "blue",
    "title": {"tag": "plain_text", "content": "Approval required"}
  },
  "body": {
    "elements": [
      {"tag": "markdown", "content": "<escaped action and description>"},
      {
        "tag": "column_set",
        "columns": [
          {
            "tag": "column",
            "elements": [
              {
                "tag": "button",
                "type": "primary",
                "text": {"tag": "plain_text", "content": "Approve"},
                "value": {"action": "approve", "token": "<opaque-token>"}
              }
            ]
          },
          {
            "tag": "column",
            "elements": [
              {
                "tag": "button",
                "type": "danger",
                "text": {"tag": "plain_text", "content": "Reject"},
                "value": {"action": "reject", "token": "<opaque-token>"}
              }
            ]
          }
        ]
      }
    ]
  }
}
```

transport 将 WebSocket data frame 的 `type = card` 解析为 `card.action.trigger`，使用以下字段：

- `header.event_id` 作为 callback dedupe identity；
- `event.operator.open_id` 必须等于原始 sender；
- `event.context.open_chat_id` 必须等于 pending chat；
- `event.context.open_message_id` 必须等于 prompt card message ID；
- `event.action.tag` 必须为 `button`；
- `event.action.value` 提供 action 和 opaque token。

第一个通过 correlation 的 callback 完成 future，后续 callback 返回已决定 toast。callback
在 3 秒内回写原 `type = card` frame，payload 为 `{"code":200,"data":"<base64>"}`，其中
`data` 是 `{"toast":{"type":"success|warning","content":"..."}}` 的 UTF-8 JSON 后再 base64。决策后使用
reply API 发送审批结果反馈。`event.token` 是 provider 的卡片更新凭证，只存活在
callback handler 内，不参与审批 correlation。

`request_approval(timeout=...)` 用 monotonic deadline 覆盖 prompt send 与 future wait；deadline
到期返回 `ApprovalDecision.REJECTED` / `reason="approval_timeout"`。channel stop 返回
`ApprovalDecision.REJECTED` / `reason="channel_stopped"`。两种结果都记入 256 项
resolved-token state，供重复点击返回明确 toast。

## 11. 文件边界

预计生产业务逻辑：

```text
src/bazaar_compute_node/contrib/lark/
    __init__.py
    plugin.py
    channel.py
    api.py
    transport.py
    frame.py
    identity.py
    attachments.py
    outbound.py
    approval.py
```

具体拆分以职责和复用为准；禁止为只调用一次的片段制造 helper，也禁止增加 normalize 命名或
同等含义的方法。provider frame projection 使用明确的 parse/map 命名。

仓库配套：

```text
pyproject.toml
uv.lock
README.md
CHANGELOG.md
tests/contrib/test_lark.py
tests/e2e/test_lark.py
tests/app/test_registry.py
tests/package_smoke.py
```

文件在对应职责产生实际实现时创建。`pyproject.toml` 与 `uv.lock`
承载 `protobug==1.0.0`；core/app/storage 保持当前内容。

## 12. 串行实施顺序

### Task 1：Composition、frame schema 与 lifecycle

修改范围：

- `pyproject.toml`、`uv.lock`；
- `contrib/lark/plugin.py`、`api.py`、`transport.py`、`frame.py`、`identity.py`、最小
  `channel.py`；
- registry/package smoke 与 focused Lark lifecycle tests；
- README 的实验性配置说明。

实现：

1. 注册 `lark` entry point，增加 `protobug==1.0.0` 并更新 lock；builder 校验
   `app_id`、`app_secret_env`、`region`；`feishu` 域名为 `https://open.feishu.cn`，
   `lark` 域名为 `https://open.larksuite.com`。
2. `frame.py` 用 `@protobug.message` 声明 `Header` 和 `Frame`：
   `SeqID=1:UInt64`、`LogID=2:UInt64`、
   `service=3:Int32`、`method=4:Int32`、`headers=5:list[Header]`、
   `payload_encoding=6:String`、`payload_type=7:String`、`payload=8:Bytes`、
   `LogIDNew=9:String`。`SeqID/LogID/service/method` 和 `Header.key/value` 不设 default；
   其余字段使用 optional/list default。decode 将 malformed/missing-required 统一映射为
   `FrameDecodeError`，跳过 unknown fields；解码后最多接受 64 个 header，key/value 分别
   限制为 64/4096 UTF-8 bytes。checked-in golden binary fixture 验证互操作。
3. `POST /callback/ws/endpoint` 使用 `{"AppID":...,"AppSecret":...}` 获取 `data.URL`
   和 `data.ClientConfig`；从 URL query 取 `service_id` 与 `device_id`，原 URL 只用于
   `aiohttp.ws_connect()`。
4. transport 在当前 asyncio loop 处理 binary frame：`method=0` 为 control，`method=1`
   为 data；header `type`支持 `ping`、`pong`、`event`、`card`；`sum/seq/message_id`
   完成 5 秒 expiry、128-message、64-part/message、8-MiB/message 的有界
   fragment reassembly，`aiohttp.ws_connect(max_msg_size=8 * 1024 * 1024)` 限制单个 frame。
5. heartbeat 发送 `type=ping`、`service=service_id`、`SeqID=0`、`LogID=0` 的 control frame；
   pong payload 更新 `PingInterval`、`ReconnectCount`、`ReconnectInterval`、`ReconnectNonce`；
   interval 默认值为 120/120/30 秒并接受 `1..86400`，reconnect count 默认 `-1`
   并接受 `-1..10000`。
6. event ack 使用已校验的 frame identity/headers 重新构造 frame 并追加 `biz_rt`。
   `event` 先完成
   bounded envelope validation 并进入 256 项 raw-event queue，再回写
   `{"code":200}`；frame/envelope error 或 queue full 回写 `{"code":500}`。单一
   mapper worker 按接收顺序执行 parent fetch、resource materialization 与 inbound enqueue。同一
   path 支持 Task 4 的 card ack data。
7. tenant token 使用 `POST /open-apis/auth/v3/tenant_access_token/internal`，传
   `app_id/app_secret`，读 `tenant_access_token/expire` 并提前 10 分钟刷新；bot identity 使用
   `GET /open-apis/bot/v3/info`，同时支持 `{"bot":...}` 与 `{"data":{"bot":...}}`
   response shape，canonical 读取 `bot.open_id` 和 `bot.app_name`，仅在兼容旧 response 时
   fallback 到 `bot.name`。
8. provider thread identity 固定为
   `lark:bot:<bot-open-id>:chat:<chat-id>:thread:<thread-id-or-zero>`，并生成 deterministic
   channel/session/message IDs。
9. start 在 endpoint、WebSocket、token 和 bot identity 完成后 ready；reconnect 串行化；stop
   先停止 raw-event admission，drain mapper，再关闭 receive/heartbeat/reconnect tasks、WebSocket 和
   `ClientSession`。
10. reconnect 每轮先 sleep `uniform(0, ReconnectNonce)`，每次重新请求 dynamic endpoint；
    transient failure 按 `ReconnectInterval` 间隔执行 `ReconnectCount` 次，`-1` 持续到
    reconnect 成功或 stop；authentication/connection-limit error 直接进入对应 degraded
    health state。
11. health 与 audit 记录 connection generation/counters/error kind，sentinel tests 覆盖 App Secret、
    tenant token、dynamic URL、`access_key` 和 `ticket` 的 credential boundary；package
    smoke 在 Python 3.14 导入 `protobug` 与 Lark entry point，Task 1 门禁包含
    `uv lock --check`。

Task 1 完成后只提交 lifecycle skeleton 与证据，不提前加入完整 inbound/outbound；发送业务 diff
并停下 review。

### Task 2：Inbound、reply 与 attachments

修改范围：

- `contrib/lark/channel.py`、`identity.py`、`attachments.py`；
- Lark contrib tests 与真实 ingress e2e；
- README 的 Lark 配置文档。

实现：

1. 解析 P2 envelope `schema/header/event`，消费 `header.event_type = im.message.receive_v1`；
   从 `event.sender.sender_id.open_id` 取 sender，从 `event.message` 取 `message_id`、
   `root_id`、`parent_id`、`create_time`、`chat_id`、`thread_id`、`chat_type`、
   `message_type`、`content`、`mentions`。
2. `p2p` 映射 DM，`group/topic` 映射 GROUP；所有 GROUP 都设置
   `mentions_agent = true`，p2p 设置 `false`，`notifies_runtime` 使用默认值。
3. 对 text 解析 `content.text`；对 post 递归转换 title/paragraph/text/link/at/image/media
   nodes；image/file/audio/media/sticker 转为正文 placeholder 和 resource descriptor；其他
   `message_type` 转为 stable classification。
4. mention placeholder 使用 event `mentions[].key/id.open_id/name` 渲染，保留人类可读正文；
   activation 仍按第 2 步的 target kind 决定。
5. 对 `sender_type=user` best-effort 查询通讯录 display name；使用
   `GET /open-apis/contact/v3/users/:user_id?user_id_type=open_id`，并按
   `app_id + tenant_key + sender_open_id` 复用有界成功/负缓存与 in-flight 查询。HTTP/provider
   error（包括无权限或用户不可见）只写 safe disposition 并令 `name=None`，当前消息仍正常入队；
   bot/app/system sender 不发起通讯录请求。
6. `parent_id` 存在时调用 `GET /open-apis/im/v1/messages/:parent_id?user_id_type=open_id`，
   将返回的 `data.items[0]` 映射为同 session quoted backfill，再入队当前消息并设置
   `reply_to_message_id`。
7. message resource 使用
   `GET /open-apis/im/v1/messages/:message_id/resources/:file_key?type=<resource_type>` 流式下载，
   `resource_type` 来自已验证的 message type；校验 filename/size/content type 后交给
   `ChannelContext.attachments.materialize`。对同一 resource 使用 5 分钟 / 256 项的
   in-flight/result cache 复用 materialization result。
8. self-sent bot event 根据 bot `open_id` 过滤；provider `message_id` 作为 durable dedupe key；
   health/audit 记录 disposition 和 resource outcome。

Task 2 focused verification 至少覆盖：

- 同一 chat 不同 thread 隔离；
- 同一 thread 多级 reply 不分裂；
- bot identity 使用 `open_id/app_name`，并与配置中的 agent name 正确形成 developer identity；
- sender contact lookup 成功、无权限/用户不可见静默失败、负缓存与并发查询合并；
- sender/display 与 bot identity 不混用；
- mention placeholder 不污染正文；
- duplicate event 只形成一条 durable message；
- resource success/failed descriptor；
- 有/无结构化 @ 的 group fixture 具有相同的 activation 语义。

完成后发送业务 diff并停下 review。

### Task 3：Outbound、attachments、delivery 与 Typing

修改范围：

- `contrib/lark/outbound.py`、`channel.py`、必要的 attachment helper；
- outbound/typing contrib tests 与真实 e2e；
- README outbound 行为文档、CHANGELOG。

实现：

1. `bcc message send` 的每条 text body 原样进入同一 Markdown outbound pipeline：
   以 3500 Unicode code points 为 part limit，优先按段落、其次按换行分割，
   并对跨 part fenced code 做
   close/reopen；辅助 fence 计入 limit。每个 part 放入单个
   `{"tag":"md","text":"<markdown part>"}` node，并编码为
   `{"zh_cn":{"title":"","content":[[{"tag":"md","text":"<markdown part>"}]]}}`
   locale map；不做 syntax conversion 或 text fallback。
2. 主动发送使用 `POST /open-apis/im/v1/messages?receive_id_type=chat_id`，body 为
   `receive_id`、`msg_type`、JSON-string `content`、本地 UUID `uuid`；reply 使用
   `POST /open-apis/im/v1/messages/:message_id/reply` 和 `reply_in_thread`。主会话后续 part 使用
   create，话题后续 part 逐个使用上一 confirmed provider message ID 作为 anchor；话题
   缺少初始 anchor 时在 preflight 返回 `missing_thread_anchor`。
3. image 使用 multipart `POST /open-apis/im/v1/images`，`image_type=message`；其他文件使用
   multipart `POST /open-apis/im/v1/files`，传 `file_type/file_name/file`；返回 key 后发送
   `msg_type=image|file|audio|media`。
4. HTTP 2xx、provider `code=0` 且 `data.message_id` 映射 `CONFIRMED`；request 发送前的
   validation/provider reject 映射 `FAILED`；已确认前缀后 clear failure 映射 `PARTIAL`；
   write 后 timeout/disconnect/malformed success 映射 `UNKNOWN`。
5. Typing 使用 `POST /open-apis/im/v1/messages/:message_id/reactions`，body 为
   `{"reaction_type":{"emoji_type":"Typing"}}`，保存 `data.reaction_id`；terminal event 使用
   `DELETE /open-apis/im/v1/messages/:message_id/reactions/:reaction_id`。

完成 focused tests 后执行全量项目门禁并停下 review。

### Task 4：Interactive approval

修改范围：

- `contrib/lark/approval.py`、`channel.py`、`transport.py`；
- approval/card-frame focused tests 与真实 e2e；
- README 支持矩阵、CHANGELOG。

实现：

1. `request_approval()` 创建 random URL-safe token 和 pending future，保存 request/sender/chat/thread/
   prompt-message correlation。
2. 使用 `interactive` card 发送 action/description 与 approve/reject buttons，button `value`
   只包含 action 和 opaque token，card JSON 固定使用第 10 节的 CardKit 2.0 shape；
   使用 `provider_reply_to_message_id` 调用 reply endpoint，话题 route 传
   `reply_in_thread = true`，并将 response `data.message_id` 写入 pending prompt correlation。
3. transport dispatch `type=card` 的 `card.action.trigger`，校验 event ID、operator open_id、
   open_chat_id、open_message_id、button tag、action 与 token。
   prompt message ID 尚未写入时返回 initializing warning toast，pending future 保持未决定。
4. first valid callback 生成 `ApprovalResult`，first-writer-wins；resolved-token map 保留 256 条最近
   决策，为 duplicate/expired callback 返回对应 toast。
5. callback 同步回写 `code=200` 与 base64 JSON toast，目标耗时低于 3 秒；决策后使用
   reply API 发送 approved/rejected 反馈。
6. 一个 monotonic deadline 覆盖 prompt send 和 callback wait；timeout 返回 rejected /
   `approval_timeout`，channel stop 返回 rejected / `channel_stopped`，两者都清理 pending map
   并写入 256 项 resolved-token map。
7. 全部功能与 project gates 通过后，在 README `Channels / 渠道` 支持矩阵新增
   `| ✅ | Lark / 飞书 |`；不在功能尚未完成时提前标记支持。

## 13. 验证

### 13.1 Focused automated verification

- builder required/unknown options 与 missing secret；
- region/domain mapping；
- identity value/parser 与 bot mismatch；
- frame golden binary round-trip、malformed varint、truncation、missing required field、unknown field
  skip、header count/key/value 与 frame size limit；
- endpoint/start timeout、auth failure、heartbeat、fragment、ack、reconnect、stop、late event；
- same-loop ordering、concurrent reconnect suppression 与 shutdown race；
- raw event validation、unknown event/type、self-sent filter；
- bot `open_id/app_name` identity、sender contact display-name success/failure/cache、mention、
  reply、thread/session identity；
- inbound body/post parsing；
- attachment download size/name/failure/materialization；
- token refresh single-flight 与 expiry；
- 纯文本风格与 Markdown 风格的 agent body 都走同一 Markdown 路径，覆盖
  split boundary、fenced-code close/reopen、reply、provider rejection、upload 与
  partial/unknown；
- typing add/remove/failure/terminal cleanup；
- approval card JSON、pending correlation、sender mismatch、route mismatch、first-writer-wins、
  duplicate/expired、timeout、stop 与 3-second card ack；
- credential/log/health/runtime environment redaction；
- transport admission 只校验 frame/envelope/resource bound 和 raw queue capacity；business policy、
  durable dedupe、batch、outbound retry/fallback 仍由现有 owner 处理。

### 13.2 Real provider e2e

凭据存在时，以普通用户自然消息完成：

1. p2p 连续对话和 reply；
2. 普通群中含结构化 @ 的消息；
3. 普通群中不含结构化 @ 的消息，与第 2 项产生相同 activation；
4. 话题群两个 thread 并发且 session 不串线；
5. 多级 reply 回到原 thread；
6. text/post、image、file 至少各一条真实 inbound；
7. Markdown、长 fenced code、multi-part、飞书不支持语法、provider rejection 与
   reply outbound；
8. 一个真实 attachment outbound；
9. reconnect 后 duplicate message 不重复入库；
10. stream 期间 Typing reaction 和 terminal cleanup；
11. 交互卡片 approve/reject、非原 sender 点击、重复点击与 callback toast；
12. stop/restart 后无 orphan connection，断线窗口不虚构 replay guarantee。

凭据存在时执行 e2e；凭据缺失时记录 skip。

### 13.3 Project gates

每个 Task 执行对应 focused tests、Ruff 与相关 type diagnostics。Task 4 完成后执行：

```text
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv lock --check
```

验证前先检查工作树，只处理当前 Task 文件，不覆盖用户或其他分支的改动。

## 14. 风险与控制

1. **私有 wire 协议漂移**：只实现 frame 必需子集，golden fixture 与真实 e2e 双重验证；
   provider 协议变化时 clear fail，不容错猜测。
2. **Frame schema 互操作**：`protobug` 声明与 golden fixture 覆盖全部 field
   number/type、required/default、malformed input、unknown skip 和资源上限。
3. **Python 3.14 / async lifecycle**：只复用 BCN 已有 `aiohttp` 和 asyncio loop，验收
   connection、callback、reconnect 与 teardown。
4. **动态 URL credential 泄密**：URL 不作为日志参数，增加 sentinel redaction tests。
5. **outbound ambiguity**：自有 aiohttp client、一次 attempt、明确 receipt classification，禁止
   retry/fallback。
6. **Group ingress 语义分裂**：adaptor 对所有 group/topic event 使用与 WeCom 一致的
   activation mapping，fixture 覆盖有/无结构化 @ 的相同结果。
7. **thread identity 错误**：固定 `chat_id + thread_id-or-zero` route，`parent_id` 仅用于
   direct reply；普通群、两个话题 thread 与多级 reply fixture 覆盖全部分支。
8. **重复 attachment 物化**：5 分钟 / 256 项的 in-flight/result cache 只复用
   materialization side effect；每条 event 仍交给 core durable dedupe。
9. **ack 与 durable append 的间隔**：provider 200 acceptance boundary 固定为“已进入有界
   raw-event queue”，单一 mapper 继续提交 core；mapper failure 记录 event ID、disposition
   和 safe error kind，health 直接暴露该窗口。
10. **bot/display identity 混用**：route/authorization 只用 open_id，name 只用于展示。
11. **card callback deadline 与 correlation**：transport 直接 dispatch `type=card`，3 秒内回写
    ack/toast；event/sender/chat/message/token 全链路校验与 first-writer-wins tests。
12. **通讯录 display-name 查询**：best-effort enrich 失败时保留 open_id 并继续入站，避免影响
    核心消息流程。
13. **计划范围膨胀**：core/app/storage contract 缺口必须带真实证据回到 Plan，不在 Task 内顺手
    重构。

## 15. 完成标准

- `lark` 可作为 agent Channel 被 entry point 动态选择；未选择时不读取凭据。
- README `Channels / 渠道` 支持矩阵在全部门禁通过后包含 `✅ Lark / 飞书`。
- `get_identity()` 暴露 bot `open_id` 与 `app_name`，并与配置中的 agent name 保持独立的
  developer identity 语义；sender display name 仅在通讯录可见时 best-effort 提供。
- DM、群、话题/thread identity 稳定且不串 session。
- text/post/reply/attachment inbound 进入现有 durable inbox。
- 所有已收到的 group/topic message 按与 WeCom 一致的最大投递模型处理。
- `bcc message send` 文本原样进入 Markdown outbound pipeline，仅按 provider limit 分片；
  send/reply/attachment 与 multi-part receipt 保持 BCN delivery evidence。
- interactive card approval 对齐 Telegram 的 authorization、correlation、first-writer-wins、
  duplicate/timeout/stop 语义。
- provider transport 不包含 policy/dedupe/batch/retry/fallback，BCN 仍是唯一业务语义 owner。
- App Secret、tenant token、dynamic endpoint credential 不进入日志、health、storage 或 Runtime。
- Python 3.14 lifecycle、全量测试、Ruff、pyright LSP check 与 lock check 通过。
- 生产业务逻辑只新增 `contrib/lark`；其余改动限于 entry point、
  `protobug==1.0.0` dependency/lock、tests 与 docs。
- 每个 Task 串行完成并停在 review；commit、push、PR、merge、release 分别等待明确授权。
