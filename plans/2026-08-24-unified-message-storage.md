# Unified Message Storage

## 状态

- 模式：Plan
- 基线：`main` 的 `544a208`，工作分支 `fix/message-conversation-history`

## 目标

先把 core storage port 收敛为异步领域 operation，并在 SQLite adapter 内用一个 writer
actor、小型 read pool 和单一 repository 实现这些 operation；再把现有 `InboundMessage` /
`OutboundMessage` 与 `inbound_messages` / `outbound_messages` 合并为一个 `Message` 模型和
一张 `messages` 表。

完成后的命令行为：

- agent 成功发送或 queued 一条消息后，`bcc message read --target <target>` 在正确位置返回
  该消息，sender 为当前 agent，type 为 `agent`；
- `bcc message read --around <outbound-message-id>` 可以用该消息的本地 id 定位历史窗口；
- 当自己发送的消息是最新可见消息时，`bcc inbox list` 的 latest message id、sender 和
  time 指向该消息；
- `bcc message check` 只返回 direction 为 inbound 的未读消息，不返回自己发送的消息；
- `bcc message send` 继续使用现有 fresh-check 与 delivery state machine。

## 设计

### 1. Storage operation boundary

Core storage port 只暴露异步领域操作，不暴露 connection、repository 或 transaction：

- 删除 `IStorageTransaction`、`IHandoffStorageTransaction` 和
  `async with storage.transaction()`；
- 调用方直接 `await storage.<operation>(...)`；
- check、inbound persist、outbound prepare、Reminder fire 等跨多次读写的不变量收敛为一个
  storage operation，由 storage implementation 保证其原子语义；
- Agent-owned operation 显式携带 Agent scope，global Reminder operation 使用独立接口；
- core 不定义 actor、连接数、SQL transaction 或 SQLite 调度方式，其他 storage adapter 可按
  自身能力实现同一 port。

SQLite adapter 对其余 `contrib/sqlite` 代码只暴露 `SqliteDatabase` execution façade：

```text
fetchone
fetchall
execute
executemany
reader
transaction_write
```

- repository 调用 façade，不持有 connection，也不感知 connection pool、writer actor 或 request
  queue；
- `fetchone` / `fetchall` 执行单次读取；`execute` / `executemany` 执行单次写入；调用方不选择
  connection；
- 需要连续执行多条读取时使用 `async with database.reader() as session`，由 context 自动借还
  read session；
- 这些读取还要求同一 snapshot 时，在 reader context 内再使用
  `async with session.transaction()`；
- 需要多条写入共同提交时使用 `transaction_write(operation)`，operation 通过传入的 writer
  session 执行；
- façade 根据方法语义完成路由，不解析 SQL 文本。

`SqliteDatabase` 内部使用一个 writer actor + 两连接 read pool。writer actor 独占 write
connection，串行执行 mutation 与 read-modify-write operation；纯读取使用 read pool，可以彼此
并发，并在 WAL 下与 writer 并发。`reader()` 只管理 read session lease；
`session.transaction()` 开启短 read transaction；`transaction_write()` 开启
`BEGIN IMMEDIATE`。单次查询和单条写入不显式开启 transaction。

所有 connections 使用一致的 WAL、foreign key 与 busy timeout 配置，read connections 启用
query-only。不再使用 `_transaction_lock`、database-global Agent binding 或 `bcn_agent_id()`。
startup migration 在 writer actor 接管 connection 前完成；shutdown 停止接收 operation，收敛
writer queue 与正在使用的 read sessions 后关闭全部 connections。

SQLite 只保留一个具体 repository 实现。旧 `repository.py`、`scoped_repository.py`、
`reminder_repository.py`、`handoff_repository.py` 中的 transaction 实现合并到
`repository/` package，删除 scoped/unscoped 重复方法和多个有状态 repository 实现。
package 内部可以用无状态、方法集互斥的 domain operation mixin 表达 Rust trait 式能力；
它们不定义 `__init__`、不持有独立状态、不覆盖同名方法，也不依赖 `super()` 调用链。
文件边界不再表达访问权限；Agent 隔离由 operation 输入、core port 和 SQLite
repository 校验共同保证。除 database executor 内部外，`contrib/sqlite` 代码不直接
取得 connection 或管理 pool/actor。

### 2. 单一 Message

新增 `MessageDirection`：

```text
inbound
outbound
```

只保留一个 `Message` dataclass。字段是现有两个模型的并集：

- 通用字段：message id、session、channel session、target、sender、body、reply、
  message type、metadata；
- inbound 字段：provider identity/time、received time、mention、notify、payload ref；
- outbound 字段：command id、delivery state、fresh-check snapshot、provider receipt、
  attempt/completion time、error；
- attachments 沿用现状：inbound 继续使用 `inbound_attachments`，outbound 继续使用
  `attachments_json`。

`direction` 是同一 Message 上的存储 discriminator：

- inbound 行要求 inbound 工作流必填字段存在；
- outbound 行要求 outbound 状态机必填字段存在；
- repository 在写入和状态迁移时校验对应不变量。

统一表只记录一个 `sender` display value，也就是 `message read` 实际展示的发送者。旧
inbound 的 `sender` 原样迁移；旧 outbound 使用所属 agent name。现有 `sender_kind` 继续
留在 `metadata_json`，供 approval 等既有判断使用。

### 3. 单一序列

所有 Message 共用一个递增 `seq`。`read`、`inbox list`、around window、check、pending、
cursor、snapshot 和 fresh-check 都使用该序列；check 通过 `direction = 'inbound'` 排除
outbound 行。

迁移按发言时间为两张旧表生成统一 seq：inbound 使用 provider time，缺失时使用 received
time；outbound 使用 created time；同一毫秒按 message id 稳定排序。临时映射把旧 inbound
seq 对应到新 seq。`consumer_cursors` 与旧 outbound 的 `snapshot_seq` /
`current_inbound_seq` 更新为同一消息边界的新 seq，业务语义保持不变。迁移完成后所有新
Message 直接分配下一个 seq。

### 4. 单表查询规则

- check/pending/fresh-check：`direction = 'inbound'`，继续使用 `seq` 与
  `notifies_runtime`；
- send/get delivery state：`direction = 'outbound'`，继续使用原 delivery state machine；
- read：读取 inbound，以及 state 为 `sent` / `queued` 的 outbound，按 `seq` 排序；
- inbox latest：使用与 read 相同的可见集合，取最大 `seq`；
- failed、partial、unknown、pending outbound 仍保存在表中，但不作为已发送会话消息展示。

Storage operation 保留现有 workflow 语义，但统一操作 `messages` 并验证 direction。read 与
inbox 使用通用 history/catalog operation，其他调用方不理解物理表或 transaction。

## SQLite v18 message migration

新增一次原子 migration：

1. 创建 `_messages_v18`，字段为两张旧表的并集，增加 `direction`、统一 `seq`、sender。
2. 在临时 guard 中检查 inbound `message_id` 与 outbound `outbound_message_id` 不冲突；
   冲突时 migration 失败并整体回滚。
3. 用一个稳定排序的 union 迁移两表：
   - inbound message id 原样保留；
   - outbound message id 原样改写为统一 `message_id`；
   - direction 分别写入 `inbound` / `outbound`；
   - workflow-specific 字段原样复制，不推导新状态。
4. `inbound_attachments.message_id` 无需改写；附件行原样保留。
5. 根据临时 seq 映射更新 `consumer_cursors` 与 outbound snapshot/current seq；runtime
   attempt、Reminder、handoff 中的 message id 原样保留。
6. 校验迁移前后总行数、各 direction 行数、seq 边界、delivery state、reply id、agent
   ownership 和 attachment 引用。
7. 删除旧 index/table，把 `_messages_v18` 改名为 `messages`，建立最小索引：
   - agent/session/target/seq，用于 read 与 inbox latest；
   - agent/direction/seq，用于 check 与 pending；
   - agent/channel/provider identity 的 inbound 唯一索引；
   - agent/command id 与 outbound state/time 索引。
8. 执行 `PRAGMA quick_check` 的 migration integration test；任一步失败均不写入 v18 ledger。

## 实施任务

### Task 1：Storage operation、SQLite writer actor 与 read pool

#### 1.1 Core storage contract

涉及：

- `src/bazaar_compute_node/core/storage.py`
- `src/bazaar_compute_node/core/models/__init__.py`
- `tests/support/src/bcn_test_support/storage.py`
- `tests/support/src/bcn_test_support/reminder_storage.py`
- `tests/core/test_ports.py`

`IStorage` 继续表示 node-owned lifecycle 与 Agent scope factory；`IStorageScope` 继续表示 immutable
Agent view。删除 `IStorageTransaction`、`IHandoffStorageTransaction` 和两个 storage interface 上的
`transaction()`。

现有只执行一个独立不变量的方法直接提升到 `IStorage` / `IStorageScope`，例如 session/context
lookup、history、inbox catalog、pending count、单行 state transition 与 global Reminder frontier。
以下跨多次 repository 调用的路径改为一个 core storage operation：

```text
record_inbound
check_messages
prepare_outbound
read_message_history
read_inbox_catalog
check_reminders
check_handoffs
fire_reminder
load_reminder_wake
load_handoff_wake
```

operation 的输入输出使用 core model，不包含 SQL、connection 或 actor 类型：

- `record_inbound` 接收 provider-normalized inbound message，原子完成 dedupe、Channel session / BCN
  session / cursor ensure、message 与 attachment 写入、last activity 更新；返回 canonical Channel
  session、BCN session、message 以及 message/session 是否新建；
- `check_messages` 接收 session id，读取当前 cursor 与 notifying inbound batch、解析 reply
  references、推进 cursor；返回 batch、snapshot seq 与 delivered-through seq；
- `prepare_outbound` 接收 caller session id、target、command id 与完整 payload，在同一个
  operation 内解析 target 并读取 freshness boundary；返回 cross-session hold、freshness hold
  snapshot 或已经持久化的 pending outbound；active draft 仍由 `SessionCommandService` 管理；
- `read_message_history` 在一个一致读快照中返回 history window、reply references 与 snapshot seq；
- `read_inbox_catalog` 在一个一致读快照中返回 caller existence 与 Agent inbox page；
- `check_reminders` / `check_handoffs` 在一个 operation 内读取 pending batch、构造展示所需
  snapshot、标记同一批记录已读并计算 has-more；
- `fire_reminder` 原子校验 revision/due slot、写 occurrence 并推进 Reminder；
- 两个 wake load operation 在一个一致读快照中返回 pending 状态、durable session context 与
  anchor message。

MemoryStorage 直接实现这些 operation。它继续使用内存数据结构与 per-session concurrency，不引入
writer actor、connection pool 或 SQLite transaction。

#### 1.2 SQLite internal executor

新增：

- `src/bazaar_compute_node/contrib/sqlite/executor.py`
- `tests/contrib/test_sqlite_executor.py`

`SqliteDatabase` 是 `contrib/sqlite` 其他模块唯一使用的 SQL execution façade；它把以下方法委托给
`executor.py` 内部的 `SqliteExecutor`。executor 持有 writer request queue、writer task 和 read
connection queue：

```text
fetchone(statement, parameters)
fetchall(statement, parameters)
execute(statement, parameters)
executemany(statement, parameter_sets)
reader()
transaction_write(operation)
```

`fetchone` / `fetchall` 在内部借用一个 read session，完成查询并关闭 cursor 后归还；`execute` /
`executemany` 创建 writer request，等待 writer actor 返回 row count、last row id 或 `RETURNING`
rows，不向调用方暴露 `aiosqlite.Cursor`。`reader()` 返回 async context manager；进入后返回
executor-owned read session。`transaction_write` 的 operation 拿到对应 writer session。两个
session 都提供相同 fetch/execute 方法；read session 另提供 `transaction()` async context
manager。session 固定使用 executor 内部已经选中的 connection，不会再次入队或借连接；connection
pool 不出现在 session 或 repository contract 中。

writer request 固定包含 operation、是否开启 transaction、cancellation signal 和 result future。
writer actor 按 queue 顺序处理：

1. 根据 request 创建 writer-session façade；
2. transactional request 进入 executor 内部 `_WriteTransaction` async context manager，并执行
   `BEGIN IMMEDIATE`；
3. 在该 context 中执行完整 operation；
4. 正常返回时 commit；operation exception 或 cancellation 透传到 `__aexit__`，由它 rollback；
5. 关闭 operation 创建的 cursor；
6. 向 result future 写入结果或原始异常，再处理下一条 request。

writer queue 使用单一 `asyncio.Queue`。`transaction_write` 等待 result future 时捕获 caller 的
`CancelledError`，设置 request cancellation signal：尚未开始的 request 由 actor 跳过；正在执行的
operation task 被取消，in-flight SQLite statement 使用 writer connection 的 `interrupt()` 中止，
然后由 `_WriteTransaction.__aexit__` rollback。executor 等待 rollback 完成后再把
`CancelledError` 透传给 caller，actor 之后才处理下一条 request。

连续读取使用 reader context；需要同一 snapshot 时在 session 内再开启 transaction：

```python
async with database.reader() as session:
    async with session.transaction():
        ...
```

`reader().__aenter__` 在 executor 内部取得 read connection 并返回 read session，不执行
`BEGIN`；`reader().__aexit__` 只负责由 executor 回收 session。`session.transaction()` 进入时执行
`BEGIN`，正常退出时 commit，exception 或 cancellation 时 interrupt 当前 read 并 rollback。
repository 不接触 connection pool，不手写 acquire/release 或 `finally`。read connection 启用
`query_only`，任何误入的写语句由 SQLite 直接拒绝。

read routing 规则固定为：

- 单条 SELECT 使用 `fetchone` / `fetchall` convenience，不显式开启 transaction；
- 连续多条 SELECT 使用一个 `database.reader()` session，避免手动管理连接；
- 这些 SELECT 的返回值要求来自同一数据库时点时，在 session 内增加
  `async with session.transaction()`；
- 多条 SELECT 不要求同一 snapshot 时不进入 `session.transaction()`。

SQLite autocommit 会让每条 SELECT 各自建立 read transaction。例如 history window、reply
references 与 latest seq 如果分别查询，writer 可能在查询之间提交，导致 messages 与 snapshot
seq 来自不同状态。`session.transaction()` 使用一次显式 `BEGIN` 把这些查询固定在同一 snapshot；
WAL 下 writer 仍可并发提交，reader 继续读取 transaction 开始时的版本。transaction context 只
包含 SQLite 查询与结果组装，不执行 Channel、Runtime、audit 或其他外部 I/O，避免长 read
transaction 阻碍 checkpoint。

#### 1.3 Connection lifecycle

修改：

- `src/bazaar_compute_node/contrib/sqlite/database.py`
- `src/bazaar_compute_node/contrib/sqlite/storage.py`
- `src/bazaar_compute_node/contrib/sqlite/plugin.py`

start 顺序固定为：

1. 创建 data directory 并限制权限；
2. 打开 writer connection，配置 WAL、`synchronous=NORMAL`、foreign keys 与 busy timeout；
3. 使用 writer connection 执行历史 checksum verification、schema migration 与 startup
   checkpoint；
4. 打开弹性 read pool；`SqliteDatabase` 默认保留 2 个 idle read connections，繁忙时按实际
   waiter 数按需扩展且默认不限制最大值，归还后的超额连接空闲 60 秒后关闭；idle 数、可选上限和
   idle timeout 均可配置；
   每条 read connection 配置相同的 WAL、foreign keys、busy timeout，最后启用 `query_only`；
5. 启动 writer actor；
6. executor ready 后才把 storage 标记为 started。

stop 在现有 lifecycle timeout 内按以下顺序执行：

1. 拒绝新的 executor request；
2. 等待已经入队的 writer request 到达 terminal outcome；
3. 等待已经借出的 read connection 归还；
4. 停止 writer actor；
5. 关闭全部 read connection 和 writer connection；
6. 清除 started 状态。

start 任一步失败时按相反顺序关闭已经创建的资源。storage scope 不拥有 executor lifecycle，Agent
stop 不关闭 shared SQLite connections。stop timeout 时 rollback active writer transaction、使未完成
request future 以 shutdown error 结束、关闭 connections，并向 caller 返回 timeout failure。

#### 1.4 SQLite v17 ownership-trigger cleanup

新增/修改：

- `src/bazaar_compute_node/contrib/sqlite/migrations/registry.py`
- `src/bazaar_compute_node/contrib/sqlite/migrations/v17_remove_agent_identity_triggers.py`
- `src/bazaar_compute_node/contrib/sqlite/repository/`
- `tests/contrib/test_sqlite_database.py`

Task 1 注册 migration 17，只清理旧 Agent identity 自动注入机制，不修改 table column、index 或业务
数据：

```text
set_channel_sessions_agent_id
set_bcn_sessions_agent_id
set_inbound_messages_agent_id
set_outbound_messages_agent_identity
set_runtime_attempts_agent_id
set_reminders_agent_id
set_reminder_occurrences_agent_id
```

migration 原子删除以上 trigger。合并后的 repository 在所有 owned insert 中显式写入 agent id；
outbound insert 同时显式写入 agent name。fresh database 仍按顺序执行历史 migrations 以保持既有
checksum，再执行 migration 17 删除 trigger。升级完成后 runtime connection 不注册
`bcn_agent_id()` / `bcn_agent_name()`。

v16 fixture upgrade test 断言：所有既有 Agent ownership 数据不变、上述 trigger 不存在、新写入
显式获得正确 ownership、跨 Agent identity 仍可共存。Task 3 的 Message 合表 migration 因此使用
version 18。

#### 1.5 Repository 合并与 Agent scope

修改/删除：

- `src/bazaar_compute_node/contrib/sqlite/repository.py`
- `src/bazaar_compute_node/contrib/sqlite/scoped_repository.py`
- `src/bazaar_compute_node/contrib/sqlite/reminder_repository.py`
- `src/bazaar_compute_node/contrib/sqlite/handoff_repository.py`
- `src/bazaar_compute_node/contrib/sqlite/storage.py`

最终只保留一个 `SqliteRepository`。它由 `SessionOperations`、`MessageOperations`、
`ReminderOperations`、`HandoffOperations` 四组无状态能力与 core `StorageOperationMixin`
装配而成；共享的 `RepositoryBase` 是唯一状态边界，只持有当前 `SqliteSession`
和 immutable agent id/name。具体 repository 不导入 `SqliteExecutor`，不继承 executor/database
class，不持有 raw connection，也不实现 async context manager。`SqliteStorageScope`
是薄 Agent-bound façade，只负责把 immutable agent id/name 传给 repository operation；
node storage 只暴露 global Reminder operation 与 lifecycle。

所有 Agent-owned SQL 显式使用 `agent_id = ?` 参数；insert 显式写入 agent id/name。删除
`_active_agent_id`、`_active_agent_name`、`_bind_agent_scope()`、`_clear_agent_scope()`、
`bcn_agent_id()`、`bcn_agent_name()` 及相关 trigger/UDF 依赖。跨 Agent global Reminder 查询不传
Agent context，并只存在于 node storage façade。

简单 repository 方法调用 database façade 的 query/execute convenience API。连续读取使用一次
`async with database.reader() as session`；需要一致 snapshot 时在其中增加
`async with session.transaction()`；多写 operation 使用一次 `transaction_write`。内部所有 SQL
通过 session 执行。repository 不根据 SQL 字符串自行选择 connection，也不出现 pool acquire、
writer queue 或 transaction lock。

路由与 transaction 固定如下：

| Operation | SQLite path | Explicit transaction |
|---|---|---|
| 单次纯查询、pending count、frontier | read pool | 无 |
| history + references + snapshot、wake context | one reader session | `session.transaction()` |
| 单条 insert/update/delete | writer actor | 无；使用 SQLite statement atomicity |
| freshness read + pending outbound insert | writer actor | 无；writer ownership 防止 BCN 内写入交错 |
| check batch read + 单条 cursor/read-marker update | writer actor | 无；writer ownership 防止 BCN 内写入交错 |
| inbound session/message/attachment/activity persist | writer actor | `BEGIN IMMEDIATE` |
| Reminder occurrence insert + Reminder update | writer actor | `BEGIN IMMEDIATE` |
| schema migration | startup writer connection | 现有 migration transaction |

#### 1.6 Core call-site migration

修改：

- `src/bazaar_compute_node/app/agent.py`
- `src/bazaar_compute_node/core/orchestration/command.py`
- `src/bazaar_compute_node/core/orchestration/handoff_command.py`
- `src/bazaar_compute_node/core/orchestration/reminder.py`
- `src/bazaar_compute_node/core/orchestration/reminder_command.py`
- `src/bazaar_compute_node/core/orchestration/services.py`
- `src/bazaar_compute_node/core/orchestration/session.py`

迁移规则：

1. 单次 lookup/count/list/save 改为直接 `await storage.<operation>()`；
2. `_record_inbound` 的持久化部分整体替换为 `record_inbound`，audit 与 runtime enqueue 保留在
   orchestration；
3. message check/read/inbox/send preflight 分别调用对应组合 operation，不再在 service 中拼 cursor
   与 repository CRUD；
4. Reminder/Handoff check 与 Reminder fire 调用消费/触发 operation，publish wake 与 audit 保留在
   orchestration；
5. runtime wake path 使用 wake load operation，不再连续读取 pending/session/anchor；
6. 所有 call site 删除 `async with storage.transaction()`，也不接触 executor。

#### 1.7 Focused verification

更新/新增：

- `tests/core/test_ports.py`
- `tests/contrib/test_sqlite_executor.py`
- `tests/contrib/test_sqlite_database.py`
- `tests/contrib/test_sqlite_inbox.py`
- `tests/contrib/test_sqlite_handoff.py`
- `tests/contrib/test_orchestration.py`
- `tests/core/test_reminder_scheduler.py`
- `tests/support/src/bcn_test_support/storage.py`
- `tests/support/src/bcn_test_support/reminder_storage.py`

新增测试只保留四个 executor 集成场景：read pool/WAL/snapshot、writer FIFO/rollback、queued 与
active cancellation、query-only reader/shutdown drain。migration、Agent scope 和既有 message、
Reminder、Handoff、runtime wake、attachment 行为复用现有公共行为测试覆盖；删除 transaction、
query plan 等实现细节测试，不为内部纯函数、机械分支或反向路径新增测试。

Focused verification：

```bash
uv run pytest tests/core/test_ports.py tests/contrib/test_sqlite_executor.py tests/contrib/test_sqlite_database.py tests/contrib/test_sqlite_inbox.py tests/contrib/test_sqlite_handoff.py tests/contrib/test_orchestration.py tests/core/test_reminder_scheduler.py -q
uv run ruff check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/core tests/contrib tests/support
uv run ruff format --check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/core tests/contrib tests/support
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
git diff --check
```

完成条件：core 与调用方不再出现 storage transaction/connection 概念；`contrib/sqlite` 只有一个
writer actor、一个两连接 read pool、一个 executor 和一个 repository；除 migration 17 删除
ownership trigger 外，现有 schema、command output 与持久化行为保持不变。完成 focused
verification 后停下 review，再开始 Task 2。

### Task 2：Core model

- 用 `Message` + `MessageDirection` 替换两个 Message dataclass；
- 合并 serializer 所需的通用属性；
- 保留现有 outbound delivery transition 校验，仅允许 outbound Message 调用；
- 更新 storage/command/channel 类型签名，保持命令返回文本。

### Task 3：Migration 与 codec

- 实现 v18 单表 migration、row codec 和 input validation；
- 本 Task 独立对 v18 migration 执行 fixture verification，但不立即加入 runtime
  `MIGRATIONS` ledger；Task 4 在 repository 切换到 `messages` 的同一个提交中注册 v18，
  避免产生“旧 repository 已失去物理表”的不可运行中间状态；
- 增加从真实 v16 fixture 升级的测试，覆盖两种 direction、全部 outbound state、cursor、
  reply、attachments 与多 agent 数据；
- 明确断言 cursor/snapshot 迁移后仍指向原消息边界。

### Task 4：Storage operation 切换单表

- 所有 message workflow 改查 `messages`；
- inbound workflow 使用 direction + seq；
- outbound workflow 使用 direction + delivery state；
- 通用 history/catalog 使用统一 seq 与可见 outbound state；
- MemoryStorage 采用同一模型，保持测试替身与 SQLite 语义一致。

### Task 5：Orchestration 与命令行为

- Channel 收到的数据构造 inbound direction Message；
- send 构造 outbound direction Message，并沿现有状态机更新同一行；
- check、fresh-check、wake 继续只读取 inbound；
- read 与 inbox list 改用通用 history/catalog；
- read 的 around anchor 同时支持 inbound 与已接受 outbound message id；
- 保持 reply、Reminder、handoff、approval 和 error feedback 的业务判断。

### Task 6：清理与验证

- 删除旧 model/codec/table 名称和失效索引；
- 更新两份现有架构计划中关于双 message table 的描述；
- 运行 storage operation tests、migration focused tests、command-process tests、完整 test suite、
  Ruff、Pyright、compileall、lock verification 与 `git diff --check`。

## 验收标准

- 数据库只存在一个 `messages` 表，旧 inbound/outbound 行及其状态无损保留；
- cursor、snapshot、current inbound seq 在迁移后仍指向原消息边界；
- check 的结果、pending 数量和 cursor 推进与迁移前一致；
- send 的 pending 到 terminal transition 与迁移前一致；
- read 按统一 seq 返回 inbound 和已接受 outbound；
- read 可以围绕已接受 outbound message id 定位窗口，并显示当前 agent sender/type；
- inbox latest 可以是 inbound 或已接受 outbound；
- check 不返回 outbound；
- failed/partial/unknown/pending outbound 不进入会话历史；
- core storage port 不暴露 transaction、connection、actor 或 SQLite 概念；
- SQLite write connection 只由 writer actor 使用，pure read operation 通过 bounded read pool
  并发执行，调用方只执行异步 storage operation；
- `contrib/sqlite` 的 repository 和后续新增 SQL 统一通过 internal executor 执行，不直接管理
  connection、pool 或 writer request；
- SQLite 不存在 scoped/unscoped repository 方法副本、多个有状态具体 repository、
  domain mixin 方法覆盖、database-global Agent binding 或 transaction lock；
- mutation/read-modify-write operation 按 writer queue 顺序执行；pure read operation 可以彼此
  并发，并可在 WAL 下与 writer 并发；
- Agent operation 无法访问其他 Agent 数据，global Reminder operation 仍覆盖全部 Agent；
- inbound persist 与 Reminder fire 的多写失败会整体回滚；
- agent scope、provider dedup、command idempotency、reply 与附件引用不回退。
