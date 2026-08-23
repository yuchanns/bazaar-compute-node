# Agent-wide Inbox Discovery, Read, and Handoff

## 目标

保持每个 BCN session 由独立 runtime 实例处理，同时为同一个 agent 提供跨 session 的只读消息视图与任务
移交能力。agent 可以先枚举自己拥有的全部消息 target，再按 target 读取对应历史，从而在当前会话中查找
其他 DM、群聊或 thread 已经收到的信息；当前 session 也可以向另一个 target 所属 session 投递 handoff，
由目标 runtime 实例在自己的上下文中继续任务和执行后续发送。

命令面收敛为以下变更：

```text
bcc inbox list [--limit <n>] [--offset <n>]
bcc message read --target "<target>" [--around "<message-id>"] [--limit <n>]
bcc handoff send --target "<target>" [--message-id "<source-message-id>"]
bcc handoff check
```

`bcc message check`、`bcc message send`、thread attention 和 Reminder 命令继续使用当前 session 的既有
语义。外部发送仍由 caller session 的 target ownership 与 fresh-check snapshot 共同授权；handoff 只唤醒
目标 session，不代替目标实例执行外部发送。

## 命令契约

### `bcc inbox list`

`inbox list` 是 agent-scoped、non-draining 的 target catalog：

- 永远枚举当前 agent 拥有的全部 BCN message target，包括 pending 为零的历史 target；
- 按 `last_activity_at_ms DESC, session_id` 稳定排序；
- 使用 `--limit` 和 `--offset` 分页，响应包含 `total`、`shown`、`offset` 和 `has_more`；
- 每个条目返回 canonical target、owner session、target kind、当前 session 标记、pending 数量，以及最新
  消息的 id、sender 和时间；
- 只返回发现与选择 target 所需的摘要，不在 catalog 中展开消息正文或附件；
- 整个查询不推进任何 consumer cursor，也不建立可供发送使用的 inbox snapshot。

canonical text 采用一行一个 target 的稳定 envelope，例如：

```text
Inbox targets: 2 returned, offset 0, total 2, ordered by recent activity.
[target=dm:<id> session=<id> kind=dm current=false pending=0 latest-msg=<id> latest-sender=@alice latest-time=2026-08-22 20:00:00]
[target=group:<id> session=<id> kind=group current=true pending=3 latest-msg=<id> latest-sender=@bob latest-time=2026-08-22 19:00:00]
```

空 catalog 和后续分页使用明确的 terminal line，使 agent 能区分“没有 target”和“当前 page 为空”。

### Cross-session `bcc message read`

本地 capability 仍绑定发起命令的 `caller_session_id`。command service 在当前 agent storage scope 内按
canonical target 解析唯一的 `source_session_id`：

- source 与 caller 相同时，保留现有 history window、referenced message 和 fresh-check snapshot 行为；
- source 与 caller 不同时，从 source session 读取 history window，并保持 caller/source 两侧的 consumer
  cursor 与 inbox snapshot 原值；
- `--around` 只解析 source session 内的 message id，referenced message 也只沿 source session 展开；
- target 缺失、属于其他 agent 或解析结果不唯一时 fail closed；
- audit 同时记录 caller session、source session、target、window limit 和 around anchor，便于追踪跨 session
  观察行为。

CLI 输出继续使用现有 message read envelope。每条 message 已包含 canonical target 与完整 local message id，
因此读取结果可以被后续同 target 的只读定位复用。

## 权限与状态边界

本地 command transport 先用 `BCN_SESSION_ID`、`BCN_RUNTIME_SESSION_ID` 和
`BCN_COMMAND_CAPABILITY` 验证 caller session。storage scope 再用 agent id 约束 target discovery 与 source
session resolution。

读取路径使用两个显式身份：

- `caller_session_id` 表示发起命令并持有本地 capability 的 runtime session；
- `source_session_id` 表示 canonical target 所属的消息 session。

`message send` 始终以 caller session 查询 target、reply message、current inbound sequence 和
`ConsumerCursor.inbox_snapshot_seq`。cross-session read 产生的观察结果不会进入这条发送授权链。

pending 数量按每个 owner session 自己的 `ConsumerCursor.delivered_through_seq` 与
`InboundMessage.notifies_runtime` 计算。catalog 只观察该状态；对应 owner session 的 `message check` 仍是唯一
推进 delivered cursor 的消息命令。

## Core 与 Storage 设计

在 command domain 增加 inbox target summary/result 类型，并让 `ICommandService` 暴露分页 list 操作。
summary 使用 provider-neutral 字段，不把 adapter payload 或 provider credential 带入 command response。

storage transaction 增加两个 agent-scoped 查询：

1. 分页列出 BCN session 与 channel session，并聚合 owner cursor、pending count 和 latest inbound 摘要；
2. 按 canonical target 精确解析唯一 owner BCN session。

查询继续依赖 scoped repository 的 `agent_id = bcn_agent_id()` 边界。实现前用 SQLite query plan 验证
agent、session、activity 和 inbound sequence 的访问路径，并根据实际 plan 添加最小复合索引。

`SessionCommandService.read` 将 caller resolution、source resolution 与 history read 分开。只有
`caller_session_id == source_session_id` 的 read 保存现有 cursor snapshot；foreign read 在 transaction 中
只执行查询。session concurrency 同时保护 caller 的 capability/state 判断与 source history 的一致窗口，锁
顺序使用稳定 session id 排序，避免两个 session 互相读取时形成反向获取。

## Application 与 CLI

command dispatcher 新增 `inbox/list` route，校验正整数 `limit` 与非负 `offset`，并序列化分页 metadata 和
target summaries。session binding validator 继续验证 caller session，不接受由 CLI 覆盖 caller identity。

`bcc` parser 增加 `inbox` resource 与 `list` command。help 明确写为：

```text
List available message targets
```

serializer 对 summary 必填字段、分页边界、latest message nullable 组合和稳定排序结果执行严格校验。

developer instruction 使用以下两个精确编辑点。

第一处：在 `Communication — bcc CLI ONLY` 中，用下面内容完整替换 `Use ONLY these command families for
communication:` 之后、`Run any subcommand with --help for syntax.` 之前的现有三项列表：

```markdown
1. **Messages** — `bcc message check`, `bcc message send`, `bcc message read`.
2. **Inbox discovery** — `bcc inbox list`.
3. **Thread attention** — `bcc thread unfollow`.
4. **Reminders** — `bcc reminder schedule`, `bcc reminder check`, `bcc reminder list`, `bcc reminder snooze`, `bcc reminder update`, `bcc reminder cancel`.
```

第二处：保持 `### Historical references` 的现有段落逐字不变，在该段落之后原样追加：

```markdown
When a user refers to prior conversations and the relevant target is unknown, use `bcc inbox list` to inspect the available conversations. Use `--offset` to find the target or exhaust the list. Select the exact `target` for the relevant conversation, then use `bcc message read` to read its history.
```

新增段落用于 target 未知的历史查找；target 已知时沿现有 `bcc message read` 路径。

## 实施任务

### Task 1：移除 developer instruction 与 bcc help 中的 canonical 术语

- 在 `core/instruction.py` 的现有 developer instruction 模板中逐项替换 5 行、共 6 次 agent-facing
  `canonical`：
  - `human-readable canonical text` 改为 `human-readable text`；
  - `canonical labeled text` 改为 `labeled text`；
  - `exact canonical target` 改为 `exact target`；
  - `canonical values supplied by bcn` 改为 `exact values supplied by bcn`；
  - `each canonical target` 与 `corresponding canonical target` 分别改为 `each target` 与
    `corresponding target`。
- 在 `bcc.py` parser 的现有 read、send、unfollow 参数 help 中删除 `Canonical` 前缀，分别使用
  `DM/thread target to read`、`DM/thread target to reply to` 和 `Group/thread target to unfollow`，保留后续
  语义与命令引用。
- 更新对应 instruction snapshot、parser help 和 serializer tests 的期望文本及测试命名，使 agent-facing
  instruction/help 不再出现 `canonical`，target 的 exact reuse 与禁止构造/替换规则继续由普通 `target`
  术语表达。
- 增加聚合断言，渲染完整 developer instruction 并遍历 `bcc message read/send`、`bcc thread unfollow` 的
  help，确认这些 agent-facing surface 中 `canonical` 的 occurrence count 为零。

依赖：现有 developer instruction 与 bcc parser。产出：统一使用 `target` / `exact target` 的既有
agent-facing vocabulary，为后续 inbox list instruction 与 help 提供基线。

### Task 2：Core command contract 与 target summary model

- 在 command domain 增加 `InboxTargetSummary` 与分页 result，固定 canonical target、owner session、target
  kind、current 标记、pending count、last activity 和 nullable latest message 摘要的类型与校验规则。
- 扩展 `ICommandService`，增加以 caller session、limit 和 offset 为输入的 inbox list contract；将
  `message read` 参数中的 session 明确命名为 caller session，并由 service 内部解析 source session。
- 保持 `MessageCheckResult`、`MessageReadResult` 和 outbound/fresh-check model 的现有序列边界，使新 catalog
  与 cross-session read 复用既有 inbound message 表达。
- 增加 core model tests，覆盖 invalid pagination、negative pending、latest message nullable 字段组合和
  result pagination invariant。

依赖：现有 session-scoped command 与 inbound/cursor model。产出：provider-neutral inbox list contract 和
caller/source read contract。

### Task 3：Agent-scoped storage discovery 与 target resolution

- 在 storage transaction port 与 SQLite scoped repository 增加 target catalog query：按 agent scope 联结 BCN
  session、内部 channel session、consumer cursor 和 latest inbound，计算每个 owner session 的 notifying
  pending count。
- 查询返回 pending 为零的历史 target，按 `last_activity_at_ms DESC, session_id` 稳定排序；count 与 page 在
  同一 transaction snapshot 中产生，尾页和空页返回一致的 total/offset 语义。
- 增加 canonical target resolver，在当前 agent scope 内返回唯一 owner BCN session；unknown、cross-agent 和
  ambiguous resolution 使用同一 fail-closed domain error。
- 对 catalog、pending 聚合、latest inbound 和 target resolution 运行 `EXPLAIN QUERY PLAN`；根据实际访问
  路径添加最小 migration/index，并更新 migration checksum 与 database schema tests。
- 增加 SQLite integration tests，覆盖多 agent 同名/同类 target、已读与 pending session、无 inbound 的已知
  session、稳定分页，以及其他 agent 的 message/session 不进入聚合结果。

依赖：Task 2 的 summary/result contract。产出：agent-isolated catalog query、exact target resolver 和必要的
SQLite 索引。

### Task 4：Cross-session read orchestration 与状态隔离

- `SessionCommandService.list_inbox` 先验证 caller BCN session，再调用 agent-scoped catalog query，并只把
  caller/source 关系投影成 `current` 标记，不推进任何 cursor 或 snapshot。
- `SessionCommandService.read` 先解析 target owner：same-session 路径保留现有 history window 与
  `inbox_snapshot_seq` 更新；foreign-session 路径读取 source history 与 referenced messages，并保持 caller
  和 source 的 `ConsumerCursor` 原值。
- foreign read 的 `--around` anchor 与 referenced message resolution 全部绑定 source session；每个返回
  message 保留自身 canonical target 和完整 local UUID。
- concurrency helper 以稳定 session id 顺序获取 caller/source session lock；caller 与 source 相同只获取
  一次，两个 session 互相读取时保持统一 lock ordering。
- audit 为 list 记录 caller 与 pagination，为 read 同时记录 caller/source、target、limit、around anchor 和
  same/foreign scope；correlation 不携带正文、附件内容或 provider payload。
- 增加 orchestration tests，证明 foreign read 不改变任一 cursor、不授权 caller send，same-session read 与
  fresh-check 兼容，并覆盖 read/check/new inbound 的 concurrency interleaving。

依赖：Task 3 的 catalog query 与 target resolver。产出：non-draining agent-wide discovery、pure foreign
read 和有序 concurrency/audit 边界。

### Task 5：Application route、bcc parser 与 target serializer

- command dispatcher 增加 `inbox/list` route，沿用 session binding validator 验证 caller capability，并严格
  校验正整数 limit、非负 offset 及 response pagination metadata。
- `bcc` parser 增加 `inbox` resource 和 `list` subcommand；help/description 对齐现有短动词风格，使用
  `List available message targets`，并为 `--limit`、`--offset` 提供一致 metavar 与默认值说明。
- 增加 inbox list request mapping 与严格 serializer，输出 header、逐 target canonical envelope 和 terminal
  pagination line；不把内部 adapter 字段加入 agent-facing response。
- `latest-time` 复用 message header 的 timestamp selection 与 `format_message_time`：优先 provider time，缺失
  时使用 received time，并按 node 当地时区输出 `YYYY-MM-DD HH:MM:SS`。
- serializer 校验 total/shown/offset/has_more、stable page bounds、current flag、pending count，以及 latest
  message 三个 nullable 字段的组合，malformed application response 继续映射为稳定 CLI error。
- 更新 parser/serializer/dispatcher tests，覆盖 help snapshot、空 catalog、中间页、尾页、nullable latest
  message、invalid request 和 session binding failure。

依赖：Task 2 的 command contract 与 Task 4 的 command service。产出：可由 runtime wrapper 调用的
`bcc inbox list` 及稳定 text contract。

### Task 6：Developer instruction integration

- 应用本 Plan 在 `Application 与 CLI` 中给出的第一处 exact replacement，将 command family 列表由三项替换
  为四项，并把 Inbox discovery 固定在 Messages 之后、Thread attention 之前。
- 应用第二处 exact insertion，保持 `Historical references` 的现有段落逐字不变，并把给定新段落紧接在
  现有段落之后。
- 更新现有 Codex 行为测试，移除 developer instruction 文案与渲染断言，保留线程选项、协议事件和启动流程覆盖。

依赖：Task 1 的 vocabulary 基线与 Task 5 的最终 CLI syntax/help wording。产出：与现有 instruction 结构
一致的 target discovery 行为指导。

### Task 7：Real command-process integration 与质量门禁

- 扩展真实 SQLite + local command transport 测试：建立同 agent 多 session 与另一 agent session，通过
  wrapper 执行 inbox list、same-session read、foreign-session read、check 和 send。
- 验证 foreign target 可发现并可读，其他 agent target 不可见；foreign read 前后两侧 cursor/snapshot
  byte-for-byte 等价，owner session 后续仍能 check 原 pending message 并收到正常 wake。
- 验证 same-session read 后 send 继续通过既有 fresh-check，foreign read 后 caller send 仍按 caller 自己的
  snapshot 判定；foreign reply target 和 message id 不能越过 send ownership gate。
- 加入两个 session 并发互读、owner check 与新 inbound 同时发生的 bounded tests，证明 lock ordering、page
  snapshot、pending count 和 referenced message resolution 没有 deadlock、误 ack 或串读。
- 运行 focused tests、完整 test suite、Ruff、Pyright、compileall、lock verification、
  `git diff --check`，并对所有改动 Python 文件运行 LSP diagnostics。

依赖：Task 1–6。产出：跨 session 只读能力的真实 command-process 证据与完整质量门禁结果。

阶段验收：任一 session 均可分页发现全部可用 target，并只读同一 agent 的 foreign history；catalog 与
foreign read 不改变任何 delivery cursor、snapshot 或 wake；same-session check/read/send 行为保持兼容；
agent-facing CLI 与 developer instruction 只使用 canonical target，不暴露内部 adapter 概念。

## 验证

测试需要证明：

- `inbox list` 返回当前 agent 的 pending 与已读历史 target，并按最近活动稳定分页；
- list 前后所有 session 的 delivered cursor、snapshot 和 wake 状态完全一致；
- foreign read 能读取同一 agent 的 DM/group/thread，并保持 caller/source cursor 不变；
- same-session read 继续刷新现有 fresh-check snapshot，随后 send 行为保持兼容；
- foreign read 不能为 caller session 的 send 提供 fresh-check evidence；
- 其他 agent 的 target、message id 与 owner session 在 discovery/read 中均不可见；
- target resolution 歧义、invalid pagination、unknown around anchor 均 fail closed；
- 两个 session 并发互读、新 inbound 同时落库以及 owner session 同时 check 时没有 deadlock、误 ack 或串读；
- CLI canonical text 在空列表、nullable latest message、分页尾页与 attachment/referenced message 场景保持稳定。

质量门禁包括 focused tests、完整 test suite、Ruff、Pyright、compileall、lock verification、
`git diff --check`，以及所有改动 Python 文件的 LSP diagnostics。

## Handoff 扩展

### 行为契约

handoff 是同一 agent 的两个 BCN session 之间的一次性任务投递。source session 只保存任务并唤醒 target
session；target runtime 实例读取 handoff 后，基于自己的 runtime 上下文决定如何执行任务。handoff 本身不调用
Channel、不产生 outbound message，也不把 source session 的发送权限带给 target session。

runtime steer 只注入内容为空的 wake notice，不把 handoff 正文直接写成新的 user message。这样当前 user request
仍然是活跃 turn 的主要上下文，目标实例可以在自然断点显式运行 `bcc handoff check`，再把带来源标记的任务正文
作为 command result 读入上下文。

handoff 使用 Reminder occurrence 相同的 inspect-once 消费语义：`check` 最多读取 100 条未读 handoff，并只把
本次实际返回的记录标记为已读。已读表示目标实例已经检查该任务，不表示任务完成。第一版不提供执行状态、完成
回执、重试、snooze、update、cancel、list 或历史重读命令。

### CLI 命令设计

发送命令从 stdin 读取必填正文：

```bash
bcc handoff send --target "dm:@user" --message-id "<source-message-id>" <<'BCCTASK'
Read the source conversation and send the requested details to the user.
BCCTASK
```

- `--target` 必填，使用 `bcc inbox list` 返回的精确 target；command service 在当前 agent scope 内解析唯一的
  target session；
- `--message-id` 可选，只能引用 source session 的 inbound message，用于给目标实例提供精确来源 anchor；
- source session 与 source target 从本地 capability 绑定的 caller session 自动取得，不接受 CLI 覆盖；
- body 必须是非空文本，保持原样存储，不通过命令行参数传递；
- 命令不要求 `message read/check` 建立 fresh-check snapshot，也不改变 source/target 任一 message cursor；
- 成功表示 handoff 已保存且目标 runtime wake 已进入本进程的 runtime queue，不表示任务已经完成或外部消息已经
  发送。

成功输出使用稳定的单行文本：

```text
Handoff sent: #<handoff-id> target=<target>
```

检查命令无参数：

```bash
bcc handoff check
```

它在同一 storage transaction 中读取最多 100 条 `read_at_ms IS NULL` 的记录，解析 source target 与可选 source
message，然后把实际返回的 handoff ID 标记为已读。每条记录输出 handoff ID、source target、可选 source message、
创建时间和完整正文；最后输出是否仍有更多未读 handoff：

```text
[handoff=<handoff-id> source=<source-target> message=<source-message-id-or-none> time=<timestamp>] <body>
No more pending handoffs.
```

空结果为 `No pending handoffs.`；超过单批上限时提示再次运行 `bcc handoff check`。CLI 不增加 `read` 或 `list`
命令：handoff 本身就是一次性 occurrence，`check` 已返回其完整内容。

### Core 与表设计

core 增加不可变 `Handoff` model，以及 `HandoffSendRequest/Result`、`HandoffCheckItem/Result` 与
`IHandoffService`。model 固定验证非空 ID/session/body、nullable source message、非负时间，以及
`read_at_ms >= created_at_ms`。

SQLite migration version 15 新增一张 agent-owned 表：

```sql
CREATE TABLE handoffs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id TEXT NOT NULL UNIQUE,
    command_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    target_session_id TEXT NOT NULL,
    source_message_id TEXT,
    body TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    read_at_ms INTEGER
);

CREATE INDEX idx_handoffs_agent_target_read_seq
    ON handoffs (agent_id, target_session_id, read_at_ms, seq);
```

`source_message_id` 只表达可选来源 anchor。`command_id` 为一次 wrapper 调用提供幂等保存，避免 local command
timeout 后产生重复 handoff。agent-scoped repository 对所有查询强制 `agent_id = bcn_agent_id()`。

storage transaction 增加以下操作：

1. 保存新 handoff；相同 `command_id` 与相同 payload 返回原记录，不同 payload 返回稳定冲突错误；
2. 按 target session、`read_at_ms IS NULL`、`seq` 升序读取限定批次；
3. 统计 target session 未读 handoff；
4. 在同一 transaction 中按明确 ID 集合设置 `read_at_ms` 并返回更新后的记录；
5. 为 target session 查询最新 inbound message，供 runtime turn correlation、approval 和错误反馈继续绑定 target
   conversation；该 routing message 不写入 handoff 表，也不作为 handoff 正文来源展示。

handoff audit 只记录 ID、source/target session、source message、数量和状态，不记录 body。表的用途是把 content-free
notice 与任务正文分离；第一版不承诺 node 终止期间的 wake 重放，也不增加后台 handoff scheduler。

HandoffCommandService 不获取 `ISessionConcurrency` session lock。send 是 append-only；check 的 pending list、精确
mark-read 与 has-more count 位于同一 storage transaction，当前 SQLite adapter 通过 `BEGIN IMMEDIATE` 与数据库
transaction lock 保证两个并发 check 不会返回同一批记录。runtime turn/steer 的顺序继续由既有 per-session runtime
queue 负责，command service 不再增加第二层 session serialization。

### Runtime notice 与 steer

`HandoffCommandService.send` 在 storage commit 后调用 target agent 的 `publish_handoff_wake(target_session_id)`。
orchestrator 重新统计未读 handoff，加载 target session context 与最新 inbound routing message，并向该 session 的
既有 runtime queue 放入 `_HandoffNotification`。

handoff notice 固定为：

```text
[handoff notice session=<session-id>]
Handoffs pending: <n>. Use `bcc handoff check` to read them.
```

`_HandoffNotification` 加入现有 wake notification union，并复用 Reminder 的运行语义：

- target session 空闲时，以 handoff notice 开启新的 runtime turn；
- target session 存在活跃 turn 时，通过现有 `_steer_active_turn()` 注入 notice；
- `_run_notification()` 与 steer 前都重新统计未读 handoff；计数为零时直接返回；
- agent 在 steer 后运行 `handoff check` 会清空对应未读记录，因此 queue 中稍后处理同一 wake 时重新计数为零，不会
  再启动重复 turn；
- turn observation source 使用 session wake，turn ID 使用独立 wake UUID，不复用 source 或 target message ID。

handoff notice 不包含 source、body 或 anchor；多个 handoff 可以由一次 `check` 批量读取。runtime error feedback 与
approval 继续路由到 target session 的 conversation，不返回 source session。

### Developer instruction 编辑

在 `Communication — bcc CLI ONLY` 的 command family 列表中，在 Inbox discovery 之后加入：

```markdown
3. **Handoffs** — `bcc handoff send`, `bcc handoff check`.
```

后续 Thread attention 与 Reminders 顺延编号；把 `Use only the provided bcc commands for messaging and Reminder
management.` 改为：

```markdown
Use only the provided `bcc` commands for messaging, handoffs, and Reminder management.
```

在 `### Reminders` 之后、`### Threads` 之前加入：

```markdown
### Handoffs

Use `bcc handoff send --target "<target>"` when work should continue in another conversation. Use `bcc inbox list` if you need to find the target, and pass the task through stdin. Add `--message-id "<message-id>"` when the task refers to a specific inbound message in the current conversation.

When a handoff notice wakes you, run `bcc handoff check` at a natural breakpoint. Continue the returned tasks in the current conversation. Use the supplied source target and optional source message with `bcc message read` when you need more context.
```

在 startup sequence 中把 handoff notice 与 Reminder notice 一起列为 content-free work wake：没有 concrete inbound
message 时分别使用 `bcc reminder check` 与 `bcc handoff check` 检查本轮实际出现的 notice；处理中收到新 notice 时
在自然断点检查。同步更新紧随 startup sequence 的 `IMPORTANT` 段落，使其列出 inbox、handoff 与 Reminder 三类
notice 及对应 check 命令。

把 `Runtime Notifications` 的开头改为三类互不合并的 notice，并在 inbox notice 与 Reminder notice 之间加入：

````markdown
Handoff notice shape:

```text
[handoff notice session=<session-id>]
Handoffs pending: <n>. Use `bcc handoff check` to read them.
```

How to handle handoff notices:
- Treat the notice as a non-urgent signal that handoff tasks are waiting.
- Run `bcc handoff check` at a natural breakpoint and continue the returned tasks in the current conversation.
- Use the supplied source target and optional source message with `bcc message read` when you need more context.
````

instruction 只描述 agent 可观察的 send/check 与 notice 行为，不暴露表、runtime queue 或 Channel adapter。

## Handoff 实施任务

### Task 8：Handoff core contract

- 新增 `Handoff` entity 与 send/check request/result 类型，固定一次性 inspect-once 语义、100 条 check 上限和
  `has_more` invariant；
- 新增 `IHandoffService`，send 输入包含 caller session、target、body、command ID、可选 source message 与创建
  时间；check 只接收当前 session；
- 在 storage port 增加 save、pending list/count、mark-read 与 target runtime anchor 查询；
- 增加 model/contract tests，覆盖 multiline body、nullable source message、时间边界、batch result 与幂等 payload
  invariant。

依赖：既有 inbox target resolver、Reminder check result pattern。产出：不依赖 SQLite/runtime 的 handoff domain
contract。

### Task 9：SQLite migration 与 agent-scoped repository

- 新增 version 15 migration、`handoffs` 表和 pending index，安装顺序位于 agent ownership 与 inbox discovery
  migration 之后；
- 在 unscoped 与 agent-scoped transaction 实现 handoff codec/repository 方法，scoped 查询全部包含当前 agent
  过滤；
- `save_handoff` 用 `command_id` 做幂等冲突检查；`check` 按 seq 稳定读取并只标记返回 ID，读取 source target 与
  source message snapshot 后再更新 read marker；
- 对 pending list/count 与 command ID lookup 运行 `EXPLAIN QUERY PLAN`，更新 migration checksum/schema tests；
- 增加 SQLite integration tests，覆盖同 agent 多 session、另一 agent 不可见、批量上限、has-more、精确 mark-read
  与幂等保存。

依赖：Task 8。产出：最小持久化 handoff inbox 与 agent 隔离证据。

### Task 10：Command service、dispatcher 与 bcc CLI

- 新增 `HandoffCommandService`；send 验证 caller capability 对应 session、解析同 agent target、解析可选 source
  message、选择 target runtime anchor、提交 handoff 后发布 wake；
- check 在单个 storage transaction 内返回未读批次并设置 `read_at_ms`，不改变 message cursor、inbox snapshot 或
  Reminder occurrence；
- command dispatcher 增加 `handoff/send` 与 `handoff/check` request model、session binding validation、稳定错误
  映射和 response serialization；
- `bcc` parser 增加 handoff resource；send 接收 required `--target`、optional `--message-id`，正文只从 stdin
  读取并生成 command ID；check 不接收额外参数；
- serializer 实现 sent/empty/batch/has-more 文本，并严格校验 source target、nullable source message、时间与正文；
- 增加 parser、stdin、request mapping、dispatcher、serializer 与 fresh-check isolation tests。

依赖：Task 8–9。产出：可调用的 `bcc handoff send/check` 与完整 command-process contract。

### Task 11：Runtime wake、notice 与 developer instruction

- 在 Agent application 接入 handoff service 与 wake publisher，在 SessionOrchestrator 增加
  `_HandoffNotification`、`publish_handoff_wake`、pending count 和 `handoff_notice()`；
- 空闲 turn 与活跃 steer 复用 Reminder 路径，但 runtime correlation 使用 target anchor，notice 正文只包含 session
  与 pending count；
- 覆盖 check 已消费后 queued wake no-op、多个 handoff 合并读取、steer 期间新增 handoff 与 target session context
  recovery；
- 按本 Plan 的 exact text 更新 command family、Handoffs 章节、startup sequence 和 Runtime Notifications；
- instruction 测试只验证渲染/结构所需的现有测试边界，不新增大段文案快照或不存在行为的反向断言。

依赖：Task 10。产出：content-free handoff wake、目标 session runtime 承接与 agent 使用说明。

### Task 12：真实进程集成与质量门禁

- 用真实 SQLite、local command transport 与两个同 agent session 执行 source `handoff send`、target runtime wake、
  target `handoff check` 和 target `message send`；
- 验证 handoff 正文不出现在 steer user message，只有 `handoff check` command result 展开正文；
- 验证 check 只消费目标 session 返回的 handoff，另一 session 与另一 agent 数据不受影响，第二次 check 返回空；
- 验证 source/target message cursor 与 fresh-check snapshot 均不因 send/check 改变，目标实例后续发送仍必须通过自己
  的 fresh-check；
- 验证空闲 wake、活跃 steer、steer 后 check 导致 queued wake no-op，以及 command timeout 重试不重复保存；
- 运行 focused tests、完整 test suite、Ruff、Pyright、compileall、lock verification、`git diff --check`，并对
  所有改动 Python 文件运行 LSP diagnostics。

依赖：Task 8–11。产出：跨 session handoff 从 CLI 到 runtime 的端到端证据。

Handoff 阶段验收：source session 可以把一次性任务投递给同 agent 的 target session；runtime 只注入 content-free
notice；目标实例通过 `handoff check` 读取并消费完整任务；后续外部发送由目标 session 自己完成；整个流程不共享
runtime 上下文、不改变 message delivery/fresh-check 状态，也不提供 handoff 的二次读取或任务生命周期管理。
