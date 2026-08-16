# 2026-08-16 Localtime Reminder & Docs Plan

## 状态

- 模式：Plan
- 状态：待 review；review 通过后只进入 Task 1A。
- 基线：`main@caf71dbd9b20af99d769c8d1db8d133fcb4221ec`。
- 分支：`agent/localtime-reminder-docs`。
- 本计划解决 agent-facing 时间显示与 Reminder 默认 calendar timezone 的本机时区适配，并同步 README。
- 所有持久时间继续使用现有 UTC/epoch milliseconds；不把 SQLite、scheduler 或 domain deadline 改成本地时间。
- 按 Task 串行开发，每完成一个 Task 停在 review；未经 review 不进入下一 Task。

## 1. 问题与目标

当前 `bcc message check/read` 选择 `provider_time_ms`，无 provider time 时回退 `received_at_ms`，最终由 `format_message_time()` 强制按 UTC 格式化为：

```text
time=2026-08-16 04:08:00
```

该文本既没有 `Z` 也没有 UTC offset，看起来像本地 wall-clock time。Agent 在安排 Reminder 时可能把该 UTC wall-clock 值当成运行 BCN 主机的当地时间，从而产生固定时区偏移。

同时，`bcc reminder schedule --repeat ...` 未提供 `--tz` 时，当前 domain 默认使用 `UTC`。对于“每天 09:00”这类自然 calendar recurrence，默认 UTC 与运行用户的系统 localtime 直觉不一致。

目标：

1. **UTC storage 不变**：`received_at_ms`、`provider_time_ms`、`next_fire_at_ms`、`scheduled_for_ms`、`fired_at_ms` 等继续表示 absolute epoch milliseconds。
2. **Agent-facing 时间使用系统 localtime**：BCC 输出时间使用系统 timezone 转换后的 ISO-8601 文本，并始终带 UTC offset，避免无时区 wall-clock 文本。
3. **Calendar Reminder 默认采用系统 timezone**：未显式传 `--tz` 时，在 schedule command boundary 读取系统 IANA timezone，并将该具体 timezone 持久化到 Reminder。
4. **显式 timezone/absolute time 保持权威**：`--tz` 继续覆盖系统 timezone；`--fire-at` 继续要求显式 ISO-8601 offset，不接受无 offset 的本地时间。
5. **README 与当前能力一致**：Reminder/定时任务从 Roadmap 移到已支持能力，并给出简短使用说明。

## 2. 设计边界

### 2.1 UTC 是 durable source of truth

不修改：

- SQLite timestamp column 语义；
- scheduler wall-clock deadline 比较；
- TimerWheel monotonic waiting；
- occurrence materialization；
- recurrence 的 UTC instant 计算结果；
- `--fire-at` 必须携带 offset 的 contract。

本次变化只发生在：

```text
system timezone
    -> command/presentation boundary
    -> local ISO-8601 display / omitted --tz resolution

UTC epoch milliseconds
    -> durable storage / scheduler / occurrence truth
```

### 2.2 Localtime 显示必须带 offset

禁止继续输出无 timezone 标识的 wall-clock：

```text
2026-08-16 12:08:00
```

BCC agent-facing timestamp 统一使用类似：

```text
2026-08-16T12:08:00+08:00
2026-11-01T01:30:00-04:00
```

offset 是输出 contract 的一部分，避免 agent 猜测 timezone。

首版不额外输出 timezone abbreviation（如 CST/EST），因为缩写存在歧义。

### 2.3 System timezone 需要 IANA identity

Calendar recurrence (`daily@...` / `weekly:...@...`) 不能只持久化当前固定 offset；DST 地区需要具体 IANA timezone，例如：

```text
Asia/Shanghai
America/New_York
Europe/Berlin
```

因此系统 local timezone resolver 必须跨 Linux/macOS/Windows 返回可供 `ZoneInfo` 使用的 IANA name。优先采用成熟的小型 timezone resolver 依赖，而不是自行解析 `/etc/localtime`、macOS 配置或 Windows registry/mapping。

如果系统 timezone 无法解析为 IANA name，省略 `--tz` 的 calendar schedule 必须 fail closed，并提示显式传 `--tz`；不能静默退回 UTC。

### 2.4 默认 timezone 在创建时固化

未提供 `--tz` 时：

1. schedule command 执行时读取 BCN 主机当前 system timezone；
2. 将具体 IANA name 传给 Reminder domain；
3. 将该 name 持久化到 Reminder definition。

之后用户改变 OS timezone，不会静默改变已有 recurring Reminder 的 schedule 语义。新创建的 Reminder 使用新的系统 timezone。

显式 `--tz` 永远优先。

`every:15m` / `every:2h` / `every:1d` 仍然是 elapsed interval，不因 timezone/DST 改变；timezone 仅影响 calendar recurrence。

### 2.5 Message timestamp source 不在本次重定义

`bcc message check/read` 继续遵循现有 source precedence：

```text
provider_time_ms if present
else received_at_ms
```

本次只改变 presentation timezone，不把 WeCom `create_time` 解析/绑定为 `provider_time_ms`；provider timestamp 语义若要调整，单独讨论。

## 3. 目标修改范围

预计生产代码：

```text
pyproject.toml / uv.lock                         # system IANA timezone resolver dependency（若需要）
src/bazaar_compute_node/app/command.py          # message timestamp local ISO display
src/bazaar_compute_node/app/reminder_dispatch.py # omitted --tz -> system IANA timezone
src/bazaar_compute_node/bcc.py                  # Reminder timestamp local ISO display/help
src/bazaar_compute_node/core/instruction.py     # agent time semantics
README.md                                       # Reminder 已支持 + timezone 说明
```

测试按当前模块归属补充，不为了测试增加 production injection/environment override。

不修改 SQLite schema，不做 Reminder data migration。

## 4. Task 1A：Localtime presentation 与 Reminder default timezone

### 实施

1. 增加跨平台 system IANA timezone resolution 能力。
2. `format_message_time(timestamp_ms)` 从 UTC 无 offset 文本改为 system-local ISO-8601 + offset。
3. Reminder 的 agent-facing `scheduled/fired/next/canceled` timestamp 同样转换为 system-local ISO-8601 + offset。
4. `bcc reminder schedule` 未指定 `--tz` 时，在 command boundary 读取 system IANA timezone并显式传入 `ReminderScheduleRequest`。
5. 显式 `--tz` 不做替换。
6. `--fire-at` 继续要求 explicit offset，并仍转换为 UTC epoch storage。
7. system timezone resolution 失败时，省略 `--tz` 的 schedule 返回清晰 handled error，并提示显式 `--tz`。
8. 不修改 `canonical_timezone(None)` 这类纯 domain helper 使其隐式依赖 host；host localtime 解析停留在 app/command boundary，domain 仍接收显式 timezone value。

### Focused tests

- message timestamp 在非 UTC local timezone 下输出 ISO offset；
- UTC local timezone 输出 `+00:00`，不输出无 offset wall-clock；
- DST zone 在不同日期显示不同 offset；
- omitted `--tz` schedule 持久化 system IANA zone；
- explicit `--tz` override；
- `every:*` elapsed recurrence 不因 local zone 改变 interval；
- `daily@` / `weekly@` 使用持久化 IANA timezone 处理 DST；
- `--fire-at` explicit offset 仍映射到相同 UTC epoch；
- system timezone 无法解析时 fail closed，不 fallback UTC。

### 完成条件

- durable timestamp/storage/scheduler semantics 无变化；
- agent-facing timestamp 不再有 timezone ambiguity；
- focused checks、Ruff、Pyright、compileall、LSP 按仓库规则通过；
- 发送业务 diff 并停在 review。

## 5. Task 1B：Help、developer instruction 与 README

### 实施

1. `bcc reminder schedule --help` 明确：
   - `--tz` 省略时默认 BCN 主机 system timezone；
   - `--fire-at` 必须带 explicit offset；
   - calendar recurrence 以 persisted IANA timezone 解释。
2. developer instruction 明确：
   - `bcc` 展示的 message/reminder timestamps 是 BCN 主机 localtime，并带 UTC offset；
   - 不应把显示时间重新解释成 UTC 或无 offset local wall-clock；
   - calendar reminder 未传 `--tz` 时使用 BCN 主机 system timezone。
3. README：
   - 在“核心能力”加入持久 Reminder / 定时任务；
   - “当前支持”说明一次性与周期性 Reminder、daemon restart recovery、session-owned wake；
   - 从 Roadmap 删除已经完成的“定时任务”；
   - 增加简短 CLI 示例；
   - 明确 UTC durable storage 与 localtime presentation/default calendar timezone 的区别。
4. 不在 README 宣称外部 Channel 自动收到 Reminder fire；Reminder fire 只唤醒 owner runtime，Agent 后续是否发消息仍走正常 `bcc message send`。

### Focused tests

- help contract 包含 system timezone / explicit offset 语义；
- instruction 无 UTC/local ambiguity；
- README 不再把 Reminder 列为 Roadmap；
- README command surface 与实际 parser 一致。

### 完成条件

- 文档与实际功能一致；
- 不宣称 `reminder log`、App inbox、channel-visible fire receipt 等不存在能力；
- focused checks、Ruff、Pyright、compileall、LSP 按仓库规则通过；
- 发送业务 diff 并停在 review。

## 6. 风险与约束

1. **System timezone ≠ fixed offset**：不能用当前 `datetime.now().astimezone().utcoffset()` 代替 IANA timezone，否则 DST recurrence 会在未来漂移。
2. **展示与存储分离**：显示 localtime 不代表 DB 改存 localtime；所有排序、due、restart recovery 继续基于 UTC epoch。
3. **已有 Reminder**：已有 definition 继续使用其已持久化 timezone；本次不批量 reinterpret 已有 Reminder。
4. **OS timezone change**：已有 recurring Reminder 不跟随系统设置变化；新 schedule 才采用新的 local zone。
5. **Provider timestamp**：WeCom `create_time` 当前只作为 metadata；本次不把它提升成 provider timestamp，以免把两个问题混在一个 follow-up。
6. **Cross-platform**：timezone resolver 必须覆盖 Linux/macOS/Windows；不写平台私有解析分支作为首选实现。

## 7. Code 模式入口条件

review 本计划后从 Task 1A 开始。Task 1A 完成并 review 前，不提前修改 README/instruction；Task 1B 完成后再决定是否需要独立 Draft PR/发布动作。