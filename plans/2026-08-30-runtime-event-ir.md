# 运行时事件的中立表达与渠道过程投影

## 1. 现状

Core 有两套并列的运行时事件模型，合成 `type RuntimeStreamItem = RuntimeEvent | StreamEvent`
（`core/runtime.py:32`），一起经 `accept_turn_event` 交给 channel。

`RuntimeEvent`（`core/models/entities.py:482`）表达 turn 级状态：`created_at_ms`、`event_name`、
`state`、`turn_id`、`error_kind`、`error_message`、`metadata`。它会入库，是审计的一部分。
`RuntimeEventState`（`core/models/states.py:232`）有五个取值，`UNKNOWN` 是其中之一，
`turn.py:62`、`:568`、`:613` 与 `command.py:365` 都依赖它做终态判定。`provider_turn_id` 经
`metadata` 传递，`turn.py:599-627` 用它做 turn 关联并写回 `RuntimeTurn.provider_turn_id`。
Claude 的 `usage`、`total_cost_usd`、`stop_reason` 等同样经终态事件的 `metadata` 上报
（`contrib/claude/events.py:436-448`）。

`StreamEvent`（`core/models/entities.py:473`）表达过程，字段只有 `kind`、`created_at_ms`、
`session_id`、`stream_id`、`content: str | None`。`StreamEventKind`（`core/models/states.py:240`）
有十个成员：`AGENT_MESSAGE_DELTA`、`PLAN_DELTA`、`REASONING_SUMMARY_DELTA`、
`REASONING_TEXT_DELTA`、`COMMAND_OUTPUT_DELTA`、`COMMAND_INTERACTION`、`FILE_CHANGE_UPDATE`、
`TOOL_PROGRESS`、`ITEM_PROGRESS`、`TURN_PROGRESS`。

分类维度已经够用，缺的是载荷。工具名、输入、输出、成功与失败只能挤进 `content` 这一个可选字符串。

`turn.py:405-430` 的循环把两类事件都转给 `accept_turn_event`：`RuntimeEvent` 先经
`_apply_runtime_event` 再转发，`StreamEvent` 直接转发；两处都用 `except Exception` 包住并只记
日志，channel 失败不影响 turn 主链路。终态事件同样会转发，Lark 依赖它清理路由与 typing 状态。

两个 runtime adapter 都丢掉了工具调用的开始。

- Codex：`contrib/codex/events.py:213-219` 对 `item/started` 与 `item/completed` 返回 `None`。
  以下取自 app-server 协议 schema（`codex-rs/app-server-protocol/schema/json/`）：

  `ItemStartedNotification` 的参数是 `item`、`startedAtMs`、`threadId`、`turnId`，
  `ItemCompletedNotification` 是 `item`、`completedAtMs`、`threadId`、`turnId`。item 的 ID 位于
  `item.id`，类型位于 `item.type`；`itemId` 是各 delta 类通知的字段，不是生命周期通知的。
  `turnId` 是 provider turn 的来源，`startedAtMs` 与 `completedAtMs` 是事件自身的时间戳。

  `ThreadItem` 共有 19 个变体：`UserMessage`、`HookPrompt`、`AgentMessage`、`FunctionCallOutput`、
  `Plan`、`Reasoning`、`CommandExecution`、`FileChange`、`McpToolCall`、`DynamicToolCall`、
  `CollabAgentToolCall`、`SubAgentActivity`、`WebSearch`、`ImageView`、`Sleep`、`ImageGeneration`、
  `EnteredReviewMode`、`ExitedReviewMode`、`ContextCompaction`。生命周期通知对全部 19 种都会发出。
  字段名为 camelCase：`CommandExecution` 是 `command`、`cwd`、`exitCode`、`aggregatedOutput`、
  `status`、`durationMs`；`FileChange` 是 `changes`、`status`；`McpToolCall` 是 `server`、`tool`、
  `arguments`、`result`、`error`、`status`；`DynamicToolCall` 是 `tool`、`arguments`、
  `contentItems`、`status`、`success`；`WebSearch` 是 `query`、`action`、`results`；
  `SubAgentActivity` 是 `agentPath`、`agentThreadId`、`kind`。

  `thread/tokenUsage/updated` 在 turn 进行中多次上报，`tokenUsage.last` 与 `tokenUsage.total`
  皆为必填的 `TokenUsageBreakdown`（`inputTokens`、`cachedInputTokens`、`cacheWriteInputTokens`、
  `outputTokens`、`reasoningOutputTokens`、`totalTokens`），`modelContextWindow` 可为空。该
  method 当前未被处理，事件被静默丢弃。

  `_STREAM_EVENT_KINDS`（`events.py:23-32`）映射九个 delta 类方法，其余 `item/` 方法落到
  `ITEM_PROGRESS`。其中 `turn/progress` 已不在当前协议的通知列表中，`events.py:221` 对它的分支是
  失效代码。
- Claude：`tool_result` 的主路径在 `kind == "user"` 分支（`contrib/claude/events.py:144-168`），
  `_map_assistant`（`:337-356`）另有一条同名分支。结果块只携带 `tool_use_id`、`content`、
  `is_error`，不含工具名。工具名与入参只出现在 assistant 消息的 `tool_use` 块上，该块当前未被
  读取。关联键已经存在：`events.py:153` 把 `stream_id` 设为 `tool_use_id`。

Lark channel 对过程事件几乎不消费。`contrib/lark/channel.py:854` 用第一条 `StreamEvent` 排一次
Typing reaction（`_TypingState`、`_typing_queue`、`_typing_runner`），终态 `RuntimeEvent` 清理
本地跟踪。审批通过或拒绝后，`contrib/lark/approval.py:313-340` 的 `_send_approval_feedback` 另发
一条 `approval.feedback.approved` 或 `approval.feedback.rejected` 回复；卡片自身的状态更新走
`approval.card.status.approved`（`:473`）。

`contrib/lark/api.py` 调用的写卡接口只有 `/open-apis/interactive/v1/card/update`，即回调场景下凭
token 更新卡片。

WeCom channel 的 `request_approval`（`contrib/wecom/channel.py:780-786`）不询问用户，直接返回
`ApprovalDecision.APPROVED`，该渠道上的每一次审批都被静默放行。`_receive_message`
（`channel.py:937-948`）收到 `aibot_event_callback` 时只计数并返回，全部事件被丢弃。

企业微信智能机器人在长连接模式下按触发场景区分回复命令：收到进入会话事件用
`aibot_respond_welcome_msg`，收到消息回调用 `aibot_respond_msg`，**收到模板卡片事件用
`aibot_respond_update_msg` 更新模板卡片**，三者都要透传回调中的 `headers.req_id`。更新命令的
`body.response_type` 固定为 `update_template_card`，`body.template_card` 的 `task_id` 需与回调
收到的一致，且**收到事件回调后需在 5 秒内发送回复，超时将无法更新**。模板卡片有五种，交互型的是
`button_interaction`、`vote_interaction` 与 `multiple_interaction`。

仓库中没有可复用的出站脱敏实现；`core/audit.py` 与 `core/orchestration/services.py` 已各自复制同一
套敏感键名，provider 错误消息里另有基于已知 secret 的精确替换，后者与启发式 token 识别不是同一
机制。

飞书卡片侧的既有约束：单张卡片最多 200 个元素或组件，体积上限 30KB；卡片级与组件级 OpenAPI 对
单个卡片实体的操作频率上限为 10 次/秒，`POST /open-apis/cardkit/v1/cards/:card_id/elements` 的
应用级频率上限为 1000 次/分钟、50 次/秒；该接口的 `uuid` 字段是幂等 ID，`sequence` 字段必填且要求
相对上一次操作严格递增，`element_id` 由调用方指定，长度上限 20 字符。创建卡片实体不会让卡片出现
在会话里，需要再调用发送消息接口按 card_id 发送。

`pyrightconfig.json` 当前是 `typeCheckingMode: basic`，未开启 `reportMatchNotExhaustive`。未开启
时，声明了非 `None` 返回值的函数漏掉联合分支只会以 `reportReturnType` 间接报错，返回 `None` 的纯
副作用消费者漏掉分支则没有任何诊断；channel 消费事件正是返回 `None` 的形状。

## 2. 实现范围

- 用一个带载荷的判别联合替换 `StreamEvent` 与 `StreamEventKind`，并与 `RuntimeEvent` 的 turn 级
  职责合并为单一模型；现有的五个终态语义、`provider_turn_id` 与终态 `metadata` 原样保留，它们是
  错误上报与 runtime 切换的判定依据；
- Core 模型保持 provider 中立，承载工具调用与上下文压缩等 runtime 真实上报的活动事件；各 runtime
  只产出公开协议中实际存在的阶段，不要求 started 与 completed 成对；
- 两个 adapter 补齐工具调用的开始、结束与失败，并保留各自原生给出的 delta 类别；
- Lark channel 新增 CardKit 客户端与投影层，把工具调用事件按 turn 投影到一张过程卡，过程卡取代
  Typing reaction 与审批结果的额外回复；
- Telegram 新增消息编辑能力，审批结果在原消息上就地更新，活动经每 turn 一条可编辑的过程消息
  呈现；
- WeCom 以模板卡片实现审批，取代当前的无条件放行；
- `pyrightconfig.json` 开启 `reportMatchNotExhaustive`，使 CI 直接拦截未覆盖的变体；
- 事件为纯内存态，投影为 best-effort。

## 3. Core 事件模型

新增 `core/models/events.py`。`entities.py` 中的 `StreamEvent` 与 `RuntimeEvent`、`states.py`
中的 `StreamEventKind` 由新模型取代，`RuntimeEventState` 保留。

公共上下文单独成型：

```python
@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    session_id: str
    runtime_session_id: str
    turn_id: str | None
    provider_turn_id: str | None
    occurred_at_ms: int
```

`provider_turn_id` 从 `metadata` 提升为一等字段，它是 turn 关联的依据而非附带信息。

envelope 与 payload 是外层包裹关系而非继承关系：`frozen=True, slots=True` 与继承并存会产生重复
slot，且公共基类会把消费方引向 `isinstance` 判断，绕开模式匹配的收窄与穷尽性。

工具调用是两个 provider 都真实拥有的语义，作为主模型。`input` 与 `output` 限定为 JSON 值：

```python
type JsonValue = (
    str | int | float | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    parent_call_id: str | None = None
    input: JsonValue = None
    output: JsonValue = None
```

`ToolCall` 只承载所有 runtime 都能投影的调用身份、展示名与可选 JSON 入出参，不把 provider 的
原始 envelope 或 item 整体带入 Core。工具成功与失败由 `ToolCallCompleted` 和 `ToolCallFailed`
两个 payload 变体表达，不在 `ToolCall` 内重复保存 `is_error`；adapter 只用 provider 的状态字段选择
变体。Core 也不定义 command、file、web search 或 agent task 等 `detail` 分类，避免按某个 provider
的工具集合建立第二套联合。

中立指的是变体的语义如何定义，不是要求每个 runtime 都能产出每个变体。**一个能力只要能被中立地
表达，就可以进入联合，即使当前只有一个 runtime 产出它；不具备该概念的 runtime 不发这个变体即可。**
后续接入的 runtime 不会与现有两个一一对应，联合按需增长，`reportMatchNotExhaustive` 保证新增变体
时所有消费点被强制更新。判断一个能力该不该进 Core 的标准是它能否脱离某个 provider 的协议独立
定义，而不是它在几个 provider 上出现过。

delta 同样保留 provider 原生给出的类别，不并成一个字符串：

载荷形状相同的合成一个，用 `kind` 区分；形状不同的各自成变体。以下三种在协议里确实只是一段文本
（`item/commandExecution/outputDelta` 与 `item/fileChange/outputDelta` 的 `delta`、
`item/mcpToolCall/progress` 的 `message`），合成一个：

```python
@dataclass(frozen=True, slots=True)
class ToolCallTextDelta:
    call_id: str
    kind: ToolCallDeltaKind  # input | output | progress
    text: str
```

另外两种携带的不是文本，各自成变体，否则又会把结构压回字符串：

```python
@dataclass(frozen=True, slots=True)
class FileChangeEntry:
    path: str
    kind: str
    patch: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallPatchUpdated:
    call_id: str
    changes: tuple[FileChangeEntry, ...]


@dataclass(frozen=True, slots=True)
class ToolCallInteraction:
    call_id: str
    stdin: str
    process_id: str | None = None
```

`item/fileChange/patchUpdated` 的 `changes` 是 `FileUpdateChange[]`；
`item/commandExecution/terminalInteraction` 带 `stdin` 与 `processId` 两个字段。

载荷本身是判别联合，每个变体是一个 frozen dataclass，变体类即 tag。turn 级变体保留
`event_name`、`error_kind`、`error_message` 与 `metadata`，与现有入库字段一一对应：

```python
@dataclass(frozen=True, slots=True)
class TurnStarted:
    event_name: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    event_name: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnFailed:
    event_name: str
    error_kind: str
    error_message: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    event_name: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnUnknown:
    event_name: str
    error_kind: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContentDelta:
    kind: ContentDeltaKind  # agent_message | plan | reasoning_text | reasoning_summary
    text: str
    index: int | None = None


@dataclass(frozen=True, slots=True)
class ContextCompactionStarted:
    compaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextCompactionCompleted:
    compaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolCallFailed:
    call: ToolCall
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    total: TokenUsage
    last: TokenUsage | None = None
    model_context_window: int | None = None
    cost_usd: float | None = None


RuntimeEventPayload = (
    TurnStarted
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
    | TurnUnknown
    | ContentDelta
    | ContextCompactionStarted
    | ContextCompactionCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | ToolCallFailed
    | ToolCallTextDelta
    | ToolCallPatchUpdated
    | ToolCallInteraction
    | UsageUpdated
)


@dataclass(frozen=True, slots=True)
class RuntimeOutputEvent:
    envelope: RuntimeEventEnvelope
    payload: RuntimeEventPayload
```

`TurnUnknown` 对应 `RuntimeEventState.UNKNOWN`。两个 adapter 在启动失败、传输错误、协议错误、
会话重置与无法识别的状态下都产出该状态，`turn.py:62`、`:613` 与 `command.py:365` 依赖它，
runtime 是否切换也由此判定。

`UsageUpdated` 承载 token 消耗。Codex 的 `thread/tokenUsage/updated` 在 turn 进行中多次上报，
因此它是一个流式事件而非终态附属；`last` 对应本次增量，`total` 对应累计。Claude 只在终态给出一次，
`last` 留空。

`metadata` 只出现在 turn 级变体上，承接现有终态携带的 `usage`、`total_cost_usd`、`stop_reason`
等字段，入库与审计的内容与迁移前逐字段一致；`UsageUpdated` 是给消费方的中立信号，两者并存，各自
服务不同的生命周期。工具调用变体不带 `metadata`，channel 无法借它依赖 provider 协议。

`ContentDeltaKind` 是 `StrEnum`，四个取值的载荷形状一致。判别字段命名为 `kind`，与
`ToolCallTextDelta` 一致；`channel` 在 bcn 里已经指 Telegram、Lark、WeCom，不用于此处。
reasoning 分为 `reasoning_text` 与 `reasoning_summary` 两个取值，与现有 `REASONING_TEXT_DELTA`
和 `REASONING_SUMMARY_DELTA` 一一对应；合并成单一 `reasoning` 会丢掉正文与摘要的区别。`index` 承接
`item/reasoning/textDelta` 的 `contentIndex` 与 `item/reasoning/summaryTextDelta` 的
`summaryIndex`，用于区分同一 turn 内的多个 reasoning 块；其余取值留空。

判别联合与枚举字段的取舍以载荷为准：tag 不同但字段与不变量完全一致时用一个 dataclass 加枚举，
tag 决定了不同的合法字段时拆成各自的变体，让类型检查器把 tag 与载荷绑定。

`pyrightconfig.json` 增加 `"reportMatchNotExhaustive": "error"`，未覆盖全部变体的 `match` 在 CI
阶段失败，诊断中指名未处理的变体。消费 `RuntimeEventPayload` 的 `match` 不写兜底分支，兜底会让
检查器认为已穷尽。

turn 级变体既走既有入库与 reducer 路径，也继续转发给 channel，与现状一致。

上下文压缩变体是给 channel 的即时活动事实，不进入 turn 持久化、audit 或
`SessionRuntimeStateMachine`。`ContextCompactionCompleted` 可以独立出现；`compaction_id` 仅用于
provider 能提供时关联同一行，不承担配对不变量。`states.py` 中从未被 adapter 驱动、且所有消费者均
与 `WORKING` 等同处理的 `SessionRuntimeState.COMPACTION_*` 与
`SessionRuntimeSignal.COMPACTION_*` 删除，`turn.py` 中按 `event_name` 字符串猜测压缩阶段的旧入口
一并删除。

### 3.1 现有事件语义的迁移

重构删除的是旧模型的形状，不是它承载的事实。现有十个 `StreamEventKind` 与 turn 级
`RuntimeEvent` 的每一条活语义都有对应落点：

| 现有语义 | 新载荷 |
| --- | --- |
| `AGENT_MESSAGE_DELTA` | `ContentDelta(kind=agent_message)` |
| `PLAN_DELTA` | `ContentDelta(kind=plan)` |
| `REASONING_TEXT_DELTA` | `ContentDelta(kind=reasoning_text)` |
| `REASONING_SUMMARY_DELTA` | `ContentDelta(kind=reasoning_summary)` |
| `COMMAND_OUTPUT_DELTA` | `ToolCallTextDelta(kind=output)` |
| `COMMAND_INTERACTION` | `ToolCallInteraction(stdin, process_id)` |
| `FILE_CHANGE_UPDATE` | `ToolCallTextDelta(kind=output)` 或 `ToolCallPatchUpdated` |
| `TOOL_PROGRESS` | `ToolCallTextDelta(kind=progress)`，或工具生命周期变体 |
| `ITEM_PROGRESS` | 取消 |
| `TURN_PROGRESS` | 取消 |
| `RuntimeEventState` 五态 | `TurnStarted` / `TurnCompleted` / `TurnFailed` / `TurnCancelled` / `TurnUnknown` |
| `event_name`、`error_kind`、`error_message`、`metadata` | turn 级变体的同名字段 |
| `metadata["provider_turn_id"]` | `RuntimeEventEnvelope.provider_turn_id` |

`ITEM_PROGRESS` 是旧模型载荷不足时的兜底：凡是落到它的方法，新 adapter 都已有明确的变体可产出，
因此它不再需要。`TURN_PROGRESS` 对应的 `turn/progress` 已不在 Codex 当前协议中，Claude 侧该分支
只携带空内容，两者都没有可表达的事实。取消这两项之外，其余语义逐条保留。

## 4. runtime adapter 的归一化

### Codex

`contrib/codex/events.py` 处理 `item/started` 与 `item/completed`。通知参数是 `item`、
`threadId`、`turnId` 与 `startedAtMs` 或 `completedAtMs`：`call_id` 取 `item.id`，item 类型取
`item.type`，`turnId` 落到 `RuntimeEventEnvelope.provider_turn_id`，时间戳落到 `occurred_at_ms`。

生命周期通知对全部 19 个 `ThreadItem` 变体都会发出。adapter 不按每个内置工具的完整 schema 建立
分支，只做一层小而有损的通用投影：

- 所有调用只要求 `id` 与 `type`。`name` 优先取有效的 `server/tool`、`tool`、`name`，其次在
  `command` 存在时取其首段；都不存在时把稳定的 `type` 判别值通过一张只负责展示名的小表翻译为
  `command`、`file_change`、`mcp_tool`、`dynamic_tool`、`agent`、`web_search`、`image_view`、
  `image_generation`、`sleep` 或 `function`，未收录的类型回退为 `tool`。该表不读取各变体内部的
  result/detail 字段；Codex 改变工具细节时只会降级展示；
- `input` 只从 `arguments`、`input`、`command` 中取第一个存在的值，`output` 只从 `result`、
  `output`、`contentItems` 中取第一个存在的值。选中的值在 JSONL 边界通过严格 `JsonValue` 校验后
  才进入 Core；不携带整个原始 item，也不为 command/file/image 等逐类猜测其他字段；
- `status` 与 `error` 都是可选字段而非全部 `ThreadItem` 的公共字段。`status` 为失败态或 `error`
  存在时产出 `ToolCallFailed`，否则完成通知产出 `ToolCallCompleted`。没有失败信号的 item 不制造
  `is_error`；失败事实只由 payload 变体表达；
- 开始通知只把选出的 `input` 放入 `ToolCall`，完成或失败通知只把选出的 `output` 放入 `ToolCall`。

内容类 item —— `UserMessage`、`HookPrompt`、`AgentMessage`、`Plan`、`Reasoning` —— 的正文已经由
各自的 delta 方法产出 `ContentDelta`，其生命周期通知不再另产事件。

线程状态类 item 中，`EnteredReviewMode` 与 `ExitedReviewMode` 继续显式跳过；
`ContextCompaction` 的 `item/started` 与 `item/completed` 分别映射到中立的
`ContextCompactionStarted` 与 `ContextCompactionCompleted`，`item.id` 落到 `compaction_id`。

其余方法：

- delta 类按原生载荷映射，`call_id` 取 `itemId`：`item/agentMessage/delta` 与 `item/plan/delta`
  进 `ContentDelta`；`item/reasoning/textDelta` 与 `item/reasoning/summaryTextDelta` 进
  `ContentDelta` 并把 `contentIndex` 或 `summaryIndex` 落到 `index`；
  任意 `item/*/outputDelta` 的 `delta` 与 `item/*/progress` 的 `message` 进
  `ToolCallTextDelta` 的对应 `kind`，不按内置工具类型分支；
- `thread/tokenUsage/updated` → `UsageUpdated`，`tokenUsage.last` 与 `tokenUsage.total` 分别落到
  `last` 与 `total`，`modelContextWindow` 落到 `model_context_window`；
- `item/autoApprovalReview/*` 不映射，审批走既有的反向控制路径，不并入展示事件；
- `events.py:221` 中针对 `turn/progress` 的分支随之删除，该方法已不在当前协议中。

### Claude

工具调用的开始与结束来自两条不同的 envelope，`contrib/claude/events.py` 需要同时处理：

- assistant 消息的 `tool_use` 块 → `ToolCallStarted`，`call_id` 取 `id`，`name` 取 `name`，
  `input` 取 `input`。这是 `name` 与 `input` 的唯一来源，也是 started 的唯一来源；
  `content_block_start` 不再另行产出 started；
- `kind == "user"` 分支的 `tool_result` 块 → `ToolCallCompleted`，`call_id` 取 `tool_use_id`，
  `output` 取 `content`；块上 `is_error` 为真时改为 `ToolCallFailed`。`_map_assistant` 中的同名
  分支一并收敛到这一条路径；
- 结果块不含工具名，因此 `TurnEventStream` 维护一个 `call_id` 到 `name` 的映射，在产出 started
  时写入、在产出 completed 或 failed 时读出并构造完整的 `ToolCall`，turn 结束时清空。映射中没有
  对应项时 `name` 取 `tool_use_id`，使投影仍能成行；
- 一条 envelope 可能含多个工具块，因此 `TurnEventStream` 内部维护一个待发队列：解析一条 envelope
  产出的全部事件先入队，`__anext__` 逐个取出，队列空时才读下一条 envelope；
- 工具入参的 JSON 增量进 `ToolCallTextDelta(kind=input)`，`call_id` 取对应的 `tool_use_id`；
- `system` 子类型 `tool_progress`、`task_notification`、`task_updated` 映射到
  `ToolCallTextDelta(kind=progress)`，`call_id` 取 `task_id`；`task_started` 产出名为 `task` 的
  `ToolCallStarted`，可用摘要进入 `input`；
- Claude Code 2.1.247 的公开 stream-json 只提供压缩完成后的 `system/compact_boundary`，映射为可
  独立到达的 `ContextCompactionCompleted`；开始与进行中阶段属于 internal `compact_progress`，不
  从内部接口推导；
- 终态 result 的 `usage` 与 `total_cost_usd` 另外产出一条 `UsageUpdated`，`total` 取自 `usage`，
  `cost_usd` 取自 `total_cost_usd`，`last` 留空；`metadata` 中的这些字段保持原样，入库内容不变。

## 5. 飞书投影

长连接 transport 的 reader 只负责解帧、路由与安排协议应答，不内联等待 WebSocket 回写完成。
ACK 由当前 connection 持有的受管 task 发送；连接关闭、重连或 channel stop 时取消并收割旧连接的
发送 task。`aiohttp` 已保证单个 WebSocket frame 的完整写入，删除 transport 外层 `_send_lock`，
它不承担去重职责。`ws_connect` 同时启用 transport heartbeat；这与现有应用层 ping 分工明确：前者
负责发现半开连接并结束悬挂 I/O，后者继续交换飞书下发的 `ClientConfig`。ACK 不增加应用层 deadline。

### 5.1 API 客户端

`contrib/lark/api.py` 新增 CardKit 方法，沿用既有 `_post_json` 与 tenant access token 机制：

- `create_card(card, *, timeout) -> str`，`POST /open-apis/cardkit/v1/cards`；
- `add_card_elements(card_id, elements, *, uuid, sequence, timeout)`，
  `POST /open-apis/cardkit/v1/cards/:card_id/elements`；
- `update_card_element(card_id, element_id, element, *, uuid, sequence, timeout)`，
  `PUT /open-apis/cardkit/v1/cards/:card_id/elements/:element_id`。

创建卡片实体只得到 `card_id`，卡片尚未出现在会话中。投影层随即用既有的 `reply_message` 以
interactive 类型按 `card_id` 引用发送，回复目标是触发该 turn 的消息，`reply_in_thread` 沿用该
会话既有的话题语义；返回的 provider message id 存入 `CardState`，供后续诊断与降级提示使用。

`/open-apis/interactive/v1/card/update` 继续服务审批卡片的回调场景。

### 5.2 投影状态

新增 `contrib/lark/activity.py`，持有纯内存的投影状态，生命周期与进程一致。

每个 turn 一张过程卡，session 只保留到当前 turn 的路由索引。容量决定了这个边界：一次工具调用占
一行，扣除固定组件预算后一张卡至多容纳约 180 行，而 bcn 的 session 跨轮持久。

过程卡在该 turn 的第一条 `ToolCallStarted` 到达时才创建，没有工具调用的 turn 不产生卡片。

`CardState` 持有 `card_id`、provider message id、`next_sequence`、`last_success_sequence`、
已用组件数与已用字节数。行引用的键是 `(turn_id, call_id)`，值是 `(card_id, element_id, state)`。

- `element_id` 由投影层生成短 ID，形如 `i000017`。飞书对 `element_id` 的长度上限是 20 字符，而
  Claude 的 `tool_use_id` 形如 `toolu_01...` 已经超过，因此该映射是必需的；
- `ToolCallStarted` 命中已存在的 `call_id` 时更新该行；
- `ToolCallCompleted` 与 `ToolCallFailed` 在未见过对应 `ToolCallStarted` 时用完成事件补建一行；
- 各类 delta 只用于刷新该行的摘要，不新增行。

### 5.3 幂等与时序

- 每张卡一个 single-writer 队列，严格串行发送。`sequence` 的作用是拒绝迟到的旧操作：若分配了 10
  与 11 而 11 先到达，10 会被拒绝且无法补发，因此并发请求的顺序由队列保证；
- `sequence` 由该卡自己的 `CardState` 分配，`next_sequence` 自 1 起递增，成功后写入
  `last_success_sequence`。飞书只要求单卡严格递增，各卡独立计数使多张卡的 worker 互不阻塞；
- `uuid` 对应一次投影操作，由 publisher 在操作入队时生成一次并存入该操作，重试时复用同一个值。
  同一个 `call_id` 的多次更新因此得到不同的 `uuid`，不会被当作重复操作丢弃；
- 重试固定复用同一组 `uuid` 与 `sequence`，成功之后才推进 `next_sequence`。

### 5.4 容量与续卡

固定组件预算为 20 个元素与 4KB，用于表头、计数与续卡标注。单行按完成态的最大尺寸预留：
`_MAX_ROW_BYTES = 512`，其中工具名上限 64 字节、输入摘要上限 192 字节、输出摘要上限 192 字节，
其余为状态标记与结构开销。行在完成时会变大，因此预留发生在追加行时而非更新时，更新不会把卡片推过
上限。

据此单张卡承载的行数上限为 `(30 * 1024 - 4096) // 512 = 51`，组件数上限为 `200 - 20 = 180`，
取两者较小值。追加行前若剩余预算不足以再容纳一行，先创建续卡：前一张卡标注后续内容位于下一张，
新卡延续计数。前一张卡的 `CardState` 与行引用保留至该 turn 终态，使在第一张卡上开始、第二张卡
打开之后才结束的长跑工具，其完成状态回写到第一张卡。

### 5.5 限流与降级

发送同时满足应用级 1000 次/分钟、50 次/秒与单卡实体 10 次/秒。按工具粒度更新时一次调用最多产生
开始与结束两次操作。收到 429 时按退避重试。

turn 终态由 turn coordinator 作为一条队列命令投递，而非在事件到达时立即清理：worker 先 drain 队列
中剩余的操作、完成必要的降级标注，再释放该 turn 的 `CardState` 与行引用。

过程记录不完整的判定来自投影层自身可观测的失败：本地队列溢出、CardKit 重试耗尽、卡片更新被拒。
前两者仍可写卡时在过程卡上标注过程记录可能不完整。卡片本身不可写时记入日志与指标，并由投影层向
turn coordinator 上报一个降级标记，coordinator 在该 turn 的最终回复文本末尾附加一行提示，走既有
出站发送路径。

### 5.6 行的内容与脱敏

每行包含状态标记、工具名与一段短摘要，长度上限如 5.4 节所列，超出部分截断并以省略号标记。

新增 `core/sanitization.py` 统一敏感键词汇与可复用的展示脱敏能力：audit 继续对敏感键执行禁止策略，
活动投影在出站边界执行替换策略，两者不改变 `RuntimeOutputEvent` 原值。对将要进入摘要的输入与输出，
按正则替换形如 `sk-`、`ghp_`、`xoxb-` 开头的令牌串，以及长度不小于 32 的连续 base64 或十六进制串，
替换为固定占位符；键名命中共享敏感键集合时整体替换。脱敏在截断之前进行。provider 错误消息中基于
已知 secret 的精确替换保持独立。

摘要只读取 `ToolCall.name` 与可选的 `input` / `output`。按工具名选择图标属于展示层启发式，实现
留在 Lark 内部。

### 5.7 过程卡取代的两处行为

- Typing reaction 移除：`contrib/lark/channel.py` 的 `_TypingState`、`_typing_queue`、
  `_typing_runner` 及其启停随之删除，过程卡的出现本身即表示 turn 正在进行；
- 审批结果的额外回复移除：`contrib/lark/approval.py:313-340` 的 `_send_approval_feedback` 不再
  发送 `approval.feedback.approved` 与 `approval.feedback.rejected`，审批卡片自身经
  `approval.card.status.*` 更新的状态即表示结果。

## 6. Telegram 投影

Telegram 表达同一批语义，载体是可编辑的持久 Rich Message。`TelegramBotApi`
（`contrib/telegram/api.py`）当前只有 `send_rich_message` 与 `answer_callback_query`，没有编辑
类方法，因此先补齐 `edit_message_text`。

### 6.1 审批结果就地更新

审批提示当前经 `send_rich_message` 携带 `inline_keyboard` 发出
（`contrib/telegram/approval.py:125-167`），决定之后 `_send_approval_feedback`（`:299-351`）另发
一条 approved 或 rejected 回复。

改为在原 message_id 上更新：决定落定后调用 `edit_message_text`，在原正文末尾追加已批准或已拒绝，
并以空 `inline_keyboard` 清除按钮；`_send_approval_feedback` 的另发消息随之移除。Telegram 的
`InlineKeyboardButton` 没有 disabled 状态，保留按钮必须配置一种可执行 action，因此终态不保留
按钮。`answer_callback_query` 继续负责结束客户端按钮的 loading 并给出即时反馈。callback handler
完成校验并唤醒审批 Future 后，把
`answer_callback_query` 与原消息编辑交给 channel 生命周期持有的受管 task，串行 polling loop
立即继续处理下一条 update；channel stop 取消并收割尚未结束的 task。

### 6.2 活动过程消息

每个 turn 一条过程消息，在该 turn 的第一条 `ToolCallStarted`、`ContextCompactionStarted` 或
`ContextCompactionCompleted` 到达时经 `send_rich_message` 创建，保存
`(chat_id, message_id, topic_id)`。

Telegram 没有元素级补丁，编辑是整条替换，因此投影层在内存中维护该 turn 过程消息的完整文档：每次
`ToolCallStarted` 追加一行，`ToolCallCompleted` 与 `ToolCallFailed` 翻转对应行的状态，各类 delta
刷新该行摘要；压缩 started 显示进行中，completed 显示已完成，孤立 completed 直接补建已完成行。
随后经 `edit_message_text` 重绘整条消息。标题、状态、输入输出标签与压缩名称都从 i18n catalog
读取，完整消息由 `resources/telegram_activity.tpl` 渲染，不在 Python 中拼接展示文案。行的内容、
长度上限与脱敏沿用 5.6 节。

每个 turn 一个 single-writer 队列。因为每次编辑重写整条消息，短时间内的多个事件先合并再发一次
编辑，合并窗口 `_EDIT_DEBOUNCE_MS = 1000`；turn 终态到达时立即 flush 一次，不等窗口。429 按既有
出站重试口径处理。

容量沿用 `contrib/telegram/outbound.py:16` 的 `_MAX_RICH_MARKDOWN_BYTES = 32_768`。文档超出该上限
时发送第二条续消息，前一条消息的 message_id 与其行引用保留至该 turn 终态，使在第一条消息上开始、
第二条打开之后才结束的工具调用，其状态回写到第一条。

飞书的 `uuid`、`sequence` 与 `element_id` 是 CardKit 的机制，不进入 Telegram 实现。

`send_chat_action` 的 typing 行为保持现状。

## 7. WeCom 审批卡片

`request_approval` 改为发出一张审批卡片并等待用户决定，取代当前的无条件 `APPROVED`。全部动作走
既有的 WebSocket 连接。

发卡：以 `aibot_send_msg` 发送 `card_type` 为 `button_interaction` 的模板卡片，两个按钮对应同意
与拒绝，`task_id` 由投影层生成并与该次审批请求关联。

收决定：`_receive_message` 对 `aibot_event_callback` 的模板卡片事件不再丢弃，按 `task_id` 找到对应
的审批请求，由按钮 key 得出同意或拒绝，唤醒等待中的 `request_approval`。调用方给定的 `timeout`
只约束发卡的发送锁与 WebSocket ACK。WeCom 协议没有人工审批时限，因此发卡成功后 channel 永不
超时，等待用户决定或 channel 生命周期结束。

回终态：事件处理路径先以决定唤醒审批 Future，再立即创建由当前 WebSocket connection 持有的受管
task，以 `aibot_respond_update_msg` 应答；reader 不等待该 task，继续接收其它会话的帧。应答透传
事件的 `headers.req_id`，`response_type` 为 `update_template_card`，`template_card` 携带同一个
`task_id` 并把按钮换成已同意或已拒绝。企业微信要求事件回调后 5 秒内回复，因此该 task 保留 5 秒
发送窗口；这只约束终态卡回写，不约束人工审批。连接关闭、重连或 channel stop 时取消并收割旧连接
的回写 task。发送成功只表示帧已交给 transport，平台是否接受仍为 unknown；传输失败与结果 unknown
分别进入 health / audit，不能把无 ACK 的回写记为 provider confirmed。

审批结果只体现在卡片自身的状态上，与 Lark 和 Telegram 一致。

## 8. 任务拆分

### Task 1：Core 事件模型

- 新增 `core/models/events.py`，实现第 3 节的全部 dataclass 与联合；
- `entities.py` 与 `states.py` 中被取代的模型随之移除，`RuntimeEventState` 保留；
- 删除未被 adapter 驱动的 session-runtime 压缩状态、信号、转移与字符串识别入口；压缩活动事件只
  透传给 channel；
- 新增共享 sanitization 模块，统一 audit 与 channel 使用的敏感键词汇，并提供不改写 Core 事件的
  展示脱敏函数；
- `pyrightconfig.json` 增加 `"reportMatchNotExhaustive": "error"`；
- `core/runtime.py` 的 `RuntimeStreamItem` 与 `IRuntimeStream` 改为产出 `RuntimeOutputEvent`；
- `core/channel.py:179` 与 `:240` 的 `accept_turn_event` 签名随之调整；
- `core/orchestration/turn.py:405-430` 改为对 `event.payload` 做 `match`，turn 级变体既走既有
  持久化与 reducer 路径也继续转发给 channel，其余变体直接转发；异常捕获与日志行为沿用；
  `provider_turn_id` 改从 envelope 读取，`turn.py:599-627` 的关联与写回逻辑保持等价；
- 测试覆盖五个 turn 级变体的入库与转发、`provider_turn_id` 的关联与写回、终态 `metadata` 的保留；
- checks：`tests/core/`、`ruff format --check .`、`ruff check .`、
  `uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 2：Codex adapter 归一化

- 按第 4 节改写 `contrib/codex/events.py`，生命周期参数取自 `item`，`turnId` 与时间戳落到
  envelope；`contextCompaction` 映射中立 started/completed；删除 `turn/progress` 的失效分支；
- 测试用真实协议样本覆盖通用 name/input/output 投影、失败状态映射到 `ToolCallFailed`、内容类 item
  与线程状态类 item 的既有过滤，以及 `thread/tokenUsage/updated` 映射到 `UsageUpdated` 的 `last`
  与 `total`；不枚举全部内置工具，也不为虚构的 future type 编写降级测试；
- checks：`tests/contrib/codex/` 与 Task 1 相同的 gates。

### Task 3：Claude adapter 归一化

- 按第 4 节改写 `contrib/claude/events.py`，`tool_use` 产出 started 并登记 `call_id` 到 `name`
  的映射，`kind == "user"` 分支产出 completed 或 failed 并读出该映射；
- `TurnEventStream` 增加待发队列，支持一条 envelope 产出多个事件；
- `system/compact_boundary` 映射为可独立到达的 `ContextCompactionCompleted`；
- 测试覆盖一条 assistant 消息含多个工具块时逐个产出、completed 取到正确的工具名、未登记时回退到
  `tool_use_id`、`is_error` 为真时产出 `ToolCallFailed`、`system` 任务类消息映射到
  名为 `task` 的工具调用、终态 result 同时产出 `UsageUpdated` 且 `metadata` 字段不变；
- checks：`tests/contrib/claude/` 与 Task 1 相同的 gates。

### Task 4：WeCom 迁移与审批卡片

- `accept_turn_event` 按新模型改写，对外行为与迁移前等价；
- 按第 7 节改写 `request_approval`：发 `button_interaction` 卡片、按 `task_id` 关联、由模板卡片
  事件唤醒；`_receive_message` 先完成审批 Future，再以 connection-owned task 发送
  `aibot_respond_update_msg`，reader 不等待回写；task 保留协议要求的 5 秒窗口，并在连接生命周期结束
  时取消、收割；
- 测试覆盖发卡帧的构造、模板卡片事件唤醒等待中的 `request_approval`、同意与拒绝各自的终态卡片、
  应答帧透传事件的 `req_id` 且 `task_id` 一致、挂住会话 A 的终态卡回写时会话 B 的入站帧仍被
  reader 接收、5 秒窗口失败的 unknown 观测、上游取消以及连接/channel stop 清理等待状态与回写 task；
- 移除 Claude permission bridge 使用通用 `provider_call_seconds` 包裹人工审批等待的
  `asyncio.timeout`，等待只由用户决定、显式取消或 session 生命周期结束；
- 删除只服务于该 timeout deny 的死代码，同步修订 Claude runtime 原计划中的 approval contract；
- 真实 Claude E2E 将原自动超时场景改为审批挂起时停止 node，验证 lifecycle cancellation 清理；
- checks：`tests/contrib/wecom/`、`tests/contrib/claude/` 与 Task 1 相同的 gates；真实 Claude E2E
  留在 Task 9 最终验收。

### Task 5：Telegram 编辑能力与审批就地更新

- `contrib/telegram/api.py` 新增 `edit_message_text`；
- 按 6.1 节改写 `contrib/telegram/approval.py`：决定后在原消息正文追加审批结果并清空 inline
  keyboard，移除
  `_send_approval_feedback` 的另发消息，保留 `answer_callback_query`；两次网络调用由 channel 持有的
  受管 task 执行，polling loop 不内联等待，并在 stop 时取消、收割；
- `accept_turn_event` 按新模型改写；
- 测试覆盖编辑请求的构造、决定后正文保留结果且按钮被清空、不再产生额外消息、
  `answer_callback_query` 照常调用、
  挂住会话 A 的 callback 回写时会话 B 的 update 仍被处理，以及 stop 清理回写 task；
- checks：`tests/contrib/telegram/` 与 Task 1 相同的 gates。

### Task 6：Telegram 活动消息投影

- 按 6.2 节实现每 turn 一条过程消息：首条工具或压缩活动创建，其后整条重绘；
- 展示文案全部进入中英文 catalog，整条消息通过资源模板渲染；压缩 started/completed 分别显示
  进行中/已完成，completed 可以独立补建行；
- 内存文档、single-writer 队列、`_EDIT_DEBOUNCE_MS` 合并窗口与终态立即 flush；
- 超出 `_MAX_RICH_MARKDOWN_BYTES` 时发送续消息，旧消息的行引用保留至该 turn 终态；
- 测试覆盖：首条 started 触发创建、无工具调用的 turn 不产生消息、合并窗口内多个事件只发一次编辑、
  终态立即 flush、超限触发续消息后旧消息仍可回写、行内容沿用脱敏与截断；
- checks：`tests/contrib/telegram/` 与 Task 1 相同的 gates。

### Task 7：Lark CardKit 客户端

- 按第 5 节调整长连接 transport：删除外层 `_send_lock`，ACK 通过 connection-owned task 发送且不阻塞
  reader，ACK 完成后再执行 post-ACK 更新；`ws_connect` 启用 transport heartbeat，连接关闭、重连与
  channel stop 取消并收割旧连接 task，不为 ACK 增加应用层 deadline；
- 按 5.1 节在 `contrib/lark/api.py` 新增三个方法，并实现按 `card_id` 发送 interactive 卡片；
- 测试覆盖请求构造、`uuid` 与 `sequence` 的传递、按 card_id 发送后 provider message id 的保存，
  以及 429 的错误分类；transport 测试挂住会话 A 的 ACK 回写并验证会话 B 的事件仍被 reader 接收，
  验证 ACK 与 post-ACK 的顺序、heartbeat 配置及重连/stop 清理发送 task；
- checks：`tests/contrib/lark/` 与 Task 1 相同的 gates。

### Task 8：Lark 过程卡投影

- 新增 `contrib/lark/activity.py`，实现 5.2 至 5.6 节；
- 同一过程卡消费中立压缩活动；各 runtime 有什么阶段就展示什么阶段，不推导缺失阶段；
- `contrib/lark/channel.py:854` 的 `accept_turn_event` 改为同步、无阻塞入队，投影在后台任务完成；
- 按 5.7 节移除 Typing reaction 相关实现与审批结果的额外回复；
- 测试覆盖：首条 `ToolCallStarted` 触发懒创建、无工具调用的 turn 不产生卡片、重复
  `ToolCallStarted` 走更新、只见完成事件时补建行、按最大完成态行尺寸预留触发续卡、续卡后旧卡仍可
  回写、串行队列在乱序输入下的发送顺序、重试复用同一组 `uuid` 与 `sequence` 且不同更新得到不同
  `uuid`、终态经队列命令 drain 后才释放状态、重试耗尽后标注不完整、卡片不可写时降级标记随最终
  回复附加、脱敏函数对各类令牌串的替换；
- checks：`tests/contrib/lark/` 与 Task 1 相同的 gates。

### Task 9：最终验收

- 运行完整非 e2e pytest suite 与 `-m e2e`；
- 运行 `ruff format --check .`、`ruff check .`、
  `uv run scripts/pyright_lsp_check.py --outputjson .`、
  `uv run python -m compileall -q src tests`、`uv lock --check`、`git diff --check`；
- 在一个真实飞书群里跑通一轮含多次工具调用的会话，确认过程卡出现在会话中、按工具逐行更新、状态
  正确翻转、最终回复照常送达，并确认审批卡片通过后不再出现额外回复；在企业微信里触发一次审批，
  确认卡片按钮可点、决定后原卡就地变为终态。这是无法在单元测试中验证的部分；
- 汇总结果，停在最终 review。

## 9. 验收标准

1. 全部消费点使用 `RuntimeOutputEvent`，Core 中只存在这一套运行时事件模型。
2. Core 模型的字段与类型独立于 provider 协议；`input`、`output` 与 `metadata` 限定为 JSON 值。
3. `pyrightconfig.json` 开启 `reportMatchNotExhaustive`；消费 `RuntimeEventPayload` 的 `match`
   不写兜底分支，漏掉任一变体时在 CI 阶段失败。
4. 五个 turn 级状态在新模型中一一对应，`TurnUnknown` 承接 `RuntimeEventState.UNKNOWN`；错误上报
   与 runtime 切换的判定结果与迁移前一致。
5. `provider_turn_id` 经 envelope 传递，turn 关联与写回 `RuntimeTurn.provider_turn_id` 的行为与
   迁移前一致；终态 `metadata` 中的 `usage`、`total_cost_usd`、`stop_reason` 等照常入库。
6. turn 级变体既入库也转发给 channel。
7. Codex 的生命周期事件从 `params.item` 读取 ID 与类型；工具名使用真实通用字段或中立 type 标签，
   `input` / `output` 只接收严格校验过的选定 JSON 槽位，不携带原始 item；失败只由
   `ToolCallFailed` 变体表达。
8. Claude 的 `tool_use` 产出 `ToolCallStarted` 且携带 `name` 与 `input`；`tool_result` 产出的
   completed 或 failed 携带同一个 `call_id` 对应的工具名。
9. Codex 的上下文压缩产出 started/completed；Claude 的公开 compact boundary 只产出可独立消费的
   completed；两者都不驱动 session-runtime 状态机。
10. 一条 envelope 包含多个工具块时，每个块都产出对应事件。
11. content delta 保留各自的 kind 与 reasoning index，工具文本 delta 按通知后缀映射为 output 或
    progress，不按 Codex 内置工具类型分支。
12. 3.1 节迁移表中的每一条现有语义在新模型中都有对应产出；`reasoning` 的正文与摘要分属两个
    `ContentDeltaKind` 取值。
13. Codex 的 `thread/tokenUsage/updated` 与 Claude 终态 result 的 `usage` 都产出 `UsageUpdated`；
    Codex 的多次上报各自成事件，`last` 与 `total` 分别落位；终态 `metadata` 的内容与迁移前一致。
14. channel 抛出异常时 turn 主链路照常完成。
15. 飞书过程卡在该 turn 首条 `ToolCallStarted` 时创建并发送进会话，无工具调用的 turn 不产生卡片。
16. 重复的 `ToolCallStarted` 更新既有行；只收到完成或失败事件时补建一行。
17. 追加行时按完成态的最大行尺寸预留，续卡由本地的组件数与字节数核算触发；前一张卡的状态保留至
    该 turn 终态，在第一张卡开始、第二张卡打开后才结束的工具调用，其状态回写到第一张卡。
18. 每张卡的发送严格串行；`sequence` 由该卡的 `CardState` 分配；同一个 `call_id` 的多次更新得到
    不同的 `uuid`；重试复用同一组 `uuid` 与 `sequence`，成功后推进。
19. `element_id` 由投影层生成，长度不超过 20 字符。
20. turn 终态经队列命令处理，drain 完成后才释放该 turn 的状态。
21. 本地队列溢出或重试耗尽且卡片仍可写时，过程卡被标注为可能不完整；卡片不可写时降级提示随最终
    回复送达。
22. 进入摘要的输入与输出先脱敏后截断。
23. Lark 不再发送 Typing reaction，审批通过或拒绝后不再发送额外回复，审批卡片自身状态更新照常。
24. Telegram 审批决定后在原消息正文追加结果并清空 inline keyboard，不再发送额外消息，
    `answer_callback_query` 照常调用。
25. Telegram 活动消息在首条工具或压缩事件时创建，其后整条重绘；压缩完成事件可以独立成行，全部
    展示文案经 i18n 与资源模板渲染；合并窗口内的多个事件只产生一次编辑，turn 终态立即 flush。
26. Telegram 文档超出 `_MAX_RICH_MARKDOWN_BYTES` 时发送续消息，前一条消息的行引用保留至该 turn
    终态。
27. WeCom 不再无条件批准：`request_approval` 主动发送审核卡，模板卡片事件唤醒等待者并以
    同一 `task_id` 就地更新终态；除此之外的 WeCom 对外行为与迁移前等价。
28. full pytest、Ruff、Pyright、compileall、lock 与 diff gates 全部通过。
