# Runtime Session 进程内生命周期与空闲回收

## 状态

- 当前阶段：设计完成，等待 review。
- 实施分支：`f-20260814-runtime-session-idle-timeout`。
- 基线：`main@18ab8b8`。
- 功能代码尚未修改；本计划不授权 commit、push、PR、发布或部署。

## 目标

1. 删除 `bcc session` 到 `runtime session` 的持久化映射与 `runtime_sessions` 实体表。
2. 每个 BCN daemon 进程只在收到需要通知 runtime 的新输入时，按 `bcn_session_id` 创建或复用一个进程内
   `RuntimeSession`。
3. daemon 重启后不恢复上一个 provider thread；下一条新输入创建全新的 runtime session 和 provider thread。
4. 在 `[runtime]` 增加以秒为单位的有限数值 `idle_timeout`。值小于或等于 `0` 时保持常驻；值大于 `0` 时，
   每条新 inbound 通过 node-scoped `TimerWheel` 刷新当前 session 的 deadline，deadline 到期后在
   `AgentState` 可安全进入 `IDLE` 时关闭当前 runtime session。
5. 空闲回收完成后，下一条新输入创建新的 runtime session；旧进程持有的 bcc session binding 立即失效。

## 问题与证据

- `SessionOrchestrator._record_inbound()`、`_runtime_loop()`、`_run_notification()` 和
  `_ensure_runtime_session()` 当前通过 `IStorageTransaction.find_runtime_session()` 读取或更新 runtime
  mapping，进程启动后会继续恢复已持久化的 provider thread。
- `NodeApplication._validate_session_binding()` 也以 SQLite mapping 作为 bcc capability 的绑定依据，导致
  command authorization 与本应短暂存在的 runtime process 生命周期耦合。
- SQLite v1 创建 `runtime_sessions`，v2 创建 `idx_runtime_sessions_bcn`；repository、codec 与测试 storage
  都继续维护这张表。
- 当前每个 BCN session 的 `_runtime_loop()` 在 turn 结束后无限等待下一条通知，没有 `IDLE` 驻留时长和
  session 回收入口。
- `_stop_runtime_session_locked()` 当前无论 provider stop 结果如何都会移除内存映射，同时把状态停在
  `STOPPING`；这无法表达“已确认关闭后可创建全新 session”和“关闭结果未知时仍需恢复”的不同语义。

## 已确认边界

- `channel_sessions`、`bcn_sessions`、inbound/outbound messages、consumer cursor 与 `runtime_attempts`
  继续持久化。`runtime_attempts` 仍是 turn 幂等与审计事实，其 `session_id` 只保存当次短暂 runtime
  session 的关联标识，不再要求对应数据库实体存在。
- `RuntimeSession` 继续作为 core 与 runtime adapter 之间的 provider-neutral 内存值对象；删除的是 storage
  contract、repository 和表，不是 runtime port 的参数类型。
- 同一 daemon 进程内，已启动且未回收的 session 继续复用当前 provider thread；仅在同一进程内出现可恢复的
  runtime unavailable/unknown 状态时允许 `resume_session()`。
- daemon 重启和已确认的空闲回收都清除进程内 mapping。随后只执行 `start_session()`，不从历史数据库恢复
  provider thread。
- 每条新 inbound 都刷新 node-scoped `TimerWheel` 中当前 live session 的 deadline。expiry 在 `IDLE` 时
  到达就立即回收；在 `WORKING` 或 compaction 期间到达只记录 expired，不中断活跃 turn，且在 deadline
  generation 保持不变时于状态回到 `IDLE` 后立即回收。
- startup、reconciliation 与 stop 期间不执行并发回收；pending notification 会先刷新 deadline，再进入
  当前或下一次 turn。
- provider stop 确认成功后删除 live mapping 并把 process-local state 复位为 `CREATED` 语义；stop failed
  或 unknown 时保留 mapping，并分别进入 `FAILED` 或 `UNKNOWN`，让下一条输入沿现有恢复状态机处理。
- `idle_timeout` 接受有限整数或浮点数；默认 `0`，所有小于或等于 `0` 的值都表示常驻。

## 设计

### 进程内唯一真值

`SessionOrchestrator` 的 runtime registry 改为 `bcn_session_id -> RuntimeSession`。新 session 使用 UUIDv7
生成独立 `runtime_session_id`，避免超时前后的旧 bcc 环境重新获得授权。

runtime queue item 只携带 durable `ChannelSession`、`BcnSession` 和 inbound message，不缓存可能在队列等待
期间失效的 runtime context。需要通知 runtime 的 item 由 worker 在开始 turn 时读取 live registry；registry
为空时创建新的内存 session。turn steer 同样只读取 live registry，不再回查 storage。

### bcc session binding

`NodeApplication._validate_session_binding()` 先确认 durable BCN session 存在，再把请求中的
`runtime_session_id` 与 orchestrator 当前 live session 精确比较，并继续使用 constant-time capability
比较。`_run_runtime_command()` 也必须取得当前 live session，不再构造 `runtime-{bcn_session_id}` fallback。

因此已关闭 session 的旧 `BCN_RUNTIME_SESSION_ID` 无法调用当前 session；新 provider process 会收到新的
runtime session id。capability 本身仍保留在进程内，授权成立必须同时满足 live session id 与 capability。

### 空闲回收与并发边界

在 `core/timerwheel.py` 新增进程级通用 `TimerWheel` service。时间轴使用 10 ms tick，`current_tick` 取 event
loop monotonic milliseconds 的整除值。delay 向上取整为 tick，确保 timer 不会早于请求时长触发；`0` 在下一次
driver 推进时触发。单个 timer 的最大 delay 为 `2^32 - 1` ticks，约 497 天。

wheel 由 256-slot near 层和 4 个 64-slot level 组成，总共覆盖 32 bit tick delta。插入时以
`deadline_tick - current_tick` 选择 bucket：低于 `2^8` 进入 near，之后依次按 `2^(8 + 6n)` 边界进入四个
level；slot 分别取 deadline 的低 8 bit 或对应 6 bit。每个 bucket 使用 keyed entry container，entry 保存
`timer_id`、absolute deadline tick、generation、level 与 slot；全局 index 由 `timer_id` 直接定位 entry，
因此 create、reset 和 cancel 的插入/摘除平均为 O(1)。

driver 使用 absolute monotonic target tick 推进，避免重复 sleep 产生累计漂移。near 回绕时，从第一个需要推进
的高层取出当前 slot，按每个 entry 的 absolute deadline 重新插入较低层；当前 near slot 到期时先整体摘出，
再把 expiry 写入 timer mailbox。普通推进成本为 O(elapsed ticks + cascaded entries + expired entries)。若一次
晚醒跨越至少 256 ticks，且 elapsed ticks 大于 active timer 数量，wheel 直接以 target tick 重建 active entries：
deadline 已到的 timer 入 mailbox，其余 timer 重新分桶，成本为 O(active timers)；否则继续按 tick/cascade
推进，选择两条路径中更低的预计遍历量。

BCN composition root 只创建一个 `TimerWheel`。`NodeApplication.start()` 在启动 session/runtime consumer 前
`await timer_wheel.start()`，由单个 background `asyncio.Task` 驱动；所有 wheel 与 timer 操作都绑定该 event
loop。shutdown 先停止 ingress 与 runtime consumer、由各 consumer cancel 自己的 timer，最后
`await timer_wheel.close()`；close 取消并等待 driver，随后结束全部 timer waiter。

`TimerWheel.create(delay_ms)` 接受 `0` 到最大 horizon 的整数毫秒并返回 `Timer`；每个 timer 内部持有
单消费者 asyncio mailbox，业务方直接 `await timer.wait()`，正常返回表示当前 generation 已到期。wheel
到期路径只写 mailbox，业务 coroutine 在
自己的 task 中恢复。timer 另外提供 `reset(delay_ms)`、`cancel()`、`active`、只读 `deadline_ms` 和 generation。

reset 递增 generation、O(1) 摘除旧 entry 并按新 deadline 重插；已经唤醒但尚未返回的旧 generation event
由 `wait()` 内部丢弃，waiter 继续等待新 deadline。cancel 幂等，并让 waiter 以 timer-cancelled 结束；wheel
close 让 waiter 以 wheel-closed 结束。一个 timer 同时只允许一个 `wait()` waiter；到期后可 reset 复用。

runtime idle consumer 为每个 live session 保存一个 `Timer` 和一个一次性 expiry watcher task。新的 inbound 在
durable dedupe/append 成功后先把 activity 投递到 per-session worker；已有 live session 时，无论该 inbound 是否
通知 runtime，worker 都立即调用 `timer.reset(idle_timeout_ms)`。不通知 runtime 的 activity 只刷新 timer，不创建
provider session、不推进 runtime cursor、不启动 turn。

首条需要通知 runtime 的 inbound 创建 live session 时，worker 按该 inbound 的 monotonic activity deadline
创建 `timer_wheel.create(delay_ms=remaining_ms)`，并用 `asyncio.create_task()` 启动 session-owned
`_forward_runtime_session_expiry(timer, queue)`；该 coroutine 只执行 `await timer.wait()`，正常返回后立即把带
timer id/generation 的 `_RuntimeExpiry` 放入现有 per-session runtime queue，然后结束。它不读取 `AgentState`、
不取得 session lock，也不调用 provider stop；因此 timer waiter 无法与业务状态机并发执行关闭。

若 provider startup 已经耗尽 deadline，watcher 会立即把 expiry 入队。timer 在 active generation 内被 inbound
reset 时，现有 watcher 继续等待新 deadline；若 watcher 已经完成，worker 在 reset 后为新 generation 创建新的
watcher。confirmed close 后 cancel timer 并 cancel/await 尚未完成的 watcher；runtime worker 保留，下一条需要
通知 runtime 的 inbound 创建新的 provider session、Timer 与 watcher。`idle_timeout <= 0` 时该 consumer 不创建
timer/watcher，但进程级 wheel 仍随 BCN lifecycle 启动，供其他 core consumer 使用。

per-session runtime queue 扩展为 notification、refresh-only activity 与 expiry 三类 item。现有 `_runtime_loop`
继续只等待 `turn_task` 与 `queue_task`；worker 消费 `_RuntimeExpiry` 后先比较 timer id/generation，忽略 reset 后
到达的 stale item，再在该 BCN session 的现有 concurrency lock 内重新检查：

1. queue 仍无新 notification；
2. registry 中仍有同一个 live session；
3. 当前 `AgentState` 仍为 `IDLE`。

三项同时成立才调用 `_stop_runtime_session_locked()`。如果 state 尚未回到 `IDLE`，worker 只记录 expired 标记，
不轮询、不打断 turn；turn/compaction 回到 `IDLE` 后由同一个 worker 再次复核。期间任一新 inbound 都 reset timer
并清除 expired；FIFO queue 与 generation check 共同决定 inbound/expiry 竞态。若新消息在 worker 已通过复核并
开始 confirmed stop 后到达，它只作为新 activity 留在 queue，不携带旧 runtime context；关闭完成后 worker
重新读取 registry，并为该 notification 创建新 session。

watcher 是 session orchestration 自己持有并监督的一次性 bridge task，不属于 `TimerWheel` driver；它只把 timer
完成转换成 mailbox item。session replacement、confirmed stop 与 daemon shutdown 都 cancel/await 未完成 watcher，
避免 detached task；timer cancel 或 wheel close 唤醒 `wait()` 时，watcher 正常退出。

### SQLite 边界

新增顺序 migration 删除 `idx_runtime_sessions_bcn` 和 `runtime_sessions`，保留既有 migration checksum 与
ledger 连续性。删除 `IStorageTransaction`、SQLite repository/codec 和 test storage 中的 runtime session
CRUD；`save_runtime_attempt()` 只校验不可变 attempt 自身，不再执行已删除实体的存在性检查。

## 实施顺序

### Task 1.1：删除 runtime session 持久化边界

修改文件：

- `src/bazaar_compute_node/core/storage.py`
- `src/bazaar_compute_node/contrib/sqlite/migrations.py`
- `src/bazaar_compute_node/contrib/sqlite/repository.py`
- `src/bazaar_compute_node/contrib/sqlite/codec.py`
- `tests/support/src/bcn_test_support/storage.py`
- `tests/support/src/bcn_test_support/control.py`
- `tests/contrib/test_sqlite_database.py`
- `tests/contrib/test_sqlite_session_repository.py`
- `tests/contrib/test_sqlite_runtime_repository.py`
- `tests/contrib/test_memory_storage.py`

实施动作：

1. 从 storage protocol 和两套 repository 删除 `get/find/save_runtime_session`。
2. 增加 SQLite migration，删除 runtime mapping 表及索引，并把 schema version 更新到新版本。
3. 保留 `runtime_attempts`，解除其对 runtime session row 的存在性依赖。
4. 删除只服务于持久化 mapping 的 codec、test fixture、snapshot 字段与 repository 用例。

Focused tests：覆盖全新数据库初始化、已有数据库顺序迁移，以及迁移后 `runtime_attempts` 与 channel/bcn
session graph 的持续读写；attempt 保持 immutable，transaction rollback 语义保持一致。

完成条件与停止点：storage interface、SQLite 与 test storage 收敛到 durable session/message/attempt contract，
schema migration version 连续更新；提交业务 diff，停在 Task 1.1 review，不进入 Task 1.2。

### Task 1.2：建立进程内 runtime session 真值与 bcc 绑定

修改文件：

- `src/bazaar_compute_node/core/orchestration/session.py`
- `src/bazaar_compute_node/core/orchestration/services.py`
- `src/bazaar_compute_node/app/application.py`
- `tests/contrib/test_orchestration.py`
- `tests/app/test_composition.py`
- `tests/app/test_bcc_process.py`

实施动作：

1. 把 registry 改为按 `bcn_session_id` 索引，并在 runtime worker 消费 notification 时创建 UUIDv7 session。
2. 删除 ingress、turn steer、turn start 和 provider start confirmed 路径中的 storage mapping 读写。
3. 让 confirmed stop 原子清除 live mapping 与 state；failed/unknown stop 保留可恢复上下文并写入对应状态。
4. 让 bcc validator 和 runtime command runner 只接受 orchestrator 当前 live session，删除 deterministic fallback。

Focused tests：覆盖同进程复用、daemon lifecycle 后创建新 runtime、confirmed stop 后生成新 runtime id、
failed/unknown stop 保留恢复上下文、当前 live runtime 的 bcc binding 成功、两个 BCN session 相互隔离、turn
steer 使用当前 live session。

完成条件与停止点：所有 runtime/provider thread mapping 只存在于 orchestrator 内存，SQLite 重启不影响 durable
消息但不会恢复旧 provider thread；提交业务 diff，停在 Task 1.2 review，不进入 Task 1.3。

### Task 1.3：接入 `runtime.idle_timeout` 与 deadline 回收

修改文件：

- `src/bazaar_compute_node/app/config.py`
- `src/bazaar_compute_node/cli.py`
- `src/bazaar_compute_node/app/application.py`
- `src/bazaar_compute_node/core/timerwheel.py`
- `src/bazaar_compute_node/core/orchestration/session.py`
- `README.md`
- `tests/test_cli.py`
- `tests/core/test_timerwheel.py`
- `tests/contrib/test_orchestration.py`

实施动作：

1. 解析并校验 `[runtime].idle_timeout` 有限整数或浮点数，经 composition root 转换为整数毫秒并注入
   `SessionOrchestrator`。
2. 在 core 实现通用 `TimerWheel`/`Timer`：async `start/close` lifecycle、单 background task、
   256-slot near、4 × 64-slot levels、10 ms driver、
   `create/reset/cancel`、cascade、missed-tick catch-up、O(1) reschedule/remove 和 generation 去陈旧；
   timer 内封装单消费者 async mailbox，`wait()` 以 awaitable 方式交付结果；时间轮不调用业务函数，也不包含
   runtime session 语义。
3. 每条新 inbound 刷新已有 live session；需要启动 runtime 的首条 inbound 在建立 worker 时加入 wheel，
   不通知 runtime 的 inbound 不单独创建 session 或 turn。
4. composition root 把同一个 wheel 注入 application/orchestrator；application startup/shutdown 管理 wheel driver。
   每个 live runtime session 创建一个一次性 `_forward_runtime_session_expiry()` bridge task，在 task 内
   `await timer.wait()` 后只投递 `_RuntimeExpiry`。现有 worker 串行消费 notification/activity/expiry，并通过
   session lock 执行 timer、queue、state、live-session 四重复核；active turn 到期只记录 expired，回到 IDLE
   后复核，不轮询、不并发 stop。
5. confirmed timeout stop 后 cancel timer 并 cancel/await 未完成 watcher；runtime worker 保持可接收下一条
   notification；下一次处理创建
   全新 session。
6. 在 README 的 runtime 配置示例中说明秒单位、支持小数、默认常驻和 `<= 0` 语义。

Focused tests：覆盖默认常驻、正值整数与小数、`<= 0` 常驻语义；通用 wheel 的 create/reset/cancel、timer
状态、`wait()` 单 waiter、reset/expiry 竞态、cancel/close 唤醒、near 边界、四级选择/cascade、跨层
refresh/remove、generation 去陈旧、普通 catch-up、大跨度 O(active timers) rebuild 与 startup/shutdown；
通知与不通知 runtime 的新 inbound
都能刷新已有 live session，后者不创建 session/turn；`IDLE` 到期关闭；`WORKING`/compaction 到期不被
中断且回到 `IDLE` 后立即关闭；deadline 前新消息复用当前 session；deadline 边界新消息在关闭后使用新
session；多 BCN session 独立计时；daemon shutdown 完成 wheel task/worker 的结构化收尾。

完成条件与停止点：配置、lifecycle 与并发语义全部闭合；提交业务 diff，停在 Task 1.3 review，不进入最终验收。

### Task 1.4：最终验收

验证范围：

1. focused tests 与完整 test suite；Ruff、Pyright、compileall、lock verification 和 `git diff --check`。
2. Neovim LSP 实际 attach 后检查全部改动 Python 文件 diagnostics。
3. 使用真实 Codex runtime 验证常驻配置下连续两次输入复用同一 provider thread。
4. 使用正值 `idle_timeout` 验证 turn 完成并进入 `IDLE` 后 provider process 到期退出，下一条输入产生不同的
   runtime session id 与 provider thread。
5. 验证 daemon 重启后下一条输入产生新 provider thread，历史消息与 runtime attempt 事实持续可读。
6. 回收后由新 provider environment 使用当前 runtime session id/capability 完成 bcc 调用。

完成条件与停止点：提供业务 diff 与验收证据，停在 task review；不执行 commit、push、PR、发布或部署。

## 最终验收

- 数据库迁移后 durable message、cursor、outbound state 与 runtime attempt 持续可读写。
- daemon 进程内一个 `bcn_session_id` 同时最多对应一个 live `RuntimeSession`。
- 默认配置保持 runtime session 常驻，不引入行为变化。
- 正值 `idle_timeout` 由 node-scoped timing wheel 按新 inbound 刷新；expiry 到达时只关闭 `IDLE` 且 queue
  quiescent 的 session，active turn 不被中断，并在 deadline generation 保持不变时于下一次 `IDLE` 立即回收。
- confirmed 回收或 daemon 重启后，新输入使用新的 runtime session id 和 provider thread。
- 新 provider environment 的 bcc binding 与当前 live runtime session 精确一致。
- durable 消息、cursor、outbound state 与 runtime attempt 不因 runtime mapping 删除而丢失。
