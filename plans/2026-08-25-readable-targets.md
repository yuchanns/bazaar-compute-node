# Readable Message Targets

## 状态

- 模式：Plan。
- 状态：待 review；review 通过后只进入 Task 1。
- 分支：`feature/readable-targets`。
- 基线：`main` 的 `7ec0381a1c17118da7bdcbcb8258d70ab9fd0231`
  （PR #45 merge commit）。
- 核心约束：数据库、Message、draft 与实际 Channel 路由只使用稳定的 channel-session UUID；
  群名和 username 只用于展示与 `bcc` selector 解析。
- 测试约束：整个 feature 只新增一个 pytest test function；所有 provider、storage、selector 与输出
  场景都作为该函数内有明确 case label 的子场景，已有合同变化直接更新现有测试。
- 所有 Task 按本文顺序串行实施。每完成一个 Task，运行该 Task 的 focused checks，发送业务
  diff 并停在 review；未经 review 不进入下一 Task。

## 1. 目标

当前 `Message.target` 同时承担持久化路由键和用户界面 selector。所有 Channel 因此统一暴露
`dm:<uuid>` / `group:<uuid>`，虽然 Telegram 已随消息提供 username 与群名，Lark 也能通过群信息
接口取得群名。

本次改造把两层语义拆开：

- canonical target：`dm:<channel-session-uuid>` 或 `group:<channel-session-uuid>`，是唯一持久化
  身份；
- display target：能可靠取得可读信息时，DM 显示为 `dm:@username`，群聊显示为
  `#group-name:<channel-session-uuid>`；
- fallback：没有名称、名称不安全、provider 查询失败或 selector 不能唯一解析时，继续使用
  canonical target；
- `bcc` 接受自己展示出的 selector，并在进入 history、draft、reply、unfollow 与 outbound
  persistence 前解析回 canonical target。

名称变化不得创建新会话、改写历史 Message target 或改变 provider route。群 target 的 UUID 后缀
是身份，名称前缀只是提示；DM username 没有 UUID 后缀，因此只有在当前 Agent 范围内唯一时才可
展示和解析。

## 2. 实现范围

- Channel 入站消息可携带一次可选的 target-presentation observation；
- `channel_sessions` 持久化当前 display name 或 DM handle，Message 与 outbound draft 仍只存 canonical
  target；
- `message check/read/send`、`inbox list`、freshness hold、Handoff source context 与
  `thread unfollow` 使用同一套展示和解析合同；
- Telegram 从入站 `chat` 读取群 `title` 或 private-chat `username`；
- Lark 群聊通过现有 tenant token 调用 `GET /open-apis/im/v1/chats/{chat_id}` 读取 `name`，并使用
  有界 TTL cache / single-flight；
- WeCom 明确保留 UUID fallback；
- developer instructions 删除 short-ID 遗留，示例改用完整 UUID message ID，并描述 readable
  target 与 canonical fallback 的实际合同。

## 3. Canonical 与 display target 合同

### 3.1 Canonical persistence

以下位置只允许 canonical target：

```text
dm:<channel-session-uuid>
group:<channel-session-uuid>
```

- 每个 inbound Message 在 Channel adapter 中即使用 canonical target；
- `record_inbound()` 不把 presentation 写入 `messages.target`；
- `MessageDraft.target` 在 selector 首次解析后立即改为 canonical target，freshness retry 与
  `--send-draft` 不保留用户输入的 alias；
- outbound Message、Reminder anchor、Handoff metadata/body identity 与 reply ownership comparison
  均以 canonical target 或 session ID 判断，不比较 display string；
- `message read` 先解析 selector，再以 canonical target 查询历史，避免可读 selector 查不到已存
  Message。

为避免 selector 在 send 的两阶段 fresh-check 中漂移，target resolution 返回一个 typed result，至少
包含 `BcnSession`、`ChannelSession`、canonical target 与当前 display target。recheck 只验证固定的
session/channel binding，没有必要再次以名称查询。

### 3.2 Display formatting

presentation boundary 统一产生：

```text
DM with unique username: dm:@username
DM fallback:             dm:<uuid>
Group with safe name:    #group-name:<uuid>
Group fallback:          group:<uuid>
```

群名可以包含空格、Unicode 与冒号；resolver 按最后一个冒号读取 UUID。空白名称、带 CR/LF、控制字符
或会破坏 header 边界的 `]` 不进入 display target，直接 fallback。Telegram username 按 provider
规则去掉展示用 `@` 后持久化，输出时只添加一次 `@`。

`Message.target` 继续表示 canonical identity。check/read 返回结果增加 presentation-only projection，
`app/command.py` 的 header、source-context command 与 CLI serializer 使用 projection，不把 display
target 写回 Message row。`InboxTargetSummary.target` 本来就是展示 DTO，直接返回 display target。

projection 使用 ChannelSession 当前保存的 presentation，因此历史 Message row 不回填、不改写，但
展示会随当前 presentation 增强：尚未观测到名称时历史 header 仍显示 UUID；一旦后续入站取得名称，
再次读取同一批历史消息时会显示当前 readable target。header 的 target 是“现在可复制使用的
selector”，不是消息发生时的名称快照。

### 3.3 Selector resolution

`resolve_inbox_target()` 的可接受输入固定为：

1. `dm:<uuid>` / `group:<uuid>`：按 Agent ownership、kind 与 channel-session UUID 精确解析；
2. `#any-label:<uuid>`：按最后一个冒号后的 UUID 精确解析，并要求目标 kind 为 group；label 不参与
   identity comparison，因此群改名后旧 selector 仍然可用；
3. `dm:@handle`：在当前 Agent 的 DM channel sessions 中按统一 handle lookup key 查询；必须恰好
   命中一个会话，否则沿用稳定的
   `InboxTargetResolutionError`，绝不任选一条。

inbox/message 输出只在 handle 当前唯一时展示 `dm:@handle`；若发生重复，两个会话都 fallback
到 UUID，保证系统不会展示一个自己无法确定解析的 selector。canonical selector 永远可用。

所有 target consumer 使用这一解析结果：`message send/read`、`inbox list` 间接展示、
`thread unfollow`、freshness/draft retry 以及 Handoff source-history guidance。实现不增加第二套 parser
或各命令自行切字符串。

## 4. 数据模型与 SQLite

新增 schema migration 22，为 `channel_sessions` 增加三个 nullable presentation columns：

```text
target_display_name
target_handle
target_handle_key
```

并增加 Agent/kind/handle-key 的非唯一 lookup index。索引不能设为 unique：真实数据或已有 provider
binding 若冲突，应触发 UUID fallback，而不是让入站事务失败。

core 增加唯一的 typed `ChannelTargetPresentation`，只包含 provider-neutral 的 `display_name` 与
`handle`。core 根据 target kind 选择 group display name 或 DM handle，并为 handle 生成统一的
case-insensitive lookup key：

- observation 本身缺席：provider 本次无法观测，保留已持久化 presentation；
- observation 存在但对应字段为 `None`：provider 已确认当前没有该值，清除旧 presentation；
- group 只允许 `display_name`，DM 只允许 `handle`；
- presentation 永远不能改变 ChannelSession 的 agent/channel/provider/target-kind identity。

core、storage、app 与 `bcc` 不按 channel name 分支，也不接触 `chat.title`、Lark `data.name`、
Telegram username 或 provider API/cache。各 adapter 负责把自己的原始 payload/API 结果投影成同一个
`ChannelTargetPresentation | None`；不具备能力的 adapter 只返回 `None`。

Channel adapter 通过 inbound Message 上的非持久化 ingestion hint 传递 observation。
`record_inbound()` 只在新 Message 上将 observation 折叠到 ChannelSession；重复 provider Message 不回滚
较新的名称。SQLite 与 memory test adapter 保持相同 update、清除和 fallback 语义。

现有 `provider_identity_ref_json` 继续只承载原 metadata，不把可查询的一等 presentation 合同藏进
JSON，也不依赖 SQLite JSON 函数解析 selector。

## 5. Provider 行为

### 5.1 Telegram

Telegram message 的 `chat` 是 target presentation 的权威来源：

- `private`：读取 `chat.username`，映射为统一 `handle`；存在时得到 `dm:@username`，缺失时清除旧
  handle 并 fallback；
- `group` / `supergroup`：读取 `chat.title`，存在且 display-safe 时得到 `#title:<uuid>`，否则
  fallback；
- sender 的 username 仍只用于 `SenderIdentity`，不得误当 DM target username；
- quoted-message backfill 使用同一个会话 presentation，但不能用引用消息中的旧 chat data 覆盖当前
  observation。

Telegram 不增加 API 请求或 cache。

### 5.2 Lark

Lark `im.message.receive_v1` 只有 `chat_id/chat_type`，群聊在组装当前 inbound 前调用新增的
`LarkApi.get_chat(chat_id)`，解析响应 `data.name`：

- 使用现有 tenant access token 与 `_get_json()` error mapping；
- chat cache 与现有 contact cache 都对成功名称采用 1 天 TTL，对失败/空名称采用 5 分钟负缓存；
  两者都设 256 entry 上限和 per-key single-flight，避免正常消息频繁调用 OpenAPI，同时让权限恢复与
  临时错误在有限时间内重试；
- lookup timeout、缺权限、限流、malformed response 或空 name 都不阻塞入站消息；记录结构化
  observation/health counter，并使用 UUID fallback；
- 只有成功取得的群名 observation 才更新持久化值；临时查询失败不清除上一次成功名称；
- p2p 不调用 chat lookup，也不把 contact display name 当作 username。

### 5.3 WeCom

当前 AI Bot WebSocket 入站只提供 `chatid/chattype/from.userid`，并且现有 `bot_id/secret` 路径没有
已确认的群资料读取能力。因此不发出 presentation observation，DM 与 group 均继续展示 canonical
UUID。测试明确锁定 fallback，后续 provider 能力增强只需开始提供 observation，不修改 core identity
模型。

## 6. Developer instructions

`resources/developer_instructions.md` 做两类同步修改：

1. 删除“message short ID / first 8 characters of a UUID”表述；header 示例使用完整、明显的 UUID
   placeholder，`msg=` 定义为必须原样使用的 canonical message ID；
2. target 说明不再暗示固定的 `dm:@peer-name` 必然存在，而是要求始终复用 bcn 输出的 exact target，
   并说明 readable selector 在不可用时会 fallback 到 `dm:<uuid>` / `group:<uuid>`。

instruction 不描述 schema、cache、provider permission 或 resolver 实现。rendered instruction tests 同时
断言不存在 short-ID 遗留、示例 ID 为完整 UUID、可读/fallback target 与真实 CLI 合同一致。

## 7. 任务拆分

### Task 1：建立 canonical/presentation core 与 SQLite 合同

- 增加 `ChannelTargetPresentation`、resolved-target typed result 与 ChannelSession presentation fields；
- 增加 migration 22、codec/repository/memory adapter 与非唯一 handle lookup index；
- 让 inbound observation 更新 ChannelSession，同时保持 `messages.target` 为 canonical；
- 将 history/send/draft/reply/unfollow 的 target resolution 与 persistence 改为 canonical result；
- 新增本 feature 唯一的 pytest test function，以带 case label 的子场景覆盖升级、observation
  update/clear、group stale label、DM 唯一/重复、provider-neutral presentation contract 与 alias send
  后持久化 canonical target；
- 运行 focused core/SQLite/command tests、Ruff、Pyright、migration ledger check 与
  `git diff --check`；
- 发送排除 tests 的业务 diff，停在 review。

### Task 2：接入 Telegram presentation

- 从当前 message `chat` 映射 private username 与 group/supergroup title；
- 保持 quoted backfill、topic identity、sender identity 与 notification semantics 不变；
- 扩展同一个 readable-target test function，覆盖 Telegram username/title、缺失/清除、unsafe title
  fallback 与 topic UUID suffix；
- 运行 Telegram/core focused tests、Ruff、Pyright 与 `git diff --check`；
- 发送排除 tests 的业务 diff，停在 review。

### Task 3：接入 Lark 群名 lookup 与 WeCom fallback

- 为 `LarkApi` 增加 chat-info GET 与 response parsing；
- 将 Lark contact/chat 名称 cache 统一为成功 1 天/失败 5 分钟，并为 chat lookup 增加有界
  single-flight、health counters 与 best-effort fallback；
- 只为 group inbound 提供成功 observation；p2p 和 WeCom 维持 UUID；
- 扩展同一个 readable-target test function，覆盖 Lark cache hit/expiry、concurrent collapse、provider
  fallback、rename refresh，以及 Lark DM / WeCom fallback；
- 运行 Lark/WeCom focused tests、Ruff、Pyright 与 `git diff --check`；
- 发送排除 tests 的业务 diff，停在 review。

### Task 4：统一所有 bcc 展示面并更新 instructions

- check/read headers、inbox list、send/freshness output、Handoff source guidance 与 unfollow 输出统一使用
  display projection；
- 验证任何由 bcc 输出的 target 都能被对应命令重新解析，canonical fallback 始终可用；
- 更新 developer instructions，删除 short-ID 表述并使用完整 UUID placeholder；
- 扩展同一个 readable-target test function，并更新已有 app/bcc/instruction exact-output tests，覆盖
  readable 与 fallback 两条路径；
- 运行 app/bcc/instruction focused tests、Ruff、Pyright 与 `git diff --check`；
- 发送排除 tests 的业务 diff，停在 review。

### Task 5：最终验收

- 运行完整非 e2e pytest suite；
- 运行 `ruff format --check .`、`ruff check .`、
  `uv run scripts/pyright_lsp_check.py --outputjson .`、compileall、`uv lock --check` 与
  `git diff --check`；
- 以 SQLite 集成场景验证 Telegram/Lark presentation 更新、canonical Message rows、重名 fallback、
  改名后的旧 group selector 与所有 bcc round-trip；
- 复核 WeCom 与无 Lark 群权限时收发链路不回归；
- 汇总最终业务 diff、migration 与测试结果，停在最终 review；不自动创建 PR、merge 或 release。

## 8. 验收标准

1. SQLite 中所有 `messages.target` 仍只有 `dm:<uuid>` / `group:<uuid>`；用 readable selector 发送的
   outbound 也不例外。
2. Telegram 有 username/title 时分别输出 `dm:@username` / `#title:<uuid>`，缺失或不安全时 fallback。
3. Lark 有权限且 lookup 成功时输出 `#name:<uuid>`；lookup 失败、p2p 与 WeCom 均不阻塞消息并
   fallback。
4. `#old-name:<uuid>` 在群改名后仍解析到同一个 owned group；错误 UUID 或错误 kind 被拒绝。
5. `dm:@handle` 仅在当前 Agent 内唯一时展示和解析，统一按 case-insensitive lookup key 匹配；重复时
   输出 UUID。
6. `message send/read`、`thread unfollow`、draft/freshness retry、inbox/check header 与 Handoff guidance
   对同一 selector 行为一致。
7. developer instructions 不再出现 `short ID`、`first 8 characters` 或 8 位 message-ID 示例。
8. schema v21 升级到 v22 不回填、不改变现有 session/message identity；现有数据立即可用 canonical
   fallback。
9. core/storage/app/bcc 中不存在按 `telegram` / `lark` / `wecom` 分支的 target presentation 或
   selector 逻辑；provider 差异止于各 Channel adapter 输出统一 observation 的边界。
10. full pytest、Ruff、Pyright、compileall、lock 与 diff gates 全部通过。
