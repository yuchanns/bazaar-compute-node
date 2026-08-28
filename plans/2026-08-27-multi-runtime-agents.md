# Multi-Runtime Agents

## 状态

- 模式：Plan。
- 状态：待 review；review 通过后只进入 Task 1。
- 分支：`feature/multi-runtime`。
- 基线：`main` 的 `d5da121cea5a463377c018de8706e93f228afc5f`（Release v0.1.32）。
- 核心约束：runtime 只在 `_create_runtime_session()` 那一刻选定，并在该 runtime session 的整个
  生命周期内保持不变。
- 测试约束：整个 feature 只新增一个 pytest test function
  `test_multi_runtime_agents`，放在 `tests/contrib/test_orchestration.py`；所有 runtime 选择、
  绑定、过期、凭据与 ban 场景都是该函数内有明确 case label 的子场景。配置版本合同的变化直接更新
  `tests/app/test_config.py` 中已有的测试。
- 所有 Task 按本文顺序串行实施。每完成一个 Task，运行该 Task 的 focused checks，发送业务
  diff 并停在 review；未经 review 不进入下一 Task。

## 1. 目标

一个 Agent 现在只能绑定一个 runtime：`AgentConfiguration.runtime` 是单值
（`app/config.py:76`），`AgentApplication` 在构造时建一个实例并终身持有
（`app/agent.py:129`），`SessionOrchestrator` 把它存成 `self._runtime`
（`core/orchestration/session.py:139`）并贯穿 start/stop、事件循环与所有 provider 调用。

本次改造让一个 Agent 配置多个 runtime，并在每次创建 runtime session 时按 round-robin 选一个：

- 一个 Agent 可以同时配置 `claudecode` 与 `codex`，也可以配置同一 kind 的多个实例来使用不同账号；
- 每个 runtime session 在创建时绑定一个 runtime，之后的 start / turn / steer / background 检查 /
  reconcile / stop 都走同一个实例；
- runtime 出错到需要向 Channel 回一条错误消息时，把它放进内存 ban 列表，后续会话轮转到别的
  runtime；
- 全部被 ban 时半开放行一个，让恢复能被探测到。

`RuntimeSession` 是纯内存状态（`core/orchestration/session.py:146` 的 `_runtime_sessions` 字典，
`IStorage` 只有 `save_runtime_attempt`，`RuntimeAttempt` 不含 runtime 名），所以绑定结果不落库，
本次改造不需要 schema migration。

绑定不可变的原因是 `reconcile_session` 走 `resume=True` + 已有的 `provider_thread_id`
（`contrib/claude/runtime.py:194`、`contrib/codex/runtime.py:287`），provider 线程是某一个 runtime
实例的本地状态，只能在同一个实例上恢复。

## 2. 实现范围

- 删除 agent 级 provider control 扩展点，让 registry 的 provider 组只剩 runtimes / channels /
  storages / audits；
- 配置升级到 version 3：`[[agent.runtime]]` 表数组，每个 runtime 有自己的 model / effort /
  sandbox_mode / network_access / env；
- `idle_timeout` 是 Agent 级设置，写在 `[[agent]]` 上，一个 Agent 的所有 runtime 共用同一个
  空闲阈值；
- `env_include` 数组换成 `env` 表，表达「子进程变量名 → 节点进程变量名」，同一个 key 就能让两个
  同 kind 的 runtime 拿到不同账号的凭据；
- 迁移链改成 payload→payload，`1 → 2 → 3` 依次套用后统一解析；
- `AgentApplication` 为每个 runtime 配置建独立实例与独立 `RuntimeCommandContext`，实例按配置
  顺序存成元组，下标即 key；
- `RuntimeSession` 记录所选下标，`SessionOrchestrator` 与 `SessionTurnCoordinator` 按它查实例，
  子进程环境也按它解析；
- runtime lifecycle 事件每个实例一个 task，`RuntimeExpire` 只影响发出事件的那个 runtime 的会话；
- 新增 `RuntimePool` 承载 round-robin、ban 与半开，并接入失败/成功上报与审计。

## 3. 配置合同（version 3）

### 3.1 TOML 形状

```toml
version = "3"

[node]
storage = "sqlite"
audit = "logging"

[[agent]]
id = "01a0420b-af5b-732c-9a95-b40df5e4bc17"
name = "arima"
idle_timeout = 600.0

[agent.channel]
kind = "lark"

[[agent.runtime]]
kind = "claudecode"
model = "opus"

[agent.runtime.env]
SSH_AUTH_SOCK = "SSH_AUTH_SOCK"
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY_WORK"

[[agent.runtime]]
kind = "claudecode"

[agent.runtime.env]
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY_PERSONAL"

[[agent.runtime]]
kind = "codex"

[agent.runtime.env]
CODEX_HOME = "BCN_CODEX_HOME_WORK"
```

`[agent.channel]` 与 `[[agent.runtime]]` 都跟在各自的 `[[agent]]` 元素后面，作用于最近一个数组元素。

配置里不出现 runtime 标识符：数组下标就是进程内的 key，数组顺序就是轮转顺序。

### 3.2 数据类

`RuntimeConfiguration` 的 `env_include: tuple[str, ...]` 换成
`env: Mapping[str, str]`，key 是子进程里的变量名，value 是节点进程里取值的变量名。
两边同名就写成 `SSH_AUTH_SOCK = "SSH_AUTH_SOCK"`；同 kind 多账号就让 key 相同、value 不同。
`__post_init__` 用现有的 `_ENVIRONMENT_NAME` 正则校验每个 key 与 value，复用
`_validate_environment_names` 的错误信息形状。

`AgentConfiguration` 的 `runtime: RuntimeConfiguration` 改为
`runtimes: tuple[RuntimeConfiguration, ...]`，`__post_init__` 要求它非空。
`idle_timeout_seconds: float` 从 `RuntimeConfiguration` 移到 `AgentConfiguration`，
非负有限数的校验一并搬过去，错误信息是 `agent.idle_timeout`。

`CONFIG_VERSION` 改为 `"3"`。

### 3.3 解析与序列化

`_parse_v3_configuration` 保留「不能定义 `node.channel` / `node.runtime`」的等价约束。
`_parse_v3_agent` 读取 agent 级的 `idle_timeout`（缺省 0，错误信息前缀 `agent #N.idle_timeout`），
并要求 `agent #N.runtime` 是 TOML 表数组，逐个交给
`_parse_runtime_configuration(runtime, kind, index=index, position=position)` 解析，错误信息前缀
写成 `agent #N.runtime #M.<key>`。

`_parse_runtime_configuration` 的 `standard_keys` 用 `"env"` 替换 `"env_include"`，并去掉
`"idle_timeout"`，其余非标准 key 继续落进 `options`，因此 adapter 侧读取选项的路径不变
（`contrib/claude/plugin.py:9-11`、`contrib/codex/plugin.py:9-11`）。`idle_timeout` 不做任何
特殊处理：它现在由 agent 级读取，写在 runtime 上就和其他未知 key 一样透传给 adapter。

`_serialize_configuration` 在 `[[agent]]` 段里输出 `idle_timeout`，再为每个 runtime 输出一段
`[[agent.runtime]]`；`env` 非空时按 key 排序输出为 `[agent.runtime.env]` 子表，且必须排在该
runtime 自己的标量 key 之后，否则那些 key 会落进 `env` 里。`_toml_value` 本来就有 Mapping
分支，key 复用 `_toml_key`。

### 3.4 迁移链

`load_node_configuration` 改成 payload 链，实现成配置等级状态机：每个 version 是一个
`_ConfigurationState`，状态上挂 `_ConfigurationUpgrade(apply, state)` 指向下一个状态，
终态的 `upgrade` 是 `None`，`is_current` 就是「没有 upgrade」。从文件里读到的 version 对应的状态
开始逐级 `advance()`，最后统一交给 `_parse_v3_configuration`，只有确实发生迁移时才
`_write_configuration`。

- `_v1_to_v2_payload` 承担现在 `_migrate_v1_configuration` 的全部逻辑，但产出 v2 payload dict
  而不是 `NodeConfiguration`：合并 `[node]` / `[runtime]` / `[runtime.env]` / `[channel.*]`，
  把 `runtime.env.include` 写成 v2 的 `env_include` 数组，补 telegram 的 `token_env` 与 wecom 的
  `secret_env` 默认值，并调用
  `_read_legacy_workspace_id(data_dir=resolve_data_dir(), database_name=...)` 取回 agent id；
- `_v2_to_v3_payload` 把每个 agent 的 `runtime` 表包成单元素表数组，把该表里的
  `idle_timeout` 提到 agent 级的 `idle_timeout`，把 `env_include = ["A", "B"]` 合并进
  `env = { A = "A", B = "B" }`（同名 key 以已有的 `env` 值为准），并把顶层 version 改成 `"3"`。

`load_control_configuration` 的版本白名单删掉，改为调用同一个 `_configuration_state()`，
支持的版本列表因此只存在一处。

### 3.5 CLI 入口

`--set` 增加一个 `agent.` scope，`--set agent.idle_timeout=600` 落到 `AgentConfiguration`；
`agent.` 下只读 `idle_timeout`，其余 key 直接忽略——`channel.` 与 `runtime.` 下的未知 key
也是原样带过去的，这里不额外拦。

`--set runtime.env` 是可重复的 `name=source` 对，不要求写 inline table：
`--set runtime.env=A=B --set runtime.env=C=D` 会累积成一张表。解析放在 `_agent_option` 里、
tomllib 兜底之前，因此值不会再被 TOML 解码。重复的 name 直接后者覆盖前者，不报错——手工编辑
`config.toml` 同样防不住，行为保持一致；同一张 `[agent.runtime.env]` 里的重名则由 tomllib
自己在 bcn 看到之前就拒绝。

`--set runtime.env_include=["A", "B"]` 继续接受，标记为 deprecated。重定向放在
`_agent_option`（`agent_management.py:200`）里：`runtime.env_include` 直接返回
`("runtime", "env", {"A": "A", "B": "B"})`，并向 stderr 打一条本地化提示，建议改用
`--set runtime.env`。数组内容的校验（非空文本、无重复）跟着搬到这里，用该函数已有的
`argparse.ArgumentTypeError`。

这样下游只看得见 `runtime.env` 一个 key：`add` 里 `env_include` 的分支整段删掉，
`runtime.env` 与 `runtime.env_include` 同时给出时也不需要新的判断——它们的 identity 都是
`("runtime", "env")`，一起并进同一张表，后写的名字覆盖先写的。

提示文案加 `cli.agent.env_include_deprecated` 到 `resources/locales/en.toml` 与 `zh-CN.toml`；
`cli.agent.set` 的帮助文案同步补上 `runtime.env` 的示例。

## 4. Runtime 实例化与子进程环境

### 4.1 registry

`AgentAdapterFactories.runtime: RuntimeFactory` 改为
`runtimes: Mapping[str, RuntimeFactory]`，key 是 runtime kind。
`load_agent(*, channel: str, runtimes: Sequence[str])` 对去重后的 kind 逐个 `_load`，
`app/application.py:150-153` 传 `runtimes=tuple(rc.kind for rc in configuration.runtimes)`。

### 4.2 AgentApplication

`AgentApplication` 为每个 `RuntimeConfiguration` 建一个 `RuntimeCommandContext` 与一个实例，
按配置顺序存成元组，下标即 key：

- `runtime_options` 按该配置独立组装（`options` 里的字符串项，加上 `model` / `effort`）；
- `sandbox_mode`、`network_access` 取该配置自己的值；
- `self.runtimes: tuple[IRuntime, ...]`，与 `configuration.runtimes` 同序等长；
- Agent 级的 `idle_timeout_seconds` 做一次 timer horizon 校验，换算成单个
  `runtime_idle_timeout_ms`。

子进程环境需要按下标取回 `RuntimeConfiguration`，直接用 `configuration.runtimes[index]`，
不另存映射。

### 4.3 子进程环境

`_runtime_environment(session)` 已经拿得到 `RuntimeSession`，用 `session.runtime_index` 取出对应的
`RuntimeConfiguration` 与 `IRuntime`，把下标传给 `_build_command_environment`，因此环境是按会话
所属 runtime 精确构造的，不需要在多个 runtime 之间取并集，A 的凭据不会进入 B 的子进程：

- 先按 `_PLATFORM_ENVIRONMENT` ∪ 该 runtime 的 `environment_variable_names()` 组装直通变量，
  这部分与现在一致；
- 再套用该配置的 `env`：对每个 `child_name -> source_name`，从 `os.environ[source_name]` 取值
  写入 `environment[child_name]`；`source_name` 不存在时报错，与现在 `env_include` 缺失变量的
  处理一致；
- `source_name` 与 `child_name` 不同时，把 `source_name` 从 `environment` 中移除，使凭据只以目标
  变量名进入子进程；
- capability token 的 `token_values` 扫描改用该配置 `env` 的 key，判定条件仍是 `TOKEN` 或以
  `_TOKEN` 结尾。

两个 adapter 的 `environment_variable_names()` 保持原样
（`contrib/claude/runtime.py:90-103`、`contrib/codex/runtime.py:84-90`）：`claudecode` 靠 `env`
给 `ANTHROPIC_API_KEY` 或 `CLAUDE_CONFIG_DIR` 绑不同来源，`codex` 靠 `env` 给 `CODEX_HOME` 绑
不同来源，adapter 一行都不用改。

## 5. Runtime 池与会话绑定

### 5.1 RuntimeSession 记录绑定

`core/models/entities.py` 的 `RuntimeSession` 增加 `runtime_index: int`。它是 4.2 里那个元组的
下标，和 `runtime`（adapter kind，用于审计 metadata 与 developer instructions）并存：前者用于
查实例，后者保持人可读。`RuntimeSession` 不落库，因此没有 schema 变化。

`_start_or_reconcile_runtime_session` 的返回值一致性检查（`session.py:1407-1413`）加上
`updated_runtime.runtime_index != runtime_session.runtime_index`。adapter 侧只用 `replace()` 改
`provider_thread_id`，新字段自动保留。

### 5.2 RuntimePool

新增 `core/orchestration/runtime_pool.py`：

```python
class RuntimePool:
    def __init__(
        self,
        runtimes: Sequence[IRuntime],
        *,
        clock: Callable[[], int],
    ) -> None: ...

    def all(self) -> tuple[IRuntime, ...]: ...
    def get(self, index: int) -> IRuntime: ...
    def select(self) -> int: ...
    def record_failure(self, index: int) -> None: ...
    def record_success(self, index: int) -> None: ...
```

- 构造时要求 `runtimes` 非空，且每个实例的 `name` 是非空文本（原
  `session.py:129-130` 的校验搬到这里）；
- ban 状态是内存 `dict[int, int]`，下标 → 解禁时间戳（毫秒）；
- `select()` 从游标位置开始按下标顺序找第一个 `ban_until <= now` 的项，游标随之前进一位；
  全部仍在 ban 中时，取 `ban_until` 最小的那个（最早被 ban 的），清除它的 ban 记录并返回它；
- `record_failure(index)` 写入 `ban_until = now + _BAN_MS`，`_BAN_MS` 是模块常量 `3_600_000`；
- `record_success(index)` 删除该下标的 ban 记录，让半开探测成功后立即恢复；
- 只配置一个 runtime 时，它被 ban 后的下一次 `select()` 立刻走半开分支放行自己。

`SessionOrchestrator` 在 `__init__` 里用自己的 `self._clock`（`session.py:125` 已有的
构造参数）构造 `RuntimePool`，并在 `record_failure` / `record_success` 造成状态变化时通过
`SessionAuditRecorder` 记录 `runtime.pool.banned` / `runtime.pool.released` 事件，metadata 带
下标、adapter kind 与 `ban_until_ms`。

### 5.3 SessionOrchestrator

`runtime: IRuntime` 改为 `runtimes: Sequence[IRuntime]`；`runtime_idle_timeout_ms` 仍是单个
`int`，因为空闲阈值是 Agent 级设置。

- `_create_runtime_session`（`session.py:294-313`）：
  `runtime_index = self._runtimes.select()`，`runtime = self._runtimes.get(runtime_index).name`；
- 其余按会话取实例：`_start_runtime_timer_if_idle` 的 `has_background_job`（`session.py:359`）、
  `_start_or_reconcile_runtime_session` 的 `start_session` / `reconcile_session`
  （`session.py:1378`、`1383`）、`_stop_runtime_session_locked` 的 `stop_session`
  （`session.py:1585`）全部改用 `self._runtimes.get(runtime_session.runtime_index)`；
- `start()`（`session.py:421-424`）依次 start 所有实例，失败时 stop 所有实例；
  `stop()`（`session.py:502`）对所有实例调用 stop，逐个记录 `runtime.stop` 失败。

### 5.4 失败与成功上报

判定标准是「这次 turn 是否要向 Channel 回一条错误消息」。这个条件已经收敛在
`RuntimeErrorReporter` 的 `_MESSAGE_KEYS`（`core/orchestration/error_feedback.py:22-27`）里，
只有 `RuntimeTurnState.FAILED` 与 `RuntimeTurnState.UNKNOWN` 会发消息。

上报点就放在唯一调用 `self._error_reporter.report(...)` 的地方（`session.py:704-710`）：
`result.state` 命中 `_MESSAGE_KEYS` 时对 `self._runtime_sessions[session_id].runtime_index` 调用
`record_failure`，`RuntimeTurnState.COMPLETED` 时调用 `record_success`，其余终态不上报；
会话此时已被移除则跳过。

`start_session` / `reconcile_session` 的 FAILED / UNKNOWN 不需要单独上报：它们会让本次 turn 以
`RuntimeSessionUnavailable`（`turn.py:345`）收敛成 FAILED turn，从同一个上报点进入 ban。

### 5.5 lifecycle 事件与过期

`_receive_runtime_event_loop` 改成 `_receive_runtime_event_loop(index, runtime)`，`start()`
为每个实例建一个 task，命名 `bcn-runtime-lifecycle-events-{agent_id}-{index}`；
`self._runtime_event_task` 改为 `list[asyncio.Task[None]]`，`stop()` 逐个 cancel 并等待。

`RuntimeExpire` 的扇出必须收窄。现在 `session.py:950-957` 把
`targets = tuple(self._runtime_sessions.values())` 全部过期，多 runtime 下会让 A 的 idle 过期打断 B
正在服务的会话。改为只选属于当前 runtime 的会话：

```python
targets = tuple(
    runtime_session
    for runtime_session in self._runtime_sessions.values()
    if runtime_session.runtime_index == index
)
```

`RuntimeBackgroundIdle` 分支的 source 查找同样加上 `runtime_session.runtime_index == index` 条件。

### 5.6 SessionTurnCoordinator

`SessionTurnCoordinator.__init__`（`core/orchestration/turn.py:172-183`）的 `runtime: IRuntime`
改为 `runtimes: RuntimePool`。`start_turn`（`turn.py:321`）与 `steer_turn`（`turn.py:471`）
改用 `self._runtimes.get(context.runtime_session.runtime_index)`；两处都从
`context.runtime_session` 拿会话，因此不需要额外传入会话字典。

## 6. 任务拆分

### Task 1：移除 agent 级 provider control 扩展点

`bazaar_compute_node.controls` 没有生产实现（主包 `pyproject.toml:25-38` 只声明 runtimes /
channels / storages / audits），唯一注册在 `tests/support/pyproject.toml:20-21`，且没有任何测试
调用过 `TestControl` 的 `inject` / `status`。删掉它之后，`load_agent` 不再需要拼
`{channel}+{runtime}+{storage}` 这个 key，多 runtime 下的 key 歧义随之消失。

生产侧删除：`app/registry.py` 的 `CONTROL_ENTRY_POINT_GROUP`、`ControlFactory`、
`AgentAdapterFactories.control`、`load_agent` 里的 control 查找、`_load_optional`
以及不再使用的 `storage` 参数与 `ControlHandler` import；`app/agent.py` 的
`_provider_control_handler`、`_handle_control`、`_adapter_context` 和传给 `CommandDispatcher` 的
`control_handler=`；`app/command.py` 的 `ControlHandler` 类型别名、`control_handler` 参数与
`kind == "control"` 分支；`app/resource_dispatch.py:188,194` 的参数透传；
`app/application.py:153` 的 `storage=` 实参。

保留：`app/application.py:342` 的**节点级** `kind == "control"`（`app/system_service.py:770`
用它做健康检查）、`app/agent.py:_validate_session_binding`（同时通过
`session_binding_validator=` 服务正常 command 路径，`app/agent.py:178`）、
`contrib/lark/transport.py:339-345` 与 `contrib/claude/client.py:211,324,352` 中同名但无关的方法。

测试侧删除：`tests/support/src/bcn_test_support/control.py`、`plugin.py` 的 `create_control`
及其导出、`tests/support/pyproject.toml` 的 `[project.entry-points."bazaar_compute_node.controls"]`
整节、`tests/app/test_registry.py:5-13` 的 `PROVIDER_GROUPS` 中 controls 一项；更新
`tests/contrib/test_codex.py:102`、`tests/contrib/test_orchestration.py:120`、
`tests/app/test_bcc_process.py:62`、`tests/e2e/test_claude_runtime.py:75` 中 `load_agent` stub 的签名。

checks：`uv sync`、`tests/app/test_registry.py`、`tests/app/test_daemon_process.py`、
`tests/app/test_composition.py`、`tests/app/test_transport.py`、`ruff format --check .`、
`ruff check .`、`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 2：配置 version 3

按第 3 节实现 `RuntimeConfiguration.env`、`AgentConfiguration.runtimes` 与
`AgentConfiguration.idle_timeout_seconds`、`CONFIG_VERSION = "3"`、
`_parse_v3_configuration` / `_parse_v3_agent`、`_serialize_configuration` 的 `[[agent]]`
`idle_timeout` 与 `[[agent.runtime]]` / `[agent.runtime.env]` 输出、配置等级状态机形式的
payload 迁移链，以及改用同一个状态查找的 `load_control_configuration`。

`app/agent_management.py` 的 `add` 用单元素 `runtimes=(rc,)` 构造，并按 3.5 节处理
`--set runtime.env` 与 deprecated 的 `--set runtime.env_include`
（`agent_management.py:125-131,152`）；
`add` 与 `list` 的输出把 `runtime=` 改成按配置顺序用逗号连接的 kind 列表
（`agent_management.py:87`、`agent_management.py:164-165`）。

本 Task 里 `app/agent.py:129` 与 `app/application.py:152` 暂时取 `configuration.runtimes[0]`，
运行时行为不变。

测试：更新 `tests/app/test_config.py` 中的版本、解析与 round-trip 契约，覆盖 v1→v3 连续迁移、
v2→v3 迁移（含 `env_include` 数组转 `env` 表）、v3 原样解析、序列化 round-trip、空
`[[agent.runtime]]`、`env` 的名称校验与 `[agent.runtime.env]` 子表序列化；在
`tests/test_cli.py` 覆盖 `--set agent.idle_timeout`、可重复的 `--set runtime.env=name=source`、
`--set runtime.env_include` 的转换与 deprecation 提示，以及两者同时给出时后者覆盖前者。

checks：`tests/app/test_config.py`、`tests/app/test_composition.py`、`tests/test_cli.py`、
`ruff format --check .`、`ruff check .`、`uv run scripts/pyright_lsp_check.py --outputjson .`、
`git diff --check`。

### Task 3：多实例构造与子进程环境

按第 4 节实现 registry 的 `runtimes` 工厂映射、`AgentApplication` 的多实例构造、
per-runtime `RuntimeCommandContext`、按会话解析的子进程环境与 `env` 的取值/改名/剥离。

`SessionOrchestrator` 本 Task 把 `runtime` 换成 `runtimes` 序列，内部先固定使用下标 0。

测试：新增 `test_multi_runtime_agents`，本 Task 覆盖两个同 kind、`env` 指向不同来源变量的
runtime 各自拿到正确的凭据、来源变量名不出现在子进程环境、同名直通项照常传入、
`environment_variable_names()` 按 runtime 分别生效。

checks：`tests/contrib/test_orchestration.py`、`tests/app/test_composition.py`、
`tests/contrib/test_claude.py`、`tests/contrib/test_codex.py`、`ruff format --check .`、
`ruff check .`、`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 4：会话与 runtime 绑定

按第 5.1、5.3、5.5、5.6 节实现：`RuntimeSession.runtime_index`、`_create_runtime_session` 选定
runtime、所有 provider 调用按下标查实例、
start/stop 覆盖所有实例、lifecycle 事件每实例一个 task、`RuntimeExpire` 与
`RuntimeBackgroundIdle` 收窄到发出事件的 runtime、`SessionTurnCoordinator` 按会话取实例。

本 Task 的 `RuntimePool` 只提供 `all` / `get` / `select`，`select()` 返回下一个下标。

测试：`test_multi_runtime_agents` 是 NodeApplication 层的构造与子进程环境测试，会话绑定要用
orchestrator 层的 `queue_turn_plan` / `emit_expire` / TimerWheel，因此在
`tests/contrib/test_orchestration.py` 新增 `make_multi_runtime_node` helper 与两个同级测试
`test_multi_runtime_session_binding`、`test_multi_runtime_expiry_is_scoped_to_its_runtime`，覆盖
两个 runtime 各自建立会话、同一会话的 start / turn / steer / stop 都落在同一个实例上、
runtime A 的 `RuntimeExpire` 不影响 runtime B 的会话。

checks：`tests/contrib/test_orchestration.py`、`tests/core/`、`tests/app/test_composition.py`、
`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 5：round-robin 与 ban

按第 5.2、5.4 节补全 `RuntimePool` 的 ban 状态、半开与 `record_failure` / `record_success`，
在 `session.py:704-710` 的错误反馈点按 `_MESSAGE_KEYS` 命中与否上报，并记录
`runtime.pool.banned` / `runtime.pool.released` 审计事件。

测试：扩展 `test_multi_runtime_agents`，覆盖连续建会话时的轮转顺序、turn 以 FAILED 收敛并向
Channel 发出错误消息后该 runtime 被跳过、UNKNOWN 同样被跳过、start 失败经
`RuntimeSessionUnavailable` 收敛成 FAILED 后被跳过、COMPLETED 后解除 ban、全部被 ban 时只放行
最早被 ban 的一个、半开成功后立即恢复参与轮转、ban 到期后自动恢复、只配置一个 runtime 时
它始终可被选中。

checks：`tests/contrib/test_orchestration.py`、`tests/core/test_audit.py`、
`tests/core/test_error_feedback.py`、`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 6：最终验收

- 运行完整非 e2e pytest suite；
- 运行 `ruff format --check .`、`ruff check .`、
  `uv run scripts/pyright_lsp_check.py --outputjson .`、
  `uv run python -m compileall -q src tests`、`uv lock --check`、`git diff --check`；
- 用测试专用配置、临时数据库与隔离进程跑一次 v2→v3 升级后的启动，确认多 runtime 配置能起来、
  两个 runtime 各自的凭据生效、`bcn agent list` 输出正确；
- 汇总最终业务 diff 与测试结果，停在最终 review。

## 7. 验收标准

1. `bazaar_compute_node.controls` 在生产与 test-support 中都不存在，节点级 health / shutdown
   与 `session_binding_validator` 路径保持绿。
2. v1 与 v2 配置能连续迁移到 v3 并原地写回，`env_include` 数组变成同名映射的 `env` 表；
   v3 配置解析与序列化 round-trip 稳定。
3. `bcn agent add --set runtime.env_include=[...]` 仍然可用，写出来的是 `env` 表，并提示改用
   `--set runtime.env`；`--set agent.idle_timeout` 写到 `[[agent]]` 上。
4. 一个 Agent 能同时运行 `claudecode` 与 `codex`，也能运行两个 `claudecode` 实例并分别使用
   `env` 指定来源的账号。
5. `env` 中来源变量名与子进程变量名不同时，来源名不出现在子进程环境中，子进程变量名拿到
   正确的值。
6. 每个 runtime session 的 start / turn / steer / background 检查 / reconcile / stop 都落在
   `RuntimeSession.runtime_index` 指向的同一个实例上。
7. runtime A 发出的 `RuntimeExpire` 只过期 A 的会话，B 的会话继续服务。
8. 连续创建会话时按配置顺序轮转；turn 终态触发 Channel 错误消息时该 runtime 在 `_BAN_MS`
   内被跳过；全部被 ban 时每次只放行最早被 ban 的一个，成功后立刻恢复参与轮转。
9. 只配置一个 runtime 的 Agent 在该 runtime 失败后仍能继续选中它。
10. `runtime.pool.banned` / `runtime.pool.released` 出现在审计流中，metadata 含下标、
    adapter kind 与 `ban_until_ms`。
11. full pytest、Ruff、Pyright、compileall、lock 与 diff gates 全部通过。
