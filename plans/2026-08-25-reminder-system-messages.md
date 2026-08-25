# Reminder and Handoff System Messages

## 状态

- 模式：Plan
- 状态：待 review；review 通过后只进入 Task 1。
- 基线：`fix/message-sender-delimiter` 的 `7213813d04a8`。
- 本文定义 Reminder fire 与跨会话 send 结果进入统一 Message inbox，移除专用 Reminder
  occurrence/read surface、独立 Handoff resource，以及对应的 developer instructions 减量。
- 所有 Task 按本文顺序串行实施；每完成一个 Task，运行该 Task 的 focused checks，发送业务
  diff 并停在 review，未经 review 不进入下一 Task。

## 1. 目标合同

Reminder definition 继续由当前 BCN session 创建、持久化并绑定一条当前 session 内的
inbound Message。调度规则、one-time/recurring 状态迁移、snooze/update/cancel 语义和单一
frontier timer 保持不变。

每次 fire 改为在同一个 storage transaction 中完成两件事：

1. one-time Reminder 转为 `fired`，或 recurring Reminder 推进到下一 scheduled slot；
2. 在 Reminder owner session 中插入一条 `sender_kind=system`、sender 为 `system`、
   `message_type=text` 的 inbound system Message。

该 Message 是本次 fire 的唯一 durable inbox occurrence，同时进入普通 conversation history。
BCN 不再保存 `ReminderOccurrence`，不再维护独立 pending/read marker，也不再提供独立 Reminder
inbox。

完整链路固定为：

```text
ReminderScheduler reaches a due slot
    -> storage atomically advances Reminder and inserts one system Message
    -> scheduler publishes an ordinary inbox wake for the owner session
    -> idle runtime starts a turn with the ordinary content-free inbox notice
       or active runtime receives the same notice through turn steering
    -> agent calls bcc message check
    -> the consumer cursor acknowledges the returned system Message
```

Reminder fire 不调用 `IChannel.send`，不创建 outbound Message，不产生 provider receipt，也不改变
thread/group following。system Message 进入 owner session 的 unread inbox；`bcc message check`
负责首次交付并推进 cursor，`bcc message read` 负责之后的只读 history 查看。

## 2. System Message 合同

### 2.1 Message 字段

不增加第三种 `MessageDirection`。Reminder fire Message 使用现有 `inbound` direction，因为它是
进入 agent inbox、由 consumer cursor 消费的本地输入；它通过 `sender_kind=system` 与 provider
inbound Message 区分。

字段固定为：

```text
direction = inbound
message_id = one UUIDv7 per fire
session_id = Reminder.owner_session_id
channel_session_id = anchor Message.channel_session_id
target = anchor Message.target
target_kind = anchor Message.target_kind
channel = anchor Message.channel
provider_thread_id = anchor Message.provider_thread_id
provider_message_id = null
provider_time_ms = null
received_at_ms = fired_at_ms
sender = SenderIdentity(id="system", name="system")
sender_kind = system
message_type = text
body = render_reminder_fire_body(Reminder, target, next_fire_at_ms)
attachments = ()
reply_to_message_id = null
mentions_agent = false
notifies_runtime = true
provider_payload_ref = null
metadata = {
    "sender_kind": "system",
    "system_message_kind": "reminder",
}
```

`SenderKind` 增加 `SYSTEM = "system"`。`Message.sender_kind` 对 system Message 返回
`SenderKind.SYSTEM`；现有 provider inbound 仍只接受 human/agent/unknown，outbound 仍固定为
agent。`sender_kind` 不是新的 SQLite column，继续通过 `metadata_json` 持久化。

`SystemMessageKind` 增加 `REMINDER = "reminder"` 与 `HANDOFF = "handoff"`，并由
`Message.system_message_kind` 从 `metadata_json` 解析。只有 `sender_kind=system` Message 必须具有
该值，普通 provider inbound/outbound Message 不得设置。该 metadata 只用于 domain validation 与
Python formatter 分支，不参与 SQL filter、catalog、freshness 或 recovery query，因此不新增物理
column。

`app/command.py::serialize_message()` 不暴露整个 metadata map，只额外投影 formatter 需要的
nullable `system_message_kind`、`system_message_source_target` 和
`system_message_source_message_id`。Handoff Message 必须同时具有后两个值；Reminder Message
的后两个值必须为空。`format_check_message()` 只按这些 typed projection 追加 suffix；
`format_read_message()` 和 freshness formatter 忽略它们。

- system Message 不要求 `provider_message_id`，且 `inbound_identity()` 不允许在 system Message
  上调用；
- system Message 不允许 attachments、provider payload reference 或 outbound delivery fields。

### 2.2 Message 正文

Reminder fire Message 的 durable body 由 scheduler 在落库前一次性构造并原样保存。durable body
不包含只用于首次交付的两行 agent instructions。one-time durable body 精确为：

```text
🔔 Reminder #<reminder-short-id> (one-time) — <target> — "<title>"
```

recurring durable body 精确为：

```text
🔔 Reminder #<reminder-short-id> (recurring · <schedule-description>) — <target> — "<title>"
Next iteration: <next-fire-at>
```

占位符固定解释为：

- `<reminder-short-id>` 是 canonical `Reminder.reminder_id` 的前 8 个字符，仅用于 fire body 展示；
  不新增持久化 `short_id` 字段，也不改变 Reminder mutation command 仍要求完整 UUID 的输入合同；
- `<target>` 是 anchor Message 的 canonical target，与 system Message envelope 的 `target` 相同；
- `"<title>"` 使用现有 CLI title quoting 规则，即
  `json.dumps(Reminder.title, ensure_ascii=False)`，避免 title 内的引号破坏单行结构；
- `<schedule-description>` 直接使用当前 Reminder 的非空 `repeat_rule`；
- `<next-fire-at>` 使用现有 `format_utc_timestamp(next_fire_at_ms)`，且只能取本次 recurring fire
  原子推进后写入 Reminder 的下一 scheduled slot。

现有 title 控制字符校验继续生效。durable body 不包含括号操作提示；该提示只由
`message check` formatter 追加。操作提示与两行 agent instructions 都不进入 durable body，
也不写入 developer instructions。

### 2.3 `message check` 可见性

`bcc message check` 保持普通 inbound Message 的 envelope/body formatter；当
`system_message_kind=reminder` 时，在 durable body 后先追加不持久的 Reminder 操作行：

```text
one-time:  (to snooze/cancel: bcc reminder --help)
recurring: (to snooze/update/cancel: bcc reminder --help)
```

然后追加以下两行固定 agent-instruction suffix：

```text
Respond as appropriate. Complete all your work before stopping.
Reply in the channel or create/reply in a thread as appropriate; use each message's `target` and `msg` fields to choose the exact target.
```

suffix 不是 Message body，不持久化，也不由 `format_read_message`、freshness formatter 或其他
history surface 输出。

one-time 的完整 check 输出精确为：

```text
[target=<target> msg=<fire-message-id> time=<fired-at> type=system] @system: 🔔 Reminder #<reminder-short-id> (one-time) — <target> — "<title>"
(to snooze/cancel: bcc reminder --help)
Respond as appropriate. Complete all your work before stopping.
Reply in the channel or create/reply in a thread as appropriate; use each message's `target` and `msg` fields to choose the exact target.
```

recurring 的完整 check 输出精确为：

```text
[target=<target> msg=<fire-message-id> time=<fired-at> type=system] @system: 🔔 Reminder #<reminder-short-id> (recurring · <schedule-description>) — <target> — "<title>"
Next iteration: <next-fire-at>
(to snooze/update/cancel: bcc reminder --help)

Respond as appropriate. Complete all your work before stopping.
Reply in the channel or create/reply in a thread as appropriate; use each message's `target` and `msg` fields to choose the exact target.
```

`bcc message read` 使用现有 history query 和 `format_read_message`，返回同一条 system Message 的
durable body，不追加 agent instructions。以 fire Message ID 调用 `message read --around` 正常定位；
history window 可反复读取且不移动 consumer cursor。例如 one-time history item 为：

```text
[<index>/<count> seq=<seq> msg=<fire-message-id> time=<fired-at> type=system replyTarget=<target>] @system: 🔔 Reminder #<reminder-short-id> (one-time) — <target> — "<title>"
```

Reminder system Message 可由现有 `message send --reply-to` resolution 正常定位。生成的 outbound
Message 保留本地 `reply_to_message_id=<fire-message-id>`；由于 system Message 没有
`provider_message_id`，`provider_reply_to_message_id` 为 `None`，Channel send 仍按 target 正常发送，
不尝试 provider-native reply。该路径不增加 Reminder-specific query predicate。

普通消息与 Reminder 在 `message check` 中按 `seq` 保持原顺序，不插入全局 footer。例如：

```text
[target=group:example msg=7a11c204 time=2026-08-25 09:30:00 type=human] @alice: 请确认发布流水线是否已经恢复。
[target=group:example msg=91bd02ef time=2026-08-25 09:30:05 type=system] @system: 🔔 Reminder #019c1234 (one-time) — group:example — "检查发布流水线状态"
(to snooze/cancel: bcc reminder --help)

Respond as appropriate. Complete all your work before stopping.
Reply in the channel or create/reply in a thread as appropriate; use each message's `target` and `msg` fields to choose the exact target.
[target=dm:@bob msg=c40e9a12 time=2026-08-25 09:30:08 type=human] @bob: 数据库迁移已完成，可以继续验证。
```

`message check` 推进 cursor 表示 Reminder 已交付给 agent，不表示业务动作已经完成。cursor 推进后，
它不再由 `message check` 返回，但仍可通过 `message read` 查看不含 agent instructions 的 durable
body。BCN 不增加 execution completion、自动重试或重新投递状态。这是本方案明确选择的 BCN
delivery 语义。

## 3. Cursor、catalog 与 runtime wake

### 3.1 Cursor

Reminder system Message 使用 `direction=inbound` 与 `notifies_runtime=true`，因此现有
`check_messages`、consumer cursor 和 fresh-check snapshot 可以直接覆盖它：

- `message check` 返回 provider inbound 与 system Reminder Message，并把
  `delivered_through_seq` / `inbox_snapshot_seq` 推进到本批次最新 inbox seq；
- system Message 是正常 inbound Message；如果它的 seq 高于 outbound snapshot，现有 freshness
  hold 正常包含它，并通过 history formatter 只显示 durable body，不追加 agent instructions；
- 任何新 inbound Message 的 seq 高于最近 snapshot 时，现有 freshness hold 继续生效；
- `message read` 不移动 consumer cursor，并正常返回 Reminder system Message。

`bcc inbox list` 把尚未消费的 Reminder system Message 计入 pending count；由于它也是 history
Message，latest Message、latest sender 与 activity time 也按现有 seq/activity 规则正常包含它，
因此可能显示 latest sender `@system`。Message repository 不增加 history-visible mode，不增加
`message_type`、metadata JSON 或新 column 的 Reminder filter。

### 3.2 统一 wake

删除 `_ReminderNotification` 与 `reminder_notice()`。ReminderScheduler 在 system Message 提交后，
调用普通 inbox wake callback。`SessionOrchestrator` 将该 wake 放入现有
`_RuntimeNotification` 队列；`completion` 对 Channel ingress 为 Future，对 scheduler wake 为
`None`。notification 同时保留 trigger Message 与可选 `wake_id`：Channel ingress 继续以
Message ID 作为 runtime attempt correlation，scheduler/recovery wake 每次生成新 UUIDv7 `wake_id`，
避免上一次 runtime attempt 已持久化但还没有执行 `message check` 时，startup recovery 被旧
`turn-<message-id>` 永久压制。`wake_id` 只是 runtime attempt correlation，不是新的业务 ID 或
Message 幂等键。

idle 与 active 两条路径都只调用 `inbox_notice()`：

- idle path 查询 owner session 当前全部 unread notifying inbound Message，以普通 content-free
  inbox notice 启动 turn；
- active path 查询同一 unread 集合，以普通 content-free inbox notice steer 当前 turn；
- system Message 与同时到达的 provider inbound Message 进入同一 batch/count/target delta；
- notice 的 changed row 从 system Message 读取 `latest sender @system`；
- runtime notice 不包含 Reminder title、cadence 或 next fire，agent 通过 `bcc message check`
  读取正文。

### 3.3 Crash recovery

ReminderScheduler startup recovery 不再识别 Reminder Message 类型，而是查询所有仍位于 consumer
cursor 之后且 `direction=inbound`、`notifies_runtime=true` 的 session，并为每个 session 返回其
最新一条 unread Message 作为 trigger，再用新 UUIDv7 `wake_id` 发布一次普通 inbox wake。该 generic
recovery 在 startup 的 overdue Reminder materialization 之前执行：已有 unread Message 先恢复 wake，
随后新 materialize 的 overdue Reminder 由正常 post-commit wake 覆盖，因此无需 system Message
分类字段或 metadata JSON predicate。

runtime 收到 recovery wake 后仍重新查询该 session 的全部 unread notifying Message；recovery 不创建
第二条 Message，也不修改 cursor。该恢复同时覆盖尚未 check 的 provider inbound 与 system Message，
不引入 Reminder-specific storage query。

如果 transaction 已提交但进程在 publish wake 前退出，startup recovery 会重新发布 wake；如果
wake 被重复发布，runtime 每次都重新查询 unread Message，已经被 `message check` 消费的 session
会 no-op。BCN 不要求 exactly-once wake，但 system Message 只允许 exactly-once 持久化。

## 4. 原子 fire 与 SQLite

### 4.1 Storage operation

用一个 repository-level compound operation 替换
`save_owned_fired_occurrence(expected_revision, reminder, occurrence)`：

```text
materialize_owned_reminder_message(
    expected_revision,
    owned_reminder,
    system_message,
) -> Message
```

该 operation 在 writer transaction 中按以下顺序执行：

1. 读取当前 Reminder，校验 owner、state、revision、scheduled slot 与 occurrence number。
2. 更新 Reminder：one-time 进入 `fired`；recurring 写入新的 `next_fire_at_ms`；两者都推进
   `revision`、`last_occurrence_no`、`last_fired_at_ms` 和 `updated_at_ms`。
3. 分配全局 Message seq，插入 system Message。
4. transaction commit 后才 publish ordinary inbox wake。

该 Message insert 不更新 `ChannelSession.last_inbound_at_ms`，也不触发 Channel ingress 的
following/mention 状态机；该 timestamp 继续只表达 Channel ingress。Message 自身按现有
seq/received-at 规则参与 unread pending、conversation latest Message 与 catalog activity time。

`materialize_owned_reminder_message` 由 single-writer actor 在 `BEGIN IMMEDIATE` transaction 中
执行。`revision`、`state`、`next_fire_at_ms` 与 `last_occurrence_no` 共同拒绝 stale fire；Reminder
状态推进与 Message insert 同事务提交或回滚。`last_fired_at_ms` 和 `last_occurrence_no` 保留为
durable fire 进度；不增加 Message 幂等字段、唯一索引或 event/log table。

### 4.2 Migration 19

新增 `v19_reminder_system_messages.py` 并注册为 schema version 19。migration 在一个 writer
transaction 中：

1. 将 `reminder_occurrences.read_at_ms IS NULL` 的现有 pending row 转为 unread system Message：
   - 复用 `occurrence_id` 作为迁移 Message ID；
   - 按 `(fired_at_ms, occurrence_id)` 排序，在当前最大 Message seq 后连续分配 seq；
   - 从 anchor Message 取得 session/channel/target 字段；
   - 按第 2.2 节的同一 formatter 构造 Message body；`next_fire_at_ms IS NULL` 使用 one-time
     模板，否则使用 recurring 模板，并使用 occurrence row 已持久化的 `next_fire_at_ms`；
   - 保持 `notifies_runtime=true`。
2. 已有 `read_at_ms IS NOT NULL` row 不再迁移，避免把已消费 occurrence 以新 seq 重新送进
   `message check`；因此 Reminder 的 Message history 从 migration 时仍 pending 的 occurrence 与
   v19 之后的新 fire 开始，不追溯重建更早的已读 occurrence。
3. 删除 occurrence indexes，再删除 `reminder_occurrences` table。

迁移完成后，schema、repository、codec、test support 和 runtime 中都不再存在 occurrence
model/read marker/table API。

## 5. 跨会话 send 与 Handoff System Message

### 5.1 Source freshness 与共享 draft

`bcc message send` 的 external contract 完全不变：`bcc.py` 仍只要求原有的
`--target <target>`，`_MessageSendRequest` 与 `ICommandService.send()` 不新增 source、
source-target、target-ID 或其他参数，stdin body、attachments、`--reply-to` 与
`--send-draft` 用法也不变。source 继续由现有 command context 的 `session_id` 提供，
target 继续由现有 `--target` 解析。变化只在内部：

```text
source_target_id = command context 中当前 BCN session A 的 stable ID
target_id = canonical --target 解析出的 BCN session stable ID，可能是 A 或 B
```

source session A 继续由现有调用上下文获得；不从 agent 接收 source ID。旧实现在
`source_target_id != target_id` 时返回 routing result；新实现不再以两者是否相等拒绝
发送，而是记录这两个 internal ID。freshness 永远属于 `source_target_id`，outbound
persistence/delivery 永远属于 `target_id`：

```text
A -> A: fresh check A, outbound A
A -> B: fresh check A, outbound B
```

B 的 consumer cursor、snapshot 和 unread Message 不参与 A 发起的 fresh check。跨会话 target 仍必须
由当前 Agent 的 inbox catalog 唯一解析；不能向其他 Agent 的 session 发送。

`SessionCommandService._drafts` 改为按 `source_target_id` key，每个来源会话只有一个 process-local
active draft。本会话与跨会话发送共享这个 draft；新正文、附件、reply target 或新 target 都替换 A
的旧 draft。`MessageDraft` 固定包含：

```text
source_target_id
target
target_id
body
attachments
reply_to_message_id
source_message_id
created_at_ms
```

`source_message_id` 也不是新的 CLI/request 参数。A 在 source draft/freshness 阶段内部查询
当时最新的 inbound Message，将其完整 canonical ID 写入 immutable draft。该值的精确
语义是“发起发送时的 source conversation context anchor”，只用于 Handoff check suffix
定位 source history；不声称 outbound 由该 inbound Message 精确因果触发。source session 在可
执行 `message send` 前已有 inbound conversation anchor，因此新 send 的该值必须非空。

`target_id` 是已解析的 stable BCN session ID，防止 `--send-draft` 时 target alias 被重新绑定；
CLI 仍要求原有的 `--send-draft --target <target>`，并同时校验 canonical target 与
internal `target_id`。delivery
为 `sent/queued` 后删除 A 的 draft；hold、failed、partial 与 unknown 保留 draft。进程重启后 draft
仍按现有合同丢失，不新增 durable draft table。

freshness hold 只读取 A 在 `inbox_snapshot_seq` 之后的 notifying inbound Message，返回现有
`MessageSendFreshnessHold` 与同一套 bounded context/draft 文案，不增加 cross-session outcome 或特殊
提示。hold 返回的 A context 已展示给当前 runtime，因此同时只推进 A 的
`inbox_snapshot_seq/inbox_snapshot_at_ms`，source 记为 `read`；`delivered_through_seq` 不动，之后
`message check` 仍能正常消费这些 Message。`--send-draft` 在 A 没有更新 inbound 时可以直接重试；
若 A 又收到新 Message，则再次返回同样的 hold。

### 5.2 Fresh check 与 outbound materialization 解耦

删除 `MessageSendHandoffRequired` 与 `format_cross_session_hold()`。原
`prepare_outbound(caller_session_id, ...)` 拆成两个明确阶段：

```text
check_outbound_freshness(
    source_target_id,
    source_snapshot_seq,
) -> FreshnessPass | MessageSendFreshnessHold

materialize_outbound_if_fresh(
    source_target_id,
    expected_source_seq,
    target_id,
    draft,
) -> pending outbound | MessageSendFreshnessHold
```

第一阶段只读/推进 A 的 freshness snapshot；第二阶段由 single-writer transaction 再次读取 A 的最新
inbound seq：

- 若高于 `expected_source_seq`，不创建 outbound，返回 A 的新 freshness hold；
- 若相等，以 target session B 的 `session_id`、`channel_session_id`、target kind、provider thread 与
 当前 seq 创建 pending outbound；local send 时 B 等于 A；
- reply target 仍在 outbound target session 内解析；跨会话 send 的 `--reply-to` 必须属于 B，不能
  引用 A 的 Message；
- A 的 lock 只保护 source draft 选择与首次 fresh check；得到 immutable attempt 后释放 A
  lock，再进入 B 的 per-target delivery lane；
- B lane 内的 writer transaction 同时 recheck A latest inbound seq 并 insert B pending
  outbound。A/B 不同时绝不同时持有两个 session lock；A/B 相同时复用同一 lane。

这样 fresh check 与 outbound owner 可以独立组合，但最终 recheck + pending insert 仍在一个 writer
transaction 内完成，不留下 fresh pass 后新 A inbound 穿透的 TOCTOU window。

### 5.3 直接 delivery 与 target-owned outbound

fresh pass 后，`SessionCommandService` 直接使用 B 的 `ChannelSession` 构造 `ChannelSendRequest` 并调用
当前 Agent 的 `OutboundDeliveryService`；不创建 Handoff row，也不等待或唤醒 B runtime 才发送。
outbound Message 完整归属 B，因此 B 的 `message read` history 正常包含实际发送内容。

跨会话 pending outbound 的 metadata 固定记录：

```text
source_target_id = A
target_id = B
source_message_id = A 在 command 开始时的 latest inbound Message
handoff_message_id = 预分配的 UUIDv7
```

这些 metadata 用于 delivery finalize、audit 与 system Message renderer。finalize 通过
`source_target_id` 解析 A 的 canonical target，再把该字符串写入 Handoff system Message
metadata 供 check formatter 使用；不参与 freshness SQL。
local A→A outbound 不写这些 cross-session metadata。

provider delivery 为 `sent` 或 `queued` 时，用 repository-level compound operation 在一个 writer
transaction 中完成：

1. 将 B 的 pending outbound 转为对应 delivery state 并保存 receipt；
2. 在 B 插入一条 `direction=inbound`、`message_type=text`、`sender_kind=system`、
   `system_message_kind=handoff`、`notifies_runtime=true` 的 Message；
3. system Message 使用预分配的 `handoff_message_id`，并在 metadata 保存 source target/message 与
   outbound Message ID。

Handoff system Message 的 sender、provider-null fields、attachments/reply 约束与第 2.1 节完全相同；
`session_id/channel_session_id/target/target_kind/channel/provider_thread_id` 全部取 B，不混用 A
envelope。

commit 后才为 B 发布普通 inbox wake。`failed/partial/unknown` 只保存 outbound outcome，不创建“已经
发送”的 Handoff system Message，也不唤醒 B。

### 5.4 Handoff Message 文本

新跨会话 send 成功后的 durable body 精确为：

```text
🤝 Handoff from <source-target> — message <outbound-message-id> was sent here from that conversation.
```

`<source-target>` 使用 A 的完整 canonical target，不使用 group-only ID，因此 DM、group 与 thread 都
能被 `bcc message read` 直接复用。`<outbound-message-id>` 是 sent/queued finalize 中同时
持久的 B-owned outbound Message 完整 canonical ID，不截短，因此 B 可用
`message read --around <outbound-message-id>` 直接定位实际发送内容且无 short-ID 歧义。该
ID 来自 provider call 前已创建的 pending outbound，不从 provider receipt 推断。history、
`--around`、freshness hold 与 catalog 只显示 durable body。

当 `message check` 收到 `system_message_kind=handoff` 时，在 body 后追加：

```text
To understand why this message was sent, inspect the source context:
  bcc message read --target "<source-target>" --around "<source-message-id>"
If you have no objection to why the message was sent, do not announce or explain the handoff, and do not repeat or respond to the referenced message; it has already been delivered. Continue only work already in progress in this conversation that is independent of that message; if there is none, stop.
Mention the handoff only when its reason is unclear, conflicts with the current conversation, or requires a decision.
```

该 suffix 的两个参数来自已验证 metadata；formatter 使用 JSON quoting 生成可直接执行的 CLI 参数。
suffix 中的后两句是 outcome-oriented 行为约束：Handoff 本身不是自动接手 source
work 的指令。无异议时不机械回报“收到 Handoff”，也不重复或响应已经交付的 referenced
message；只继续 B 的当前 conversation 中独立于该 message、已经在进行的 work。若没有这类
work 则结束。只在 reason 不清、与当前 conversation 冲突或需要 decision 时才提及 Handoff。
整个 suffix 不持久化，不在 `message read` 中重复。
`system_message_kind=reminder` 继续使用第 2.3 节的 Reminder suffix，两者不通过正文前缀猜类型。

### 5.5 Crash、retry 与旧 Handoff migration

pending outbound 的唯一 `command_id` 保持现有 command/outbound identity 与 conflict boundary，不声称
provider call exactly-once。预分配并随 pending outbound 持久化的 `handoff_message_id` 使已有
delivery evidence 的 finalize 重试只能插入同一条 system Message。compound
operation 遇到已存在的同 ID Message时必须校验 session、source/outbound metadata 与 body 完全一致，
不静默接受冲突。

- provider call 前崩溃：outbound 保持 pending，且没有 system Message；
- provider 已返回 sent/queued 但 finalize transaction 未 commit 时崩溃：outbound 仍是 pending，
  不创建可能误导 B 的 system Message；沿用现有 unknown/reconciliation 边界，不自动重发
  完整 provider payload；
- transaction commit 后、wake 前崩溃：outbound final state 与 system Message 已同时持久化，第
  3.3 节 generic unread recovery 重发 ordinary inbox wake；
- 重复 finalize：复用相同 `handoff_message_id`，不产生第二条 system Message。

新增 migration 20 `v20_remove_handoffs.py`。对旧 `handoffs` table：

1. `read_at_ms IS NULL` row 转为 target session 的 unread system Message，复用 `handoff_id` 作为
   Message ID，并保留 source target/message metadata。旧 row 的 `source_message_id` 非空时原样
   保留；为空时使用 source session 在 `created_at_ms` 当时已存在的最新 inbound
   Message，若旧数据违反 Handoff 创建前必须存在 source anchor 的不变量则 migration
   明确失败，不伪造 `--around` ID。migration-only durable body 为：

   ```text
   🤝 Handoff from <source-target>
   <original-handoff-body>
   ```

   这类 Message 使用同一个 check-only source-context suffix，使旧 pending work 不丢失；不伪造
   provider outbound 或 `<outbound-message-id>`。
2. 已读 row 不迁移，避免重新交付已消费 work。
3. 删除 Handoff index 与 `handoffs` table；删除 schema、codec、repository、test support 中的全部
   Handoff persistence API。

## 6. CLI surface 减量

Reminder CLI 最终只包含：

```text
bcc reminder schedule
bcc reminder list
bcc reminder snooze
bcc reminder update
bcc reminder cancel
```

`bcc handoff` resource 整体删除；跨会话发送只通过普通 `bcc message send --target
<target>` 进入第 5 节的直接 delivery 链路。精确删除：

- `bcc.py` 中 `reminder check` parser/help/dispatch、`serialize_reminder_check` 及 mapping；
- `bcc.py` 中整个 `handoff` parser tree、stdin 规则、request mapping、strict response
  models/validators、`serialize_handoff_send`、`serialize_handoff_check` 及 serializer mapping，并从
  resource metavar 和 stdin-resource set 删除 `handoff`；
- `app/resource_dispatch.py` 中 `_ReminderCheckRequest` 及 occurrence response；
- `app/resource_dispatch.py` 中 Handoff imports、`serialize_handoff`、`_HandoffSendRequest`、
  `_HandoffCheckRequest`、schema table、service dependency、resource branch、`_dispatch_handoff` 与 Handoff
  error mapping；
- `core/command.py` 中 `IReminderService.check`、`IHandoffService`、Handoff contracts imports，以及
  `MessageSendHandoffRequired` 和它在 `MessageSendResult` 中的 union member；
- `core/reminder.py` 中 `ReminderCheckRequest`、`ReminderCheckItem`、`ReminderCheckResult`，以及
  `core/orchestration/reminder_command.py` 中 `ReminderCommandService.check`；
- 整个 `core/handoff.py`、`core/models/handoff.py`、
  `core/orchestration/handoff_command.py`，以及对应 package exports；
- `core/storage.py` 中 Reminder occurrence check/wake ports、`HandoffConflictError`、
  `HandoffWakeResult`、`check_handoffs`、`load_handoff_wake` 与整个 `IHandoffStorageScope`；
- `app/agent.py` 中 Handoff storage cast、`HandoffCommandService` composition、resource-dispatch
  injection 与 `publish_handoff_wake`；`app/application.py` 与 orchestration 中对应 wake wiring；
- `app/command.py` 中 `MessageSendHandoffRequired` response branch 与
  `format_cross_session_hold()`；跨会话成功/失败使用普通 message-send response；
- SQLite 的 `handoff_codec.py`、`repository/handoffs.py`、repository facade mixin、storage
  exports/scope 与 Handoff protocol imports；旧 v15 migration ledger 保持不变，v20 完成迁移后
  删除当前 schema 的 Handoff table/index；
- `tests/test_bcc_handoff.py`、`tests/core/test_handoff.py`、
  `tests/contrib/test_sqlite_handoff.py`，以及 help/app/process/dispatch/orchestration/storage tests 中的
  Handoff resource、notice、wake、read marker 和 routing-result assertions；
- Reminder help/CLI/app tests 中所有 `check` 成功输出、empty/more 与 occurrence payload
  assertions。

`bcc reminder check` 在 argparse 层成为未知 command，`bcc handoff` 成为未知 resource。
两者都不保留 alias、deprecated shim 或替代提示。

## 7. Developer instructions 减量

`src/bazaar_compute_node/core/instruction.py` 只做删除与由删除引起的编号、标点、连词收口；不增加
Reminder/Handoff 替代说明，也不把第 2.3/5.4 节的 check-only suffix 写入 developer
instructions。精确删除清单如下：

1. Communication command family 删除整行
   ``3. **Handoffs** — `bcc handoff send`, `bcc handoff check`.``；Reminder 行删除
   `` `bcc reminder check`, ``；保留行机械重新编号。
2. Critical rules 从
   ``Use only the provided `bcc` commands for messaging, handoffs, and Reminder management.``
   删除 ` handoffs,`。
3. Startup sequence 整项删除以下两行，并把后续项机械重新编号：

   ```text
   3. If there is no concrete incoming message to handle but this turn includes a reminder notice, run `bcc reminder check` to inspect the pending Reminder occurrences. The notice is only a wake hint and does not contain the Reminder title or task details.
   4. If there is no concrete incoming message or reminder notice to handle but this turn includes a handoff notice, run `bcc handoff check` to inspect the pending handoffs. The notice is only a wake hint and does not contain the handoff task or source details.
   ```
4. Startup inbox step 从首个条件和 `neither` 条件各删除 reminder/handoff notice 条件；
   从结尾删除 `Handoff wakes, and Reminder wakes`，并只收口为完整句子。
5. Startup receive/inspect step 整句删除
   `When you inspect a due Reminder or handoff, continue the returned work and send an external
   message only if the task now calls for one.`
6. Startup completion step 从 wake 枚举删除 `Handoff wakes, and Reminder wakes`，并只收口
   为完整句子。
7. Startup 后的 `IMPORTANT` 行从 notice 枚举删除 `handoff notice, or reminder notice`，
   从 command 枚举删除 `bcc handoff check, or bcc reminder check`，并只收口为单一 inbox
   分支的完整句子。
8. Messaging 整段删除：
   ``If bcn says the target belongs to another conversation, use `bcc handoff send --target
   "<target>"` only when work should continue there. Make the handoff self-contained with enough
   background, the goal, and the next action for the target conversation.``
9. `### Reminders` 第一段只删除
   `does not send a message or system receipt to the anchored DM/thread and `；保留 session
   ownership、Channel call boundary 与后续主动通知说明。
10. `### Reminders` 整段删除以 `When a reminder notice wakes you` 开始、以
    `or create another one as appropriate.` 结束的 occurrence check/read-marker 段落。
11. 整个 `### Handoffs` section 删除，精确原文为：

    ```text
    ### Handoffs

    Use `bcc handoff send --target "<target>"` when work should continue in another conversation. Use `bcc inbox list` if you need to find the target, and pass the task through stdin. Add `--message-id "<message-id>"` when the task refers to a specific inbound message in the current conversation.

    When a handoff notice wakes you, run `bcc handoff check` at a natural breakpoint. Continue the returned tasks in the current conversation. Use the supplied source target and optional source message with `bcc message read` when you need more context.
    ```
12. Conversation etiquette 的 blocker bullet 只删除 `handoff, `，保留 review/decision/reply
    列表。
13. `## Runtime Notifications` 首段从 wake 枚举删除 handoff/Reminder 两项，并整句
    删除 `These are separate notice types; bcn does not combine their counts into one notice.`
14. 删除以下完整 Handoff notice block：

    ````text
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
15. 删除以下完整 Reminder notice block：

    ````text
    Reminder notice shape:

    ```text
    [reminder notice session=<session-id>]
    Reminders pending: <n>. Use `bcc reminder check` to read them.
    ```

    How to handle Reminder notices:
    - Treat the notice as a wake hint that one or more durable Reminder occurrences are pending. The notice deliberately omits titles, anchors, and occurrence details.
    - Run `bcc reminder check` to inspect the pending occurrences. If the notice arrives while you are already working, do this at a natural breakpoint rather than assuming the notice itself contains enough information.
    - A Reminder notice is not a channel message notice. Do not call `bcc message check` merely because a Reminder fired, and do not infer that another human or agent was notified.
    - Reminder firing never sends an external Channel message by itself. Only use `bcc message send` when the follow-up task actually requires an external message.
    ````

对应 instruction tests 断言：

- command family 不存在 Handoff，Reminder 精确包含五个保留 commands；
- rendered instructions 不包含 `bcc handoff`、`bcc reminder check`、`[handoff notice`、
  `[reminder notice`、`Handoffs pending:`、`Reminders pending:`、`separate notice types`、Handoff
  read-marker 或 Reminder occurrence read-marker 语义；
- 现有 message notice 文本继续与 `inbox_notice()` 输出一致；
- `### Reminders` 保留 schedule、anchor、snooze/update/cancel 和 Channel 边界的现有
  文本，不增加新句子。

## 8. 目标文件

```text
src/bazaar_compute_node/
├── core/
│   ├── models/
│   │   ├── entities.py                 # system Message validation and metadata
│   │   ├── states.py                   # SenderKind.SYSTEM and SystemMessageKind
│   │   ├── reminder.py                 # remove ReminderOccurrence
│   │   ├── reminder_owner.py           # remove OwnedReminderOccurrence
│   │   └── handoff.py                  # delete old Handoff model
│   ├── command.py                      # shared source draft and direct cross-session result
│   ├── reminder.py                     # remove check contracts; render fire body
│   ├── handoff.py                      # delete old resource contracts
│   ├── storage.py                      # compound Message operations and generic recovery
│   ├── instruction.py                  # exact deletions from section 7
│   └── orchestration/
│       ├── command.py                  # A-only freshness; B-owned outbound/delivery
│       ├── delivery.py                 # atomic finalize plus Handoff Message
│       ├── reminder.py                 # materialize Message and publish ordinary wake
│       ├── reminder_command.py         # remove check
│       ├── handoff_command.py          # delete old command service
│       ├── session.py                  # one RuntimeNotification path
│       └── turn.py                     # remove dedicated notice formatters
├── contrib/sqlite/
│   ├── codec.py                        # system Message codec/validation
│   ├── reminder_codec.py               # remove occurrence codec
│   ├── handoff_codec.py                # delete old Handoff codec
│   ├── migrations/
│   │   ├── registry.py                 # register v19 and v20
│   │   ├── v19_reminder_system_messages.py
│   │   └── v20_remove_handoffs.py
│   ├── repository/messages.py          # system Message and compound outbound operations
│   ├── repository/reminders.py         # atomic fire
│   ├── repository/handoffs.py          # delete old repository
│   ├── repository/facade.py            # remove Handoff mixin
│   └── storage.py                      # remove occurrence/Handoff exports
├── app/
│   ├── command.py                      # system check suffixes; remove routing result
│   ├── agent.py                        # ordinary wake and remove Handoff wiring
│   ├── application.py                  # scheduler/message wiring only
│   └── resource_dispatch.py            # remove Reminder check and Handoff resource
└── bcc.py                              # five-command Reminder CLI; no Handoff resource

tests/
├── app/                                # direct-send/dispatch/composition/process behavior
├── contrib/
│   ├── test_orchestration.py           # unified ordinary notices
│   ├── test_sqlite_database.py         # v19/v20 schema and migration
│   └── test_sqlite_handoff.py          # delete old repository tests
├── core/
│   ├── test_instruction.py             # exact deletion assertions
│   ├── test_reminder.py                # body/model contracts
│   ├── test_reminder_scheduler.py      # atomic fire/recovery
│   └── test_handoff.py                 # delete old resource tests
├── support/src/bcn_test_support/
│   ├── storage.py                      # remove Handoff scope; support compound Message APIs
│   └── reminder_storage.py             # Message-backed reminder test storage
├── test_bcc_help.py                     # no Handoff; Reminder check rejected
├── test_bcc_handoff.py                  # delete old CLI tests
└── test_bcc_reminder.py                 # retained command serializers only
```

## 9. 串行实施任务

### Task 1 — System Message foundation 与 additive storage contracts

实现：

- 增加 `SenderKind.SYSTEM`、metadata-only `SystemMessageKind` 与 system Message validation/codec；
- 在 `core/reminder.py` 增加无 I/O fire-body renderer，严格实现第 2.2 节 durable
  body，不包含 check-only suffix；
- 在尚未切换 scheduler 的前提下，additive 增加 `materialize_owned_reminder_message`，实现
  Reminder transition + Message insert 原子 transaction；旧 occurrence operation/table 暂时保留，使
  Task 1 结束时现有 scheduler/CLI 仍完整可运行；
- 增加只按 direction、`notifies_runtime` 和 consumer cursor 查询的 generic unread owner
  query，不使用 metadata SQL filter；
- 为 Reminder/Handoff 两种 metadata kind 增加 domain validation 和 formatter test vectors，但此 Task
  不改变当前 runtime/CLI 行为。

Focused checks：

```bash
uv run pytest tests/core/test_reminder.py tests/core/test_models.py tests/contrib/test_sqlite_database.py -q
uv run ruff check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/core/test_reminder.py tests/contrib/test_sqlite_database.py
uv run ruff format --check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/core/test_reminder.py tests/contrib/test_sqlite_database.py
uv run scripts/pyright_lsp_check.py --outputjson .
```

完成后发送业务 diff 与验证结果，停在 review。

### Task 2 — Reminder cutover 与统一 inbox wake

实现：

- scheduler 调用 Task 1 renderer 构造 exact Reminder system Message body；
- fire commit 后 publish ordinary inbox wake；
- 增加 migration 19，以 migration-local 冻结 renderer 迁移 pending occurrence，跳过已读 row，
  然后 drop occurrence indexes/table；以共享 test vectors 验证 migration/runtime body 合同
  一致；
- 删除 occurrence domain/owner/codec/storage API 与 Reminder `check` core/app/CLI surface；
- startup recovery 从所有 unread notifying inbound Message owner 恢复 wake，不增加 system
  Message discriminator；
- 删除 `_ReminderNotification` 与 `reminder_notice()`；
- `_RuntimeNotification` 支持无 completion 的 scheduler wake 与独立 `wake_id`；
- idle/start 与 active/steer 都使用 `inbox_notice()`，并覆盖普通 Message 与 Reminder
  混排、restart recovery 和重复 wake no-op；
- 执行第 7 节中仅与 Reminder notice/check 有关的 deletion-only instruction 项，保留旧
  Handoff instruction 直到 Task 4；
- 验证 history/around、freshness、catalog pending/latest/sender/activity 都按普通 inbound
  Message 语义包含 Reminder，且只有 `message check` 追加 Reminder suffix。

Focused checks：

```bash
uv run pytest tests/core/test_reminder.py tests/core/test_reminder_scheduler.py tests/contrib/test_sqlite_database.py tests/contrib/test_orchestration.py tests/test_bcc_help.py tests/test_bcc_reminder.py tests/core/test_instruction.py tests/app -q
uv run ruff check src/bazaar_compute_node/bcc.py src/bazaar_compute_node/core src/bazaar_compute_node/app src/bazaar_compute_node/contrib/sqlite tests
uv run ruff format --check src/bazaar_compute_node/bcc.py src/bazaar_compute_node/core src/bazaar_compute_node/app src/bazaar_compute_node/contrib/sqlite tests
uv run scripts/pyright_lsp_check.py --outputjson .
```

完成后发送业务 diff 与验证结果，停在 review。

### Task 3 — 跨会话 direct send 与 Handoff System Message

实现：

- 保持 `bcc message send` parser、`_MessageSendRequest`、`ICommandService.send()` 与 stdin
  mapping 参数集完全不变，增加 contract test 断言不存在 source/source-target/target-ID
  CLI 或 request field；
- 将 draft key 改为 source session A，实现第 5.1 节的单 draft、target/session 校验与
  sent/queued 清理规则；
- 拆分 A-only freshness 与 B-owned outbound materialization；A→B 时只读取/推进 A snapshot，
  绝不读取或改写 B cursor/unread；
- 在 transaction 内 recheck A latest seq 后才插入 B pending outbound，并在 B 的 delivery lane
  使用 B ChannelSession 直接调用 Channel；
- 使用 compound finalize 在 sent/queued 时原子保存 B outbound final state 和第 5.4 节
  Handoff system Message，commit 后发布 ordinary B inbox wake；
- 增加 exact tests 覆盖 A→A、A→B、A 的 freshness hold/retry、B cursor 完全不参与、
  reply target 必须属于 B、同一 source draft 替换、delivery failure 不产生 Handoff Message、
  finalize retry 不重复 Message，以及 durable body 嵌入正确的完整 B outbound Message ID；
- 验证 `message check` 为 Handoff Message 追加第 5.4 节的完整 source-context/action suffix，
  exact-output test 包含“无异议时不评论 Handoff、只继续 B 当前已有 work、无当前
  work 则结束”与三个例外条件，而
  `message read`/`--around`/freshness/catalog 只显示 durable body。

Focused checks：

```bash
uv run pytest tests/app/test_command_resource.py tests/contrib/test_orchestration.py tests/contrib/test_sqlite_database.py -q
uv run ruff check src/bazaar_compute_node/core src/bazaar_compute_node/app src/bazaar_compute_node/contrib/sqlite tests/core tests/app tests/contrib/test_orchestration.py tests/contrib/test_sqlite_database.py
uv run ruff format --check src/bazaar_compute_node/core src/bazaar_compute_node/app src/bazaar_compute_node/contrib/sqlite tests/core tests/app tests/contrib/test_orchestration.py tests/contrib/test_sqlite_database.py
uv run scripts/pyright_lsp_check.py --outputjson .
```

完成后发送业务 diff 与验证结果，停在 review；此时旧 Handoff resource/table 暂时仍可
共存，Task 4 才删除。

### Task 4 — 删除旧 Handoff surfaces 并完成 instruction 减量

实现：

- 执行第 6 节尚未在 Task 2 完成的 Handoff CLI/app/core/storage/test 删除清单；
- 执行第 7 节尚未在 Task 2 完成的 Handoff developer-instruction 精确删除
  清单；
- 增加 migration 20，按第 5.5 节迁移 old pending Handoff rows、不重投已读 row，然后
  drop Handoff indexes/table；
- 删除剩余的专用 Handoff notice/wake，只保留 Task 2 已建立的 generic ordinary
  inbox wake/recovery；
- 增加 Reminder/Handoff `message check` exact-output tests，断言各自 check-only suffix；
- 增加 `message read --around`、history window 与 freshness exact-output tests，断言只显示对应
  durable body；
- 验证 reply-to system Message 保留 local relation，provider reply ID 为 `None`；
- 验证 help/parser 拒绝 `bcc reminder check` 与整个 `bcc handoff` resource，且 instruction 无
  新增文案。

Focused checks：

```bash
uv run pytest tests/test_bcc_help.py tests/test_bcc_reminder.py tests/test_bcc.py tests/core/test_instruction.py tests/app tests/contrib/test_sqlite_database.py -q
uv run ruff check src/bazaar_compute_node/bcc.py src/bazaar_compute_node/app src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests
uv run ruff format --check src/bazaar_compute_node/bcc.py src/bazaar_compute_node/app src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests
uv run scripts/pyright_lsp_check.py --outputjson .
```

完成后发送业务 diff 与验证结果，停在 review。

### Task 5 — 全量验收

执行：

```bash
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

额外静态验收：

```bash
rg -n "ReminderOccurrence|OwnedReminderOccurrence|reminder_notice|MessageSendHandoffRequired|format_cross_session_hold|IHandoffService|IHandoffStorageScope|HandoffCommandService|HandoffWakeResult|handoff_notice|publish_handoff_wake" \
  src/bazaar_compute_node --glob '!**/migrations/v12_add_reminders.py' --glob '!**/migrations/v15_add_handoffs.py' --glob '!**/migrations/v19_reminder_system_messages.py' --glob '!**/migrations/v20_remove_handoffs.py'
rg -n "bcc reminder check|bcc handoff|Reminders pending:|Handoffs pending:|\[reminder notice|\[handoff notice" src
```

两条命令都必须无匹配。旧 migration ledger 保持不可变；确认 v20 schema 不存在
`reminder_occurrences` 或 `handoffs`，Reminder CLI help 只列五个命令，且无 Handoff resource。
确认 A→B 发送只用 A freshness，B 拥有 outbound/history 且收到 Handoff system Message；确认
`message check` 按 system kind 追加正确 suffix，而 `message read`/`--around`/freshness/catalog
只显示 durable body；确认 reply target 保留 local relation 且不产生 provider-native reply。最后发送
业务 diff 和验证结果，停在 final review。
