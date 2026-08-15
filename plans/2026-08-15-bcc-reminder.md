# 2026-08-15 BCC Reminder Plan

## 状态

- 模式：Plan
- 状态：待 review；review 通过后只进入 Phase 1 Task 1A。
- 基线：`main`，当前提交为 `3d62d89e3d84a8ce43b71a9468d8c12c9b90c129`。
- 当前更新只定义 Reminder 的 domain、SQLite、scheduler、runtime wake、`bcc` 命令和
  developer instruction 边界，不修改生产代码。
- 所有 Task 按本文顺序串行实施；每完成一个 Task，运行该 Task 的 focused checks，发送业务
  diff 并停在 review，未经 review 不进入下一 Task。

## 1. 目标

在现有 message inbox、per-session runtime queue、Codex App Server `turn/start` /
`turn/steer` 和共享 `TimerWheel` 基础上增加 session-owned Reminder。Reminder 由当前
`bcn_session_id` 创建并绑定当前 session 内的一条 inbound message；它不向外部 Channel
发送内容，也不修改 thread/group 的 following 状态。

首个纵向切片的业务链路是：

```text
agent receives one channel message
    -> bcc reminder schedule --message-id <inbound-message-id>
    -> SQLite persists reminder definition
    -> one node-wide ReminderScheduler arms one frontier TimerWheel timer
    -> scheduler atomically materializes due reminder occurrence
    -> ReminderWake enters the existing per-session runtime queue
    -> idle runtime uses turn/start; active runtime uses turn/steer
    -> runtime receives one reminder-only notice
    -> agent calls bcc reminder check
    -> returned occurrences are marked read
```

首版必须满足以下产品语义：

1. Reminder owner 固定为创建命令所在的 `bcn_session_id`。runtime session 可以因 idle
   timeout、进程重建或 provider 恢复而变化，Reminder ownership 不随 runtime session
   identity 变化。
2. Reminder anchor 只能是当前 `bcn_session_id` 中已经持久化的 inbound message。
   `--message-id` 接受完整本地 UUID 或唯一 short prefix；解析后持久化完整本地
   `message_id`。
3. Reminder 不调用 `IChannel.send`，不产生 Channel receipt，不向 thread/DM 主动发送
   system message，也不修改 `ChannelSession.following`。anchor 只承担上下文、归属和历史定位。
4. `bcc` 首版提供：
   `reminder schedule/check/list/snooze/update/cancel`。不提供通用 App inbox，也不提供
   `reminder log`。
5. Message 和 Reminder 使用各自的持久化 pending/read 语义，但共用现有
   `SessionOrchestrator._runtime_queues`、同一个 per-session worker 和同一套
   `turn/start` / `turn/steer` 决策。Runtime adapter 不增加第二套 inbox API。
6. runtime 只接收两种互斥 notice：
   message wake 使用现有 message inbox notice；Reminder wake 使用 reminder notice。
   两种 wake 分别入队、按队列顺序串行处理，不构造跨类型合并 notice。
7. Reminder scheduler 全 node 只维护一个活动 TimerWheel timer。SQLite 可以保存任意数量
   future Reminder；event loop 不为每个 Reminder 创建 Timer 或 watcher task。
8. SQLite 中的 UTC wall-clock `next_fire_at_ms` 是调度事实源；TimerWheel 只负责最近一次
   进程内唤醒。BCN 重启后不恢复旧 Timer，而是从 SQLite 重建 frontier。
9. TimerWheel 单次 horizon 之外的 Reminder 通过 bounded re-arm 解决，不修改现有
   TimerWheel bit layout。scheduler 每次醒来都重新读取 wall clock 和 SQLite，不把
   monotonic deadline 持久化。
10. Reminder 支持 one-time 和以下 recurrence：
    `every:15m`、`every:2h`、`every:1d`、`daily@09:00`、
    `weekly:mon,fri@09:00`。时间计算只使用 Python 标准库 `datetime` 与 `zoneinfo`。
11. BCN 启动时，所有 `next_fire_at_ms <= now` 的 Reminder 都进入 overdue recovery。
    one-time 产生一条 occurrence；recurring 按原 scheduled slot 逐条补齐 missed
    occurrences，使用有界 batch 并让出 event loop，不静默丢弃 backlog。
12. 每次 fire 都产生独立 `ReminderOccurrence`。`pending` 表示该 occurrence 尚未被 agent
    通过 `bcc reminder check` 查看，不表示 Reminder 所描述的业务工作尚未完成。
13. `bcc reminder check` 使用 drain 语义：在一个显式事务中读取一批 pending occurrences，
    将这一批标记为 read，然后返回 canonical text。它不推进 message cursor，也不影响
    `bcc message send` 的 fresh-check snapshot。
14. 一次性 Reminder fire 后进入 `fired`；`snooze` 可以把 `scheduled` 或 `fired`
    Reminder 重新安排为 `scheduled`；`update` 和 `cancel` 只允许操作 `scheduled`
    Reminder。
15. 首版不承诺 exactly-once runtime notice。durable occurrence 是事实源；wake 可以
    at-least-once，消费前重新查询 pending count，过期 wake 静默 no-op。

## 2. 已确认的边界与首版范围

### 2.1 已确认的边界

- 现有 `TimerWheel` 使用 10 ms tick、monotonic clock 和单一 driver task；单次最大 delay
  约为 497 天。Reminder 不扩大它的层级，不为 future Reminder 保存 monotonic deadline。
- 现有 Channel ingress 先完成 inbound 持久化，再按 DM、following、mention 决定
  `notifies_runtime`。普通 message 只有在这一过滤通过后才进入 runtime queue。
- Reminder 不伪装为 `InboundMessage`，也不进入上述过滤表达式；它在
  `notifies_runtime` 决策之后的公共 runtime wake boundary 接入同一个 session queue。
- Message wake 和 Reminder wake 不互相查询、不互相消费，也不合并 notice。即使二者同时
  发生，也只是两个 queue item，先入先处理。
- active turn 收到 ReminderWake 时复用现有 `IRuntime.steer_turn`；idle/no-live-runtime
  收到 ReminderWake 时复用现有 runtime establishment 与 `IRuntime.start_turn`。
- Reminder fire 不重新打开 unfollowed thread。thread/group following 只由普通 Channel
  inbound mention 和 `bcc thread unfollow` 状态机管理。
- `--channel` 不进入 BCN Reminder CLI。BCN 的 `channel` 是 adapter 概念，不是 Reminder
  delivery destination；thread/DM 归属已经由 owner session 与 anchor inbound 确定。
- deprecated `--msg-id` 不进入 BCN CLI，只保留 `--message-id`。
- schedule 的 `--title` 和 `--message-id` 必填；`--delay-seconds` 与 `--fire-at`
  互斥；至少需要 relative/absolute first fire 或一条能够计算 first fire 的 recurrence。
- `--tz` 使用 IANA timezone。未提供时固定使用 `UTC`，不依赖 daemon host 的隐式时区。
  timezone 始终持久化，因此后续 `update --cadence` 可以沿用已有 timezone。
- `every:*` 是 elapsed interval；`every:1d` 固定为 24 小时，和 calendar-based
  `daily@...` 不同。
- recurrence 的下一次时间从当前 occurrence 的 scheduled slot 计算，而不是从实际
  `fired_at_ms` 计算，避免正常延迟造成 cadence 漂移。
- snooze 会重设下一次 scheduled slot：
  - `scheduled` Reminder：`next_fire_at_ms += duration`；
  - `fired` Reminder：`next_fire_at_ms = now + duration`。
  下一次 fire 后，recurrence 从该 snoozed slot 继续计算；中间被 snooze 跨过的 slot 不补发。
- `update --fire-at` / `update --in` 重设下一次 scheduled slot；
  `update --cadence` 保持当前 next 不变，并在当前 next fire 后应用新 cadence；
  `update --title` 只修改 title。
- `update` 一次只允许修改一个字段；fired Reminder 直接 update 返回稳定错误，提示先
  snooze 或创建新 Reminder。
- `cancel` 只允许 scheduled Reminder；重复 cancel 不伪装成成功的状态变化。
- `list` 默认返回 `scheduled,fired`；`--all` 增加 `canceled`；`--status` 接受显式
  comma-separated statuses。
- `check` 默认每次最多 drain 100 条 occurrence。剩余 pending 时输出明确提示，让 agent
  再次调用；没有 pending 时输出稳定的空结果。
- 删除 `reminder log` 只删除用户可见 lifecycle history 和专用 event table，不删除
  `reminder_occurrences`。occurrence 表仍承担 durable fire、pending/read、crash recovery
  和 recurrence identity。

### 2.2 首版范围

首版只实现 Reminder domain，不预先建立通用 `AppInboxItem`、`app/class/item/action`
扩展协议。内部 runtime queue 可以区分 message/reminder wake kind，但持久化与 CLI 保持
Reminder-specific：

```text
message inbox:
    inbound_messages + consumer_cursors
    bcc message check/read

reminder inbox:
    reminders + reminder_occurrences
    bcc reminder check/list
```

首版 recurrence grammar 仅接受本文列出的规则，不接受 cron expression、自然语言时间、
秒级 recurrence、月份规则或任意 RRULE。新增 grammar 必须另行计划，不能把未知文本交给
第三方 parser 猜测。

首版不增加 Reminder retention 删除。read occurrence 保留为本地 fire history和去重事实；
未来若需要归档或清理，必须先定义 pending 边界、recurrence identity 和恢复语义。

## 3. 目标模块边界

依赖方向继续保持 `app -> contrib -> core`；core 不依赖 SQLite、Codex、WeCom 或 CLI。

```text
src/bazaar_compute_node/
├── core/
│   ├── models/
│   │   ├── entities.py        # Reminder / ReminderOccurrence
│   │   └── states.py          # ReminderState
│   ├── reminder.py            # recurrence、duration、service/result contract
│   ├── storage.py             # reminder repository port
│   └── orchestration/
│       ├── reminder.py        # one-frontier ReminderScheduler
│       ├── session.py         # shared runtime queue and ReminderWake
│       ├── turn.py            # message/reminder notice injection
│       └── command.py         # ReminderCommandService
├── contrib/
│   └── sqlite/
│       ├── migrations.py      # schema version 12
│       ├── codec.py           # reminder row codec
│       └── repository.py      # reminder/occurrence operations
├── app/
│   ├── application.py         # lifecycle/composition wiring
│   └── command.py             # local request dispatch/serialization
├── bcc.py                     # reminder CLI parser and canonical output
└── core/instruction.py        # command family and Reminder behavior

tests/
├── core/
│   ├── test_reminder.py
│   ├── test_reminder_scheduler.py
│   └── test_instruction.py
├── contrib/
│   ├── test_sqlite_reminder_repository.py
│   └── test_orchestration.py
├── app/
│   └── test_bcc_process.py
└── support/
    └── src/bcn_test_support/storage.py
```

具体文件拆分可以在 Code 模式根据当前文件体积调整，但不得：

- 让 core import `aiosqlite`、Codex protocol type 或 Channel provider type；
- 为每个 Reminder 创建独立 runtime worker、TimerWheel 或 scheduler task；
- 把 Reminder 命令塞进 `IChannel`；
- 用 `InboundMessage` 的假行承载 Reminder occurrence；
- 为了测试在 production composition 增加环境开关、factory override 或假 provider。

## 4. 核心数据模型与 SQLite

### 4.1 Reminder definition

在 `core.models` 增加 immutable `Reminder`：

```text
reminder_id
owner_session_id
anchor_message_id
title
state
next_fire_at_ms
repeat_rule
timezone
revision
last_occurrence_no
created_at_ms
updated_at_ms
last_fired_at_ms
canceled_at_ms
```

字段语义固定如下：

- `reminder_id`：repository 生成的 UUIDv7。
- `owner_session_id`：创建命令所在的稳定 `bcn_session_id`。
- `anchor_message_id`：同一 owner session 中的完整 inbound local message ID。
- `title`：非空 Unicode text；拒绝 CR/LF 和控制字符，避免 canonical CLI envelope 被拆行。
- `state`：`scheduled`、`fired`、`canceled`。
- `next_fire_at_ms`：
  - scheduled 必须非空；
  - fired/canceled 必须为空。
- `repeat_rule`：规范化后的 recurrence string；one-time 为 `None`。
- `timezone`：IANA name；默认 `UTC`，即使 one-time 也持久化。
- `revision`：从 1 开始；schedule 之后的 snooze/update/cancel/fire 每次成功状态变化都加一。
- `last_occurrence_no`：从 0 开始；每次成功 materialize occurrence 加一。
- `last_fired_at_ms`：最近一次 occurrence 实际落库时间。
- `canceled_at_ms`：只在 canceled 时存在。

model/repository 校验 required field、状态组合、时间单调性、owner/anchor 归属和 revision；
SQLite DDL 不用 trigger 替代 domain state machine。

### 4.2 Reminder occurrence

每次 fire 创建 immutable identity + mutable read marker 的 `ReminderOccurrence`：

```text
occurrence_id
reminder_id
owner_session_id
occurrence_no
anchor_message_id
scheduled_for_ms
fired_at_ms
next_fire_at_ms
overdue
read_at_ms
created_at_ms
```

字段语义：

- `occurrence_id`：repository 生成 UUIDv7，作为 runtime wake source 和
  `client_user_message_id`。
- `(reminder_id, occurrence_no)`：application-level 唯一 identity；occurrence number 从 1
  单调递增。
- `scheduled_for_ms`：本次 cadence slot，不能被实际处理延迟覆盖。
- `fired_at_ms`：事务实际 materialize 的 wall-clock time。
- `next_fire_at_ms`：本次 fire 后 Reminder 的下一次时间；one-time 为 `None`。
- `overdue`：`fired_at_ms > scheduled_for_ms` 且该 occurrence 是启动恢复或 scheduler
  catch-up 产生；正常 event-loop jitter 不单独把业务 occurrence 标成 overdue。
- `read_at_ms`：
  - `None` 表示 pending；
  - 非空表示已经由 `bcc reminder check` 返回并 drain。
- `anchor_message_id` 保存 occurrence 创建时的 anchor identity，避免 Reminder 后续修改或
  删除定义时改变已发生 occurrence 的上下文。

occurrence fire 事实不可删除或覆盖。`bcc reminder check` 只允许一次性设置 `read_at_ms`，
不能把 read 重新改回 pending。

### 4.3 Reminder 状态迁移

```text
scheduled --fire(one-time)------> fired
scheduled --fire(recurring)-----> scheduled
scheduled --snooze-------------> scheduled
fired     --snooze-------------> scheduled
scheduled --update-------------> scheduled
scheduled --cancel-------------> canceled
```

禁止：

```text
fired     --update
fired     --cancel
canceled  --snooze/update/cancel/fire
```

fire 和 command mutation 在同一个 Reminder 上发生竞态时，以取得 session lock 并提交
storage transaction 的顺序为线性化顺序：

- cancel/update/snooze 先提交：scheduler 观察到旧 revision/next 已失效，重新查询并 no-op；
- fire 先提交：one-time 已是 fired，update/cancel 失败，snooze 可以重新激活；
- recurring fire 先提交：Reminder 仍 scheduled，后续 update/cancel/snooze 作用于新 next。

### 4.4 Recurrence 与时间计算

受限 parser 接受：

```text
every:<positive-int>m
every:<positive-int>h
every:<positive-int>d
daily@HH:MM
weekly:<mon,tue,wed,thu,fri,sat,sun list>@HH:MM
```

规范：

1. `every:` interval 使用整数 milliseconds，最小 1 分钟；`d` 固定为 24 小时。
2. `daily`/`weekly` 使用持久化 IANA timezone；输出和 storage 仍使用 UTC milliseconds。
3. weekday list 去重并按周一到周日的规范顺序持久化。
4. schedule 可同时提供 repeat 和显式 first fire。显式 `--delay-seconds` / `--fire-at`
   决定 first slot；repeat 从该 slot 继续。
5. 只提供 repeat 时：
   - `every:` 的 first slot 为 `now + interval`；
   - `daily`/`weekly` 取 timezone 中严格晚于 now 的下一个 calendar slot。
6. recurrence next 从 `scheduled_for_ms` 计算，不从 `fired_at_ms` 计算。
7. calendar local time 落入 DST ambiguous slot 时取第一次出现；落入不存在的 local time 时
   顺延到 gap 后第一个有效 instant。该行为必须由 focused tests 固定。
8. `--fire-at` 只接受带 offset 的 ISO-8601，并 canonicalize 为 UTC；无 offset 的 timestamp
   fail closed。
9. duration parser 只接受正整数 `m/h/d`；`--delay-seconds` 只接受正整数 seconds。
10. 解析、canonicalization 和 next calculation 是纯 core 逻辑，不访问 SQLite 或 runtime。

### 4.5 SQLite schema

新增 migration version 12，创建两张表：

```sql
CREATE TABLE reminders (
    reminder_id TEXT PRIMARY KEY,
    owner_session_id TEXT,
    anchor_message_id TEXT,
    title TEXT,
    state TEXT,
    next_fire_at_ms INTEGER,
    repeat_rule TEXT,
    timezone TEXT,
    revision INTEGER,
    last_occurrence_no INTEGER,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    last_fired_at_ms INTEGER,
    canceled_at_ms INTEGER
);

CREATE TABLE reminder_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    reminder_id TEXT,
    owner_session_id TEXT,
    occurrence_no INTEGER,
    anchor_message_id TEXT,
    scheduled_for_ms INTEGER,
    fired_at_ms INTEGER,
    next_fire_at_ms INTEGER,
    overdue INTEGER,
    read_at_ms INTEGER,
    created_at_ms INTEGER
);
```

普通查询索引：

```sql
CREATE INDEX idx_reminders_state_next
    ON reminders (state, next_fire_at_ms, reminder_id);

CREATE INDEX idx_reminders_owner_state_updated
    ON reminders (owner_session_id, state, updated_at_ms, reminder_id);

CREATE INDEX idx_reminder_occurrences_owner_read_fired
    ON reminder_occurrences (
        owner_session_id,
        read_at_ms,
        fired_at_ms,
        occurrence_id
    );

CREATE INDEX idx_reminder_occurrences_reminder_number
    ON reminder_occurrences (reminder_id, occurrence_no);
```

沿用当前 repository 约束风格：关系、required fields、state、revision、
`(reminder_id, occurrence_no)` 唯一性和 owner/anchor 归属由 application/repository 校验，
不新增 trigger。Migration 只追加 version 12，不改写历史 migration checksum。

### 4.6 Storage port 与 repository 操作

`IStorageTransaction` 增加语义明确的 Reminder 操作：

```text
resolve_inbound_message(session_id, message_id_or_prefix)
get_reminder(owner_session_id, reminder_id_or_prefix)
list_reminders(owner_session_id, statuses)
save_new_reminder(reminder)
save_reminder_transition(expected_revision, reminder)
get_next_scheduled_reminder()
list_due_reminders(now_ms, limit)
save_fired_occurrence(expected_revision, reminder, occurrence)
list_pending_reminder_occurrences(owner_session_id, limit)
count_pending_reminder_occurrences(owner_session_id)
mark_reminder_occurrences_read(owner_session_id, occurrence_ids, read_at_ms)
list_sessions_with_pending_reminders()
```

约束：

- short prefix 必须在当前 owner session 中唯一；零匹配和多匹配使用不同错误。
- anchor resolve 只查询 inbound message，不允许 outbound id、provider id 或其他 session id。
- `save_fired_occurrence` 在一个 transaction 内验证 expected state/revision/next，插入
  occurrence，并写回 Reminder 的 state/next/revision/last occurrence。
- occurrence 插入与 Reminder next 推进不可分离；不能先推进 next 再在另一个 transaction
  创建 pending item。
- `mark_reminder_occurrences_read` 只能更新本次 check 实际返回且仍 pending 的 IDs。
- MemoryStorage 和 SQLite 必须提供等价语义；测试不能通过绕过 port 直接写 dict/SQL 来制造
  success path。

## 5. ReminderScheduler

### 5.1 单一 frontier timer

`ReminderScheduler` 是 node-wide async lifecycle object，只维护：

```text
one long-lived scheduler task
one optional active TimerWheel Timer
one asyncio.Event used as poke signal
one stopping flag/event
```

它不维护每个 Reminder 的 Timer，不为 future Reminder 创建 watcher task，也不缓存完整
Reminder 集合。每轮只查询全库最早的 scheduled row。

核心循环：

```python
while not stopping:
    await materialize_due_batches()

    frontier = await storage.get_next_scheduled_reminder()
    if frontier is None:
        await poke.wait()
        continue

    remaining_ms = frontier.next_fire_at_ms - wall_clock_ms()
    if remaining_ms <= 0:
        continue

    delay_ms = min(
        remaining_ms,
        timer_wheel.maximum_delay_ms - timer_wheel.tick_ms,
        wall_clock_recheck_ms,
    )

    timer = timer_wheel.create(delay_ms)
    await first_of(timer.wait(), poke.wait(), stopping.wait())
```

`wall_clock_recheck_ms` 使用 code-owned fixed constant，不增加配置或环境覆盖。它只用于重新
核对 wall clock；任一时刻仍只有一个 Timer。建议首版固定为 60 秒，使 host clock 前跳后
Reminder 最多在一个 recheck window 内被重新识别，且 node 每分钟最多执行一次 frontier query。

### 5.2 Poke 与 frontier 变化

以下成功 transaction 提交后调用 `scheduler.poke()`：

```text
schedule
snooze
update next/cadence
cancel
fire 后推进 recurrence
```

poke 不携带 Reminder 内容，只使 scheduler 取消当前 Timer 并重新查询最早 frontier。
多个连续 poke 由 `asyncio.Event` 合并；这不合并 runtime wake，也不改变 occurrence 数量。

### 5.3 Due materialization

scheduler 每轮按：

```text
state=scheduled
next_fire_at_ms <= now
ORDER BY next_fire_at_ms, reminder_id
LIMIT <batch-size>
```

读取 due batch。每个 Reminder 在 owner session lock 和显式 storage transaction 中：

1. 重读当前 row；
2. 校验 `state=scheduled`、expected revision 与 expected next；
3. 令 `occurrence_no = last_occurrence_no + 1`；
4. 创建 immutable occurrence；
5. one-time 写回 `state=fired,next=None`；
6. recurring 从本次 scheduled slot 计算 next，保持 `state=scheduled`；
7. revision 加一；
8. 同 transaction 插入 occurrence 并更新 Reminder；
9. commit 后记录 owner session 需要一次 ReminderWake。

同一个 due pass 为同一 owner 创建多条 occurrence 时，只向 runtime queue 发布一个
ReminderWake；该 notice 的 count 从数据库实时查询，并不把多条 occurrence 合并成一条
业务记录。

### 5.4 Horizon 与 wall clock

- `remaining_ms > TimerWheel.maximum_delay_ms` 时只等待 bounded delay，醒来后重查 SQLite。
- timer 到期不等于 Reminder 已到期；每次都以 wall-clock `next_fire_at_ms <= now` 为准。
- host suspend/resume、event-loop lag 和 wall-clock 调整只影响何时重新查询，不改变
  scheduled slot。
- scheduler 不调用 `Timer.reset` 复用已过期 Timer；frontier 变化时取消旧 Timer 并创建新
  Timer，避免 stale generation 触发错误 row。
- TimerWheel close、scheduler stop 和 task cancellation 都不能把 Reminder row标成 fired。

### 5.5 Overdue recovery

scheduler start 时先做两步：

1. 查询已有 `read_at_ms IS NULL` occurrences，按 owner session 发布一次恢复
   ReminderWake；
2. 按 due batch materialize所有 `next_fire_at_ms <= now` 的 Reminder。

recurring downtime catch-up：

```text
scheduled slot 1 <= now -> occurrence 1
scheduled slot 2 <= now -> occurrence 2
...
first next slot > now    -> stop catch-up and arm frontier
```

每个 transaction/batch 有固定上限，批次之间 `await asyncio.sleep(0)` 让出 event loop。
不设置“只保留最新一次”的隐式截断。若 backlog 很大，BCN 继续按批处理，已产生 occurrence
立即可由 `bcc reminder check` 消费。

crash consistency：

- commit 前崩溃：Reminder 仍 due，重启后重试；
- occurrence + Reminder update commit 后、queue publish 前崩溃：startup pending scan
  重新发布 wake；
- queue publish 后崩溃：occurrence 仍 pending，startup 再发布；notice 可重复，occurrence
  不重复。

## 6. 统一 runtime queue 与两种 notice

### 6.1 Queue item

现有 `_runtime_queues[bcn_session_id]` 继续是唯一 session runtime 调度入口。
`_RuntimeQueueItem` 增加 concrete `_ReminderNotification`，不增加第二张 queue：

```python
@dataclass(slots=True)
class _ReminderNotification:
    occurrence_id: str
    context: _DurableSessionContext
    wake_id: str
    activity_at_ms: int
```

Message 继续使用 `_RuntimeNotification`；idle activity、runtime expiry 和 provider
`RuntimeExpire` 保持现有类型。

`wake_id` 每次 publish/recovery 生成新的 UUIDv7。它是一次 runtime notice attempt 的
identity；`occurrence_id` 是持久业务 source。这样 pending occurrence 在 restart 后可以重新
唤醒，不会因为旧 `RuntimeAttempt` 已存在而永久跳过。

### 6.2 接入位置

普通 message 路径不改变：

```text
Channel receive
    -> conversation ingress queue
    -> persist InboundMessage
    -> apply DM/following/mention gating
    -> notifies_runtime=true
    -> _RuntimeNotification
```

Reminder 路径：

```text
ReminderScheduler
    -> persist ReminderOccurrence
    -> resolve owner BcnSession + ChannelSession context
    -> _ReminderNotification
```

二者只在 runtime queue 汇合。Reminder 不调用 `_record_inbound`，不分配 inbound seq，
不创建 ConsumerCursor，不影响 message snapshot。

### 6.3 串行 start/steer

runtime worker 按 queue 顺序逐项处理：

- 当前没有 active turn：该 notification 走 runtime establishment，然后用其对应 notice
  `turn/start`。
- 当前有 active turn：该 notification 用其对应 notice `turn/steer`。
- MessageNotification 和 ReminderNotification 不跨类型 batch、不合并 notice。
- Message 与 Reminder 同时入队时，先入队者先 start/steer；后一项随后处理。
- turn 在两项之间结束时，后一项自然启动新 turn，不需要特殊 race branch。

为了复用现有 turn lifecycle，`SessionTurnCoordinator.run_turn` /
`steer_turn` 改为接收 caller 已构造的 `input_text`，而不是内部固定调用 message
`inbox_notice`。message path 继续传现有 notice；Reminder path 传 reminder notice。
turn correlation 仍可使用 anchor inbound 作为 session/channel context，但
`RuntimeTurn.client_user_message_id` 和 `RuntimeAttempt.client_user_message_id`
使用本次 `wake_id`，不把 occurrence 伪装成 channel message。

### 6.4 两种 canonical notice

Message notice保持当前逐字 contract：

```text
[inbox notice session=<bcn_session_id>]
Inbox update: <n> unread message(s). Use the message command to read them.
```

Reminder notice新增：

```text
[reminder notice session=<bcn_session_id>]
Reminders pending: <n>. Use `bcc reminder check` to read them.
```

规则：

- `_RuntimeNotification` 只查询 notifying unread message count，并只生成 message notice。
- `_ReminderNotification` 只查询 pending Reminder occurrence count，并只生成 reminder notice。
- 不生成含两种 count 的第三种 notice。
- count 为 0 表示 wake 已过期，worker 直接完成该 queue item，不 start、不 steer。
- notice 不包含 title、anchor、scheduled time、occurrence ID 或其他 Reminder payload。

### 6.5 Active turn steer

`_steer_active_turn` 扩展为按 notification type 构造 notice：

- MessageNotification：沿用最新 unread message count；
- ReminderNotification：查询最新 pending Reminder count。

steer 仍是 content-free wake hint，不消费 occurrence。provider 不接受 steer、active turn
已结束或 outcome 无法确认时，queue item 保持其正常后续顺序；turn terminal 后 worker 再次
处理 pending notification，查询真实 pending count，必要时启动下一 turn。

### 6.6 following 与 runtime idle timer

ReminderNotification：

- 不读取或修改 `ChannelSession.following`；
- 不设置 `mentions_agent`；
- 不改变 anchor message 的 `notifies_runtime`；
- 作为 runtime activity 刷新现有 idle timer；
- idle runtime 被重新建立时仍复用 owner session 的持久 provider thread binding和 shared
  workspace。

## 7. BCC Reminder command

### 7.1 CLI surface

```text
bcc reminder schedule
bcc reminder check
bcc reminder list
bcc reminder snooze
bcc reminder update
bcc reminder cancel
```

不注册：

```text
bcc inbox check
bcc reminder log
bcc reminder schedule --channel
bcc reminder schedule --msg-id
```

`bcc.py` 当前 local request 只发送裸 `command`，而 message/reminder 都存在 `check`。
local command wire 改为同时发送：

```json
{
  "resource": "reminder",
  "command": "check"
}
```

现有 message/thread 请求同步带上 `resource`；dispatcher 按 `(resource, command)` 路由。
wrapper 与 daemon 同版本生成，不保留旧裸 command compatibility branch。

### 7.2 Schedule

```text
Usage: bcc reminder schedule [options]

Options:
  --title <t>
  --delay-seconds <n>
  --fire-at <iso>
  --repeat <rule>
  --tz <iana>
  --message-id <id>
```

校验：

- `--title`、`--message-id` 必填；
- `--delay-seconds` 与 `--fire-at` 互斥；
- delay/fire/repeat 至少存在一种；
- repeat grammar 与 timezone 必须在 command service 调用 storage 前完成校验；
- anchor 必须解析到当前 session 的 inbound message；
- title、rule、timezone、anchor 不进入 shell body或环境变量。

成功：

```text
Reminder scheduled: #<short-id> (<one-time|canonical-rule>) "<title>"
Next: <utc-iso>
```

### 7.3 Check 与 pending 推进

`bcc reminder check` 无参数。command service 在同一 session lock 和 storage transaction 中：

1. 读取最多 100 条 `read_at_ms IS NULL` occurrences，按
   `fired_at_ms, occurrence_id` 排序；
2. 解析每条 anchor message 的 canonical target；
3. 将实际返回的 occurrence IDs 标记为 read；
4. commit；
5. 返回保存好的 canonical snapshot。

一条 occurrence 输出：

```text
[class=due id=<short-reminder-id> occurrence=<n> scheduled=<utc-iso> fired=<utc-iso> overdue=<true|false> next=<utc-iso|none> target=<canonical-target> anchor=<full-message-id>] <title>
```

全部 drain：

```text
No more pending reminders.
```

没有 pending：

```text
No pending reminders.
```

仍有下一批：

```text
More pending reminders remain. Run `bcc reminder check` again.
```

`check` 的 read marker 表示 agent 已看到 occurrence，不表示 title 中描述的业务已经完成。
若 agent 需要稍后再次处理，使用 `snooze` 或创建新的 Reminder。

`check` 沿用 message check 的本地 drain 风险边界：transaction 已提交但 CLI 在 stdout
展示前崩溃时，该 occurrence 已被视为 read；首版不新增二阶段 acknowledgement 或
exactly-once delivery。

### 7.4 List

```text
Usage: bcc reminder list [options]

Options:
  --all
  --status <scheduled,fired,canceled>
```

默认 statuses 为 `scheduled,fired`。canonical row：

```text
#<short-id> [scheduled] (<one-time|rule>) next=<utc-iso> "<title>" anchor=<short-message-id>
#<short-id> [fired] (one-time) fired_at=<utc-iso> "<title>" anchor=<short-message-id>
#<short-id> [canceled] (<one-time|rule>) canceled_at=<utc-iso> "<title>" anchor=<short-message-id>
```

list 查询 Reminder definitions，不返回 occurrence/read history。

### 7.5 Snooze

```text
Usage: bcc reminder snooze [options]

Options:
  --id <id>
  --by <duration>
```

- scheduled：在当前 `next_fire_at_ms` 上增加 duration；
- fired：从 command evaluation `now` 增加 duration，并转回 scheduled；
- canceled：拒绝；
- snooze 后 revision 加一并 poke scheduler。

成功：

```text
Reminder snoozed: #<short-id>
Next: <utc-iso>
```

### 7.6 Update

```text
Usage: bcc reminder update [options]

Options:
  --id <id>
  --fire-at <iso>
  --in <duration>
  --cadence <rule>
  --title <text>
```

`--id` 之外恰好一个 update field。只允许 scheduled：

- `--fire-at` / `--in` 更新 next scheduled slot；
- `--cadence` 更新 repeat rule，当前 next 不变；
- `--title` 更新 title；
- fired 返回 `REMINDER_UPDATE_FAILED`，Next action 指向 snooze 或新建；
- canceled 返回稳定不可变状态错误。

成功：

```text
Reminder updated: #<short-id>
Next: <utc-iso>
```

### 7.7 Cancel

```text
Usage: bcc reminder cancel [options]

Options:
  --id <id>
```

只允许 scheduled；成功清空 next、写 canceled time、revision 加一并 poke scheduler：

```text
Reminder canceled: #<short-id>
```

### 7.8 Error contract

所有 handled failures 使用现有标签：

```text
Error: <human-readable summary>
Code: <stable code>
Next action: <optional recovery>
```

首版稳定 code 至少包括：

```text
REMINDER_TITLE_REQUIRED
REMINDER_TIME_REQUIRED
REMINDER_TIME_CONFLICT
REMINDER_REPEAT_INVALID
REMINDER_TIMEZONE_INVALID
REMINDER_ANCHOR_REQUIRED
REMINDER_ANCHOR_NOT_FOUND
REMINDER_ANCHOR_AMBIGUOUS
REMINDER_NOT_FOUND
REMINDER_ID_AMBIGUOUS
REMINDER_NOT_SCHEDULED
REMINDER_UPDATE_FAILED
REMINDER_CHECK_FAILED
```

Reminder commands 不使用 outbound draft，因此不输出 `Draft saved:`。

### 7.9 Developer instruction

`core/instruction.py` 的 command family 调整为连续编号，并加入：

```text
3. **Reminders** — `bcc reminder schedule`, `bcc reminder check`,
   `bcc reminder list`, `bcc reminder snooze`, `bcc reminder update`,
   `bcc reminder cancel`.
```

`### Reminders` 放在 Sending messages 后、Threads 前，保留以下行为：

- future follow-up 使用 Reminder，不保持当前 turn 长时间 sleep；
- Reminder 属于创建它的当前 session；
- 已有 Reminder 优先 snooze/update，真正不再需要时 cancel；
- agent 创建 Reminder 前必须从当前 conversation 解析 anchor，并显式传
  `--message-id`；
- 不使用 runtime-native cron/wakeup 替代 `bcc reminder schedule`；
- fire 不向 anchor thread/DM 发送 system message，只唤醒 owner runtime。

startup/notification instruction 增加 reminder notice：

```text
[reminder notice session=<session-id>]
Reminders pending: <n>. Use `bcc reminder check` to read them.
```

agent 看到 Reminder notice 时使用 `bcc reminder check`；普通 message notice 仍使用
`bcc message check`。instruction 不描述不存在的 `bcc inbox check`、App item 或
`bcc reminder log`。

## 8. Lifecycle 与恢复顺序

### 8.1 启动

`NodeApplication` / `SessionOrchestrator` 启动顺序：

1. 创建 data directory 与 wrapper。
2. 启动共享 TimerWheel。
3. 启动 local command server，但 dispatcher 尚不 accepting。
4. 启动 storage、runtime、Channel 和现有 receive/runtime-expire loops。
5. 启动 ReminderScheduler：
   - 发布已有 pending occurrences 的恢复 wake；
   - materialize overdue occurrences；
   - arm 一个 frontier Timer。
6. command dispatcher 开始 accepting。

scheduler 只在 storage 已 ready、runtime queue 可创建后启动，避免 occurrence commit 后无
可用 wake target。

### 8.2 关闭

1. command dispatcher 停止 accepting 并 drain in-flight commands。
2. ReminderScheduler 停止接受 poke，取消唯一 active Timer，等待当前 fire transaction
   bounded 收口。
3. SessionOrchestrator 停止 Channel receive、runtime workers、runtime session。
4. 停止 command server、清理 wrapper。
5. 最后关闭共享 TimerWheel。

scheduler 必须先于 SessionOrchestrator runtime queues 和 TimerWheel 关闭；关闭过程中没有
提交的 fire transaction 回滚，已经提交的 occurrence 留作下次 startup pending recovery。

### 8.3 运行时过期与进程重建

Reminder ownership 不依赖 live RuntimeSession。runtime idle timeout 停止 Codex process 后，
Reminder 仍保存在 SQLite；下一 ReminderWake 使用现有 session establishment 创建/恢复
RuntimeSession 和 provider thread。

pending occurrence 在 daemon restart 后重新产生新的 `wake_id`。同一 occurrence 可以导致
多次 content-free notice，但 `bcc reminder check` 只会 drain 一次，read marker 防止业务
occurrence 重复返回。

## 9. 实施顺序

### Phase 1：Reminder domain 与持久化

目标：先固定 state、time、recurrence、anchor、occurrence 和 pending/read 的持久语义，
不提前修改 runtime queue 或 Codex adapter。

#### Task 1A：domain model、recurrence 与 command contract

修改：

```text
src/bazaar_compute_node/core/models/entities.py
src/bazaar_compute_node/core/models/states.py
src/bazaar_compute_node/core/reminder.py
src/bazaar_compute_node/core/command.py
tests/core/test_reminder.py
```

实施：

- 增加 `ReminderState`、`Reminder`、`ReminderOccurrence` 及字段组合校验。
- 实现受限 duration/repeat/fire-at/timezone parser 和 canonical serializer。
- 固定 recurrence next、DST、snooze/update/cancel transition。
- 定义 `IReminderService`、schedule/check/list/snooze/update/cancel request/result。
- 不接 SQLite、TimerWheel、runtime queue 或 `bcc.py`。

Focused tests：

- one-time、every、daily、weekly parse/canonicalization；
- UTC/default timezone、IANA invalid、offset ISO；
- DST ambiguous/nonexistent policy；
- scheduled/fired/canceled 状态迁移；
- fired snooze 重新 scheduled；
- fired update/cancel 和 canceled mutation fail closed；
- title/control character、duration、weekday、short ID input validation。

完成条件：

- core types 不 import contrib/provider；
- Pyright/LSP 对修改文件无诊断；
- focused pytest 与 Ruff 通过；
- 发送业务 diff并停在 review。

依赖：本计划 review。产出：完整 Reminder domain contract。

#### Task 1B：SQLite migration、repository 与 MemoryStorage parity

修改：

```text
src/bazaar_compute_node/core/storage.py
src/bazaar_compute_node/contrib/sqlite/migrations.py
src/bazaar_compute_node/contrib/sqlite/codec.py
src/bazaar_compute_node/contrib/sqlite/repository.py
tests/support/src/bcn_test_support/storage.py
tests/contrib/test_sqlite_reminder_repository.py
```

实施：

- 新增 migration version 12、两张表和查询索引。
- 实现 anchor/full-or-short resolve、Reminder/full-or-short resolve、definition CRUD、
  due/frontier query、atomic fire、pending count/check/read marker。
- repository 执行 owner/session/anchor/revision/occurrence invariant。
- MemoryStorage 实现相同 transaction rollback 和 semantic checks。
- 不接 scheduler、runtime queue 或 CLI。

Focused tests：

- migration ledger/restart；
- anchor 必须是当前 session inbound；
- short prefix zero/one/multiple match；
- create/list/update/snooze/cancel；
- fire transaction 同时写 occurrence 和 next；
- expected revision race；
- recurring occurrence number 连续；
- check 只标记实际返回 batch；
- rollback 不留下半条 occurrence或已推进 next；
- SQLite 与 MemoryStorage contract matrix。

完成条件：

- schema version 12 可重复启动；
- 旧 schema 11 正常升级；
- focused pytest、Ruff、Pyright、compileall、LSP 通过；
- 发送业务 diff并停在 review。

依赖：Task 1A。产出：durable Reminder/occurrence repository。

Phase 验收：不启动 Runtime 的情况下，通过真实临时 SQLite 完成 schedule -> due fire ->
pending check -> read，并证明 recurring next、overdue slot、snooze fired、update/cancel race 和
restart persistence 符合本文 contract。

### Phase 2：单 frontier scheduler 与统一 runtime queue

目标：使用现有 TimerWheel 实现 node-wide scheduler，并把 ReminderWake 接入现有
per-session queue；Runtime 仍只有 start/steer 两个输入方法。

#### Task 2A：ReminderScheduler 与 lifecycle

修改：

```text
src/bazaar_compute_node/core/orchestration/reminder.py
src/bazaar_compute_node/core/orchestration/session.py
src/bazaar_compute_node/app/application.py
tests/core/test_reminder_scheduler.py
tests/contrib/test_orchestration.py
```

实施：

- 实现一个 scheduler task、一个 active Timer、一个 poke event。
- frontier query、60 秒 wall-clock recheck、TimerWheel horizon re-arm。
- due batch、atomic occurrence materialization、per-owner wake publish。
- startup pending recovery 与 recurring overdue catch-up。
- scheduler start/stop 顺序及 Timer cancellation。
- 暂时通过 internal callback 记录 wake，不修改 turn/start/steer。

Focused tests：

- 任意数量 future Reminder 只有一个 TimerWheel entry；
- 更早 schedule/update/snooze/cancel 会 poke 并替换 frontier；
- horizon 外 Reminder 分段 re-arm；
- wall clock 前跳/后跳重新查询；
- one-time/recurring due；
- 多 missed slots 有界 batch 且不丢失；
- crash boundary 后 pending recovery；
- stop 不误 fire、不遗留 waiter；
- 同 owner batch 只发布一次 wake，occurrence 仍逐条存在。

完成条件：

- scheduler 无 per-reminder task/timer；
- focused pytest、Ruff、Pyright、compileall、LSP 通过；
- 发送业务 diff并停在 review。

依赖：Phase 1。产出：durable one-frontier scheduling engine。

#### Task 2B：ReminderWake、串行 start/steer 与两种 notice

修改：

```text
src/bazaar_compute_node/core/orchestration/session.py
src/bazaar_compute_node/core/orchestration/turn.py
src/bazaar_compute_node/core/correlation.py
tests/contrib/test_orchestration.py
```

实施：

- `_RuntimeQueueItem` 增加 `_ReminderNotification`，仍使用现有 `_runtime_queues`。
- Reminder callback resolve durable session context并发布 wake。
- runtime worker 按 queue order 分别处理 MessageNotification 与 ReminderNotification。
- `SessionTurnCoordinator` 接收 caller-provided input text，message notice逐字保持，新增
  reminder notice。
- idle ReminderWake 走现有 runtime establishment/start turn；active ReminderWake 走 steer。
- stale wake 通过 pending count=0 no-op。
- wake 使用独立 `wake_id` 创建 RuntimeAttempt，不把 occurrence 伪装成 InboundMessage。
- Reminder wake 刷新 idle timer但不改变 following。
- 不构造跨类型 batch/notice。

Focused tests：

- MessageWake -> ReminderWake 和 ReminderWake -> MessageWake 的严格顺序；
- idle 两项在 turn terminal 边界自然串行；
- active turn 分别收到 message steer 与 reminder steer；
- 两种 notice逐字 contract，永远不出现 combined notice；
- ReminderWake 不调用 Channel send/receive，不创建 inbound row，不推进 message cursor；
- unfollowed group anchor 仍能 wake owner且 following 保持 false；
- pending count=0 的 stale wake不 start/steer；
- steer rejected/terminal race 后 occurrence不丢失；
- restart pending occurrence使用新 wake id重新通知；
- 多 session queue/turn不串线。

完成条件：

- Runtime port 无新 inbox method；
- 一个 session 只有一个 runtime worker；
- focused pytest、Ruff、Pyright、compileall、LSP 通过；
- 发送业务 diff并停在 review。

依赖：Task 2A。产出：统一 runtime wake path 和 Reminder notice。

Phase 验收：TestChannel+TestRuntime 下，普通 message 经过原 gating 后入队，Reminder 不经过
gating 但进入同一 queue；idle/start、active/steer、同时入队顺序、unfollowed anchor 和
stale wake 均符合本文 contract。

### Phase 3：BCC 命令、developer instruction 与真实 Runtime 验收

目标：把已完成的 domain/scheduler/orchestration 暴露为 session-scoped `bcc` command，并
用真实 Codex App Server 证明 agent 能自然 schedule 和消费 Reminder。

#### Task 3A：local command dispatch 与 BCC canonical CLI

修改：

```text
src/bazaar_compute_node/core/orchestration/command.py
src/bazaar_compute_node/app/command.py
src/bazaar_compute_node/app/application.py
src/bazaar_compute_node/bcc.py
tests/test_bcc.py
tests/app/test_bcc_process.py
tests/contrib/test_orchestration.py
```

实施：

- local request 增加 `resource`，迁移 message/thread dispatch，不保留裸 command兼容。
- wiring `IReminderService` 到 CommandDispatcher。
- 实现六个 parser、request validation、response serialization和 stable errors。
- `check` 事务 drain、100-item batch、canonical class=due output。
- schedule/list/snooze/update/cancel canonical success text。
- command mutation 成功后 poke scheduler。
- `--channel`、`--msg-id`、`log` 不出现在 help或 parser。

Focused tests：

- 六个 `--help`；
- session/runtime capability binding；
- command resource collision；
- schedule input/anchor；
- check drain/empty/more；
- list filters；
- fired snooze、fired update failure、cancel；
- canonical stdout/stderr和退出码；
- daemon process 中真实 wrapper/IPC调用。

完成条件：

- `bcc reminder ...` 全部经本机 command transport；
- focused pytest、Ruff、Pyright、compileall、LSP 通过；
- 发送业务 diff并停在 review。

依赖：Phase 2。产出：用户可用 Reminder CLI。

#### Task 3B：developer instruction 与 instruction contract

修改：

```text
src/bazaar_compute_node/core/instruction.py
tests/core/test_instruction.py
tests/contrib/test_codex.py
```

实施：

- command family 增加 Reminder 六命令并连续编号。
- 插入符合 BCN 实际行为的 `### Reminders`。
- startup/notification rules区分 message notice 与 reminder notice。
- 删除不存在的 channel-visible receipt、App inbox、`reminder log` 和 runtime-native cron
  建议。
- Codex thread/start 的 developer instruction继续由当前
  `DeveloperInstructionContext` 机械渲染，不新增用户覆盖或 provider分支。

Focused tests：

- instruction 逐字段/关键段落 golden assertions；
- no unresolved placeholder；
- command list只包含实际 parser surface；
- Codex thread/start接收新 instruction；
- notice text与 orchestration canonical function逐字一致。

完成条件：

- prompt 不宣称不存在的能力；
- focused pytest、Ruff、Pyright、compileall、LSP 通过；
- 发送业务 diff并停在 review。

依赖：Task 3A。产出：agent-facing Reminder contract。

#### Task 3C：端到端与跨平台验收

不新增 production seam；复用 tests/support 的真实 contract adapters和当前 Codex runtime。

验收场景：

1. TestChannel+TestRuntime：
   - 一个自然 inbound 创建 owner/anchor；
   - schedule one-time/recurring；
   - idle fire 启动 Reminder turn；
   - active fire steer 当前 turn；
   - `bcc reminder check` drain；
   - message/reminder 同时发生按 queue order 分别 notice。
2. TestChannel+CodexRuntime：
   - 使用自然语言要求真实 Codex 安排短 Reminder；
   - Codex 实际调用 `bcc reminder schedule`；
   - fire 后真实 start/steer notice；
   - Codex 实际调用 `bcc reminder check` 并根据 anchor history继续任务；
   - 不断言模型精确回答文本，只验证 command、turn、occurrence、pending/read和provider
     correlation。
3. restart：
   - daemon 在 scheduled future、committed-unpublished occurrence、pending-unread occurrence
     三个边界重启；
   - overdue与pending恢复，不重复 occurrence，不永久丢 wake。
4. WeComChannel+CodexRuntime（具备真实 WeCom 会话的平台）：
   - inbound anchor来自真实 DM/thread；
   - Reminder fire本身不调用 WeCom send、不产生外部 system message；
   - agent只有在自然任务需要时才通过正常 `bcc message send`回复用户。
5. Linux/macOS/Windows：
   - 平台具备真实 Codex时使用真实 App Server；
   - 不用 mock/fake provider 替代；
   - TimerWheel、zoneinfo、SQLite、wrapper/IPC和shutdown均通过。

最终 gates：

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

完成条件：提供业务 diff、测试终态、真实 provider证据和剩余限制，停在 review；不 commit、
push、merge、release或deploy，除非获得单独授权。

依赖：Task 3B。产出：完整 Reminder feature review candidate。

## 10. 验证原则

- 测试从公开 command、storage port、Channel ingress 和 Runtime port进入，不直接写私有
  dict/SQLite row冒充业务链路。
- TestChannel/TestRuntime 只验证 provider-neutral contract；真实 Codex验收必须启动真实
  App Server process。
- 用户消息使用自然语言业务上下文，例如“半小时后提醒我重新检查这个 PR”，不能在用户消息
  中精确指导 agent调用哪个命令或参数。
- scheduler focused tests可以使用受控 clock和现有 TimerWheel内部时间推进，但 production
  不增加可覆盖环境变量或只为测试存在的 injection。
- SQLite tests使用真实临时文件和显式 transaction，不用 mock cursor。
- recurrence tests固定 UTC、IANA timezone、DST、interval/calendar差异和 missed slot顺序。
- runtime notice tests验证逐字文本、queue order、start/steer选择和不合并行为。
- `bcc reminder check` tests同时验证返回 snapshot与read marker，不能只断言 stdout。
- no-channel-delivery通过 Channel send observation和真实 WeCom场景证明，不以“代码看起来没
  调用”代替。
- full suite必须自然运行到终态；出现 failure不手动中断测试进程。
- 每个 Task完成后先 focused tests和LSP，再发送 diff并等待 review。

## 11. 关键风险与处理边界

1. **Wall clock 与 monotonic clock分离**：TimerWheel不能直接表示持久 absolute time。
   scheduler必须定期重查 wall clock，fire前再次比较 `next_fire_at_ms`。
2. **Frontier stale race**：schedule/update/snooze/cancel可能改变当前最早 Reminder。
   poke只负责唤醒，真正决策必须重新查询 SQLite和expected revision。
3. **Recurring overdue backlog**：长时间停机可能产生大量 missed slots。使用有界 batch和
   event-loop yield，不静默截断，也不在一个 transaction处理无限 backlog。
4. **DST calendar edge**：daily/weekly在 ambiguous/nonexistent local time必须有固定规则和
   golden tests，不能依赖平台默认 fold。
5. **Fire/mutation竞态**：fire、cancel、update、snooze必须以session lock + transaction
   线性化；Timer到期本身不拥有状态变更权。
6. **Commit/wake crash gap**：occurrence与next在同一transaction提交，startup扫描pending
   occurrences恢复wake，不能依赖内存queue作为事实源。
7. **Duplicate notice**：wake允许at-least-once；每次处理前查询pending count。重复notice不能
   生成第二条occurrence，check read marker保证业务item只返回一次。
8. **Check drain output gap**：read marker可能在CLI展示前提交，语义与现有message check一致；
   首版明确不提供exactly-once或二阶段ack。
9. **RuntimeAttempt identity**：重启后的pending occurrence需要新的wake id，不能使用固定
   occurrence id作为唯一turn attempt并被旧attempt永久挡住。
10. **Message/Reminder耦合**：统一的是runtime queue，不是storage/cursor。Reminder不能推进
    message cursor、改变fresh-check或使用InboundMessage seq。
11. **Following误更新**：Reminder anchor可能位于unfollowed group；任何Reminder代码路径都
    不得写following或伪造mention。
12. **CLI namespace冲突**：message和reminder都有check；local command request必须显式携带
    resource，不能继续只按裸command路由。
13. **无 lifecycle log**：首版不提供Reminder mutation history；诊断只依赖当前definition、
    occurrence rows和现有operational audit。若产品需要用户可见审计，另行增加event table和
    command，不在本实现隐式保留半套log。
14. **数据增长**：read occurrence首版不删除。未来retention必须另行定义，不能在scheduler或
    check中顺手清理历史，避免破坏recovery和occurrence identity。

## 12. Code 模式入口条件

进入 Code 模式前只需要确认本计划，无需再次选择 scheduler、queue、App item 或 log 子方案。
review 通过后必须从 Phase 1 Task 1A 开始；Task 1A 完成并 review 前，不提前编写 migration、
scheduler、runtime queue、`bcc` parser或developer instruction。后续每个 Task沿用相同的
focused checks、业务 diff和review停止点。
