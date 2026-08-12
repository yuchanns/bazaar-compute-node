# 移除 Runtime Event 持久化

## 目标

`runtime_events` 是一份只写不读的诊断副本。启动恢复、inbox 投递、turn 防重、outbound
投递和 Agent 状态恢复都不会读取它。本任务彻底移除这条数据库边界，同时保留驱动当前活跃
turn 所需的内存生命周期。

迁移需要删除历史表和索引，并在 schema transaction 外压缩数据库，让操作系统看到的文件
实际缩小。压缩必须可安全重试：如果进程在 schema commit 之后、`VACUUM` 完成之前退出，
下次启动必须继续执行。

未来 Node 向 Server 上报可观测事件不在本任务范围内。后续可由 `bazaar-compute-server`
实时接收 Node 事件并负责存储，Node 数据库不保留临时兼容副本。

## 持久化边界

以下 SQLite 数据会继续保留，因为应用会读取它们来完成业务行为或恢复：

- `channel_sessions`、`bcn_sessions` 和 `runtime_sessions`：稳定身份与 provider thread
  绑定；
- `runtime_attempts`：同一 inbound 只触发一次 turn；
- `inbound_messages` 和 `consumer_cursors`：inbox 顺序与投递；
- `outbound_messages`：fresh-check、provider attempt、draft 和投递结果；
- `node_state` 和 `schema_migrations`：Node 身份与 schema 完整性。

`RuntimeEvent` 继续作为 provider-neutral 的内存生命周期对象，但删除数据库序号、数据库身份、
跨表引用和存储元数据字段。活跃 turn coordinator 直接校验当前 turn 与 provider turn 关联，
推进 `AgentState`，并发出既有的脱敏 audit，不再打开 storage transaction。

普通 provider `turn/started`、`item/started` 和 `item/completed` 不会在 coordinator 自己
发出的 turn-start 之外改变状态，因此不再转成 runtime audit event。Delta/progress 继续走既有
有界 `StreamEvent` 路径；terminal、failure、cancellation、unknown、protocol 和 transport
事件继续作为内存生命周期信号。

## 迁移与压缩

新增 migration 9，包含两项 schema 变更：

1. 为 `schema_migrations` 增加可空的 `compaction_completed_at_ms`；
2. 删除 `runtime_events`，其三个索引随表一起删除。

Migration transaction commit 后，`SqliteDatabase.start()` 检查 migration 9 的
`compaction_completed_at_ms`：

- 值为空时，在独占的启动连接上、显式 transaction 之外执行 `VACUUM`；
- `VACUUM` 成功后，通过新 transaction 写入完成时间，再 truncate WAL checkpoint；
- 若进程或 `VACUUM` 在写入完成时间前失败，SQLite 保留原始有效数据库，下次启动重试；
- 完成时间已写入时，后续普通启动跳过压缩。

这里使用 migration ledger marker，而不是根据文件大小做 best-effort 判断，从而提供可重试的
一次性完成语义；无需新增永久 maintenance table，也不会因无关 delete 在每次启动重复 vacuum。

## 实施任务

1. 将 `RuntimeEvent` 收敛到实时生命周期 reduction 和脱敏 audit 真正使用的字段，并同步更新
   Codex runtime 与 test runtime。
2. 从 `IStorageTransaction`、SQLite repository/codec 和内存 test storage 中删除
   `append_runtime_event`。
3. 在既有 per-session concurrency lock 内直接应用实时 runtime event。保留 turn/provider
   correlation、terminal cleanup、`AgentState` reduction 和 audit emission。
4. 停止产生重复的普通 item/turn progress audit event，同时保留 stream update 与 terminal/error
   行为。
5. 新增 migration 9 和可安全重试的 post-migration `VACUUM`。
6. 更新 focused tests，证明：
   - 没有 event repository 时 turn 仍能完成，Agent 状态回到 idle；
   - stream event 仍绕过 storage 与 audit；
   - provider terminal/error/cancellation 语义保持不变；
   - 既有 v8 数据库会删除 `runtime_events` 及其索引；
   - 其他表与数据完整保留；
   - 数据库文件显著缩小、`freelist_count` 被回收、WAL 被 truncate、`quick_check` 为 `ok`，
     且 migration 9 标记为已压缩；
   - 已 commit schema 但未写压缩完成标记时，下次启动会安全重试；
   - 全新数据库不会暴露 `runtime_events`。
7. 运行 focused tests、完整 non-real-home suite、Ruff、Pyright、compileall、lock verification、
   diff-check，以及所有改动 Python 文件的 LSP diagnostics。

完成后携带 exact diff 与验证证据停在 review。不引入兼容表或 dual-write。
