# BCN Runtime 错误反馈与国际化

## 状态

- 当前阶段：Code，Task 3 implementation 与验证已完成，diff 等待 review。
- 工作分支：`f-20260818-runtime-error-feedback`。
- 基线：`main@c1870911aa0b43dbcc74ac6eb20ca651088d7b8c`。
- Plan 已作为 `fcda6c4` 推送；Task 1 已作为 `39b397d` 推送；Task 2 已作为 `e6d27ce` 推送；Task 3 diff 位于当前工作区。
- 每个 Task 串行开发；完成实现与验证后停下 review。commit 和 push 分别等待明确授权。

## 目标

1. 抽取无状态的 `OutboundDeliveryService`，让 `bcc message send` 与 runtime error feedback 共享同一条 provider 调用和结果归并链路。
2. `bcc message send` 继续负责现有持久化、fresh-check、draft、附件和 command error 语义。
3. Runtime 完成恢复与重试后，如果最终状态为 `FAILED` 或 `UNKNOWN`，向触发该 turn 的 Channel route 回复一条错误反馈。
4. 错误反馈直接携带当前 `RuntimeTurn.error_message`，仅精确替换已知 token 值，保留具体错误以便诊断。
5. 在 `src/bazaar_compute_node/i18n` 提供 English 与简体中文 catalog，根据部署环境系统语言渲染，默认回退 English。
6. 错误反馈完成后，原 `RuntimeTurn` 继续作为 notification 的最终结果。

## 现状与问题

`SessionCommandService.send()` 当前同时承担两组职责：

1. BCC command 语义：解析目标和 reply、解析附件、创建并持久化 `OutboundMessage`、执行 fresh-check、保存 draft 与最终 delivery state。
2. Provider delivery 语义：调用 `IChannel.send()`、捕获 adapter 异常，并将 `ProviderCallResult` 映射为 `SENT/QUEUED/PARTIAL/FAILED/UNKNOWN`。

错误反馈与 BCC command 共享第二组 provider delivery 职责；第一组 command 职责继续由 `SessionCommandService` 独立拥有。因此抽取无状态 delivery boundary，让两个调用方共享一次 provider attempt 的完整归并逻辑。

当前 `ChannelSendRequest` 携带完整的 `OutboundMessage`。目标 contract 改为真实投递所需的瞬时字段，让 durable BCC command 与 transient error feedback 都能直接构造同一种 provider request。

## 目标边界

```text
bcc message send
    validate target, reply and attachments
    persist OutboundMessage
    run fresh-check
    build ChannelSendRequest
        -> OutboundDeliveryService
            -> IChannel.send
            <- ProviderCallResult
        <- OutboundDeliveryResult
    persist delivery state

final runtime turn
    FAILED or UNKNOWN
    render localized error
    replace known token values
    build ChannelSendRequest from inbound route
        -> OutboundDeliveryService
    audit feedback outcome only
```

`OutboundDeliveryService` 是纯内存的 provider attempt service。调用方分别拥有自己的校验、持久化、重试策略与 audit 语义。

## Channel 投递契约

### `ChannelSendRequest`

将 `core/channel.py` 的 request 改为只包含 adapter 真正使用的瞬时输入：

```python
@dataclass(frozen=True, slots=True)
class ChannelSendRequest:
    session_id: str
    body: str
    attachments: tuple[OutboundAttachment, ...]
    target_kind: ChannelTargetKind
    provider_thread_id: str
    provider_reply_to_message_id: str | None = None
```

约束：

- `session_id` 保留给 Telegram stream route correlation。
- `body` 与 `attachments` 表达一次 logical message；是否为空继续由业务调用方和 adapter 现有防御检查负责。
- provider route 仍由 `provider_thread_id`、`target_kind` 和 provider reply ID 表达。
- Telegram、WeCom 与 test adapter 改为直接读取 request 字段，并保持现有 provider 行为。

### `OutboundDeliveryService`

在 core orchestration 增加共享 service：

```python
class OutboundDeliveryService:
    async def deliver(
        self,
        request: ChannelSendRequest,
    ) -> OutboundDeliveryResult: ...
```

`OutboundDeliveryResult` 是一次 provider attempt 的不可变结果，包含：

- 归一后的 `OutboundDeliveryState`；
- provider message ID 与 receipt reference；
- provider receipt metadata；
- error kind、error message 与需要人工决定整条重试时的 next action。

状态归并保持现有语义：

| Provider 结果 | Delivery state | 语义 |
| --- | --- | --- |
| `CONFIRMED` | `SENT` | provider 已确认可见交付 |
| `QUEUED` | `QUEUED` | provider 已接收但未最终确认 |
| `PARTIAL` | `PARTIAL` | 已有部分可见交付，整条消息重试交由人工决定 |
| `FAILED` | `FAILED` | provider 明确拒绝或发送前失败 |
| `UNKNOWN` | `UNKNOWN` | 结果不可确认，必须人工 reconcile |
| adapter 抛出非取消异常 | `UNKNOWN` | 交付状态保持未知并保留原异常文本 |

`asyncio.CancelledError` 继续向上传播。`CONFIRMED/QUEUED/PARTIAL` 缺少必需 receipt 时抛出 contract violation。

BCC command 与 error reporter 根据同一个 delivery result 分别记录各自的事件名称和 correlation；service 专注 provider attempt 与结果归并。

## `bcc message send` 保持的职责

`SessionCommandService.send()` 继续拥有：

- body、target、reply 与附件校验；
- `OutboundMessage` 初始保存与所有后续状态保存；
- fresh-check snapshot、current inbound sequence 与拒绝后的 draft；
- command-facing `ErrorKind`、`next_action` 与返回 payload；
- `bcc.send.*` 和 `channel.outbound.*` audit。

通过 fresh-check 后，command service 将已经持久化的 outbound 字段显式投影为 `ChannelSendRequest`，调用共享 delivery service，再把 `OutboundDeliveryResult` 应用回 `OutboundMessage`。错误反馈直接从 reporter 进入 delivery service。

Task 1 使用现有 focused tests 锁定 fresh-check 成功/失败、正文与附件、reply route、provider receipt、partial/unknown、adapter exception、draft 与 command response。

## Runtime error feedback

### 触发点

`SessionOrchestrator._runtime_loop()` 在 `turn_task.result()` 返回后拿到经过 session establishment、recovery 和 runtime retry 的最终 `RuntimeTurn`。Reporter 在此处、完成对应 notification future 之前最多执行一次：

```text
turn_task.result()
    -> final RuntimeTurn
    -> report FAILED or UNKNOWN once
    -> complete notification futures with original RuntimeTurn
```

选择该位置的原因：

- recovery 与 retry 已经收敛为一个 final result；
- stream event 和 synthesized terminal state 已统一收敛为 `RuntimeTurn`；
- 当前 `_WakeNotification` 仍可提供精确 Channel route；
- reporter 的 delivery outcome 可以在同一边界记录，notification completion 继续使用原 `RuntimeTurn`。

Reporter 的触发集合固定为 `{FAILED, UNKNOWN}`。对 batched inbound notifications 依据 `batch[0]` 发送一次。

### Route 与 reply

- 普通 Channel turn 使用触发 turn 的 `InboundMessage`。
- Reminder turn 使用 `_ReminderNotification.anchor_message`。
- `provider_thread_id` 与 `target_kind` 原样沿用 inbound route。
- `provider_reply_to_message_id` 使用触发/anchor inbound 的 `provider_message_id`，在支持 reply 的 Channel 中把错误放回原上下文。
- `session_id` 使用对应 BCN session ID；`attachments` 使用空 tuple。

### 内容

Reporter 根据 terminal state 选择稳定 message key：

- `runtime.error.failed`
- `runtime.error.unknown`

插值参数 `error` 优先使用 `RuntimeTurn.error_message`；缺失时回退到 `error_kind`，再回退到 terminal state value。Reporter 直接消费 turn coordinator 已经收敛的 terminal detail。

示例语义：

```text
Execution failed: ${error}
Execution status is unknown: ${error}
```

具体中文文案由 catalog 定义，reporter 只按 terminal state 选择 message key。

### Reporter outcome 语义

- Reporter 为每个 final turn 执行一次 provider attempt。
- `SENT/QUEUED` 记录成功或已接受 audit。
- `PARTIAL/FAILED/UNKNOWN` 记录 `runtime.error_feedback.failed`，包含安全的 state、error kind 与 provider receipt reference。
- reporter 自身的非取消异常同样只记录 audit/log。
- 所有 feedback outcome 均返回原 `RuntimeTurn` 及其 state/error。

## Token replacement

错误反馈保留具体 terminal error，只对调用边界已知的 token 原值做精确字符串替换：

```python
for token in token_values:
    if token:
        text = text.replace(token, "<redacted>")
```

规则：

- 使用 `<redacted>`，与当前 Telegram API error 的 `description.replace(self._token, "<redacted>")` 保持一致，并明确标识脱敏位置。
- 算法是区分大小写的精确字符串替换，其余错误正文保持原样。
- adapter 继续负责过滤自己持有的 provider token；Telegram 保留现有逻辑。
- `AgentApplication` 捕获 runtime process 实际可见的 token：当前 session 的 `BCN_COMMAND_CAPABILITY`，以及通过 `runtime.env_include` 显式注入且变量名为 `TOKEN` 或以 `_TOKEN` 结尾的值。
- channel adapter 继续独立持有 provider token；runtime token values 保持在 Agent composition boundary。
- token 值为空时跳过，重复值在调用处内联去重。

`AgentApplication` 使用 session binding 中的 immutable token values 生成 error feedback detail；core reporter 只提交 session ID 与 error text。

## i18n

### 文件边界

新增：

```text
src/bazaar_compute_node/i18n/
    __init__.py
    catalog.py
    english.py
    schinese.py
```

参考 Ishiku 的 locale 模块，但适配 BCN 的多 Agent 并发模型：

- message 使用稳定 key，业务代码只选择 key；
- English 与简体中文 resource 分离；
- `${name}` 形式参数插值；
- language lookup fallback 为 English；
- message key lookup fallback 为 key 本身，保证错误反馈可见且便于发现 catalog defect；
- 两套 catalog 必须具有相同 key 集合。

`NodeApplication` 启动 composition 时创建一次 immutable translator，随后注入各 Agent reporter。该 translator 在进程生命周期内保持固定语言。

### `config.toml`

在现有 `[node]` 增加可选 `lang`：

```toml
[node]
lang = "zh-CN"
```

配置语义：

- `lang` 校验为非空文本；值为 `zh-CN` 时使用简体中文，其他任何值统一使用 English。
- `NodeConfiguration.lang` 使用 `str | None`；`None` 表示未显式配置，启动时按系统语言选择。
- 序列化在显式配置时写出 `lang`；默认配置由目标部署环境的系统语言决定。
- `lang` 属于 node，同一进程内所有 Agent 使用相同 translator。
- 配置在进程启动时生效，语言在该进程生命周期内保持固定。

### 系统语言选择

当 `[node].lang` 未配置时，使用 Python 标准库 `locale.getlocale()` 读取 Python 已初始化的系统 locale，并取返回 tuple 的 language code：

- `zh_CN` -> 简体中文；
- 其他 language code 与 `None` -> English。

显式 `[node].lang` 优先于系统语言。标准库负责跨平台读取 locale，BCN 对 `locale.getlocale()` 返回的 structured language code 做一次选择。

### Catalog 验证

测试至少覆盖：

- English 与简体中文 key 完整一致；
- `failed`/`unknown` 的 `${error}` 插值；
- language lookup 的 English fallback；
- message key lookup 的 key fallback；
- 简体中文 locale variants 与默认 English 选择；
- `[node].lang` 显式优先、缺省使用系统语言、非空约束、未知值 English fallback 及配置 round-trip；
- error detail 中的 `$`、换行和非 ASCII 内容保持原样。

## Audit 与可观测性

沿用现有 `SessionAuditRecorder` 和 correlation，直接记录本次 transient feedback attempt。建议事件：

- `runtime.error_feedback.started`
- `runtime.error_feedback.sent`
- `runtime.error_feedback.failed`

Audit metadata 使用 terminal state、delivery state 和 provider receipt reference 字段白名单。错误正文进入现有 `error_message` 字段前先执行同一 token replacement。

## Tasks

### Task 1：抽取无状态 outbound delivery boundary

修改范围：

- `src/bazaar_compute_node/core/channel.py`
- `src/bazaar_compute_node/core/outcomes.py`
- `src/bazaar_compute_node/core/orchestration/command.py`
- 新增 core orchestration delivery module
- Telegram、WeCom 与 test Channel adapters
- command、adapter 与 delivery focused tests

实现：

1. 将 `ChannelSendRequest` 调整为瞬时投递字段，由 BCC command 和 error reporter 分别构造。
2. 增加 `OutboundDeliveryResult` 与无状态 `OutboundDeliveryService`，集中 provider call、exception 和结果状态归并。
3. `SessionCommandService` 在 fresh-check 后调用 service，并把 result 应用回原 durable `OutboundMessage`。
4. 保留现有 command response、draft、receipt、audit 与 attachment preflight 行为。

验证：

- focused delivery mapping tests 覆盖全部 provider status 与 adapter exception；
- 现有 BCC send/fresh-check/reply/attachment tests；
- Telegram、WeCom outbound focused tests；
- Ruff format/check、相关文件 LSP/pyright、`git diff --check`。

Task 1 review 完成后作为 `39b397d` 推送，再进入 Task 2。

### Task 2：增加 i18n 与精确 token replacement

修改范围：

- `src/bazaar_compute_node/i18n/`
- `src/bazaar_compute_node/app/config.py`
- `src/bazaar_compute_node/app/application.py`
- `src/bazaar_compute_node/app/agent.py`
- 对应 i18n、composition 与 redaction tests

实现：

1. 增加 immutable translator、English/简体中文 catalog、`${...}` 插值和 English fallback。
2. 在 `[node]` 增加可选非空 `lang`；显式配置优先，只有 `zh-CN` 使用中文，其他值 fallback 到 English，缺省时解析一次 system locale。
3. `NodeApplication` 创建 translator 并传给全部 Agent，进程生命周期内语言保持固定。
4. 从现有 session capability 与显式 runtime token environment 收集 immutable token values，由 Agent composition 生成 error feedback detail。
5. 保留 Telegram adapter 现有 provider token replacement。

验证：

- catalog completeness、fallback、interpolation 与 locale variant tests；
- config explicit/default/empty/unknown/serialization tests，以及 node composition 选择优先级；
- 单 token、多 token、重复 token、空 token、错误正文保持测试；
- capability 与显式 `*_TOKEN` 被替换，普通 runtime environment value 保持原样；
- Ruff format/check、相关文件 LSP/pyright、`git diff --check`。

完成实现与验证后以 uncommitted Task 2 diff 进入 review；review 通过后再确定 commit、push 与 Task 3。

### Task 3：接入 final runtime error reporter

修改范围：

- `src/bazaar_compute_node/core/orchestration/session.py`
- 新增 core runtime error reporter module
- `src/bazaar_compute_node/app/agent.py`
- session orchestration、reminder 与 error feedback tests

实现：

1. Reporter 只接收 final `RuntimeTurn`、原始 wake route、translator、Agent error feedback detail 生成边界与共享 delivery service。
2. `_runtime_loop()` 在 final result 后对 `FAILED/UNKNOWN` 调用一次 reporter。
3. 普通 inbound 与 Reminder anchor 均 reply 到原 provider message；batched notifications 只发送一次。
4. delivery failure 只写 audit/log，原 `RuntimeTurn` 原样完成所有 notification futures。

验证至少覆盖：

- `FAILED` 原始 error detail 发送与 token replacement；
- `UNKNOWN` synthesized/provider error 发送；
- `COMPLETED`、`CANCELLED` 和 `None` 的 delivery attempt count 为 0；
- recovery 中间 error 的 delivery attempt count 为 0，final failure 为 1；
- batched inbound 只发送一次；
- Reminder 使用 anchor route/reply；
- reporter 的 `FAILED/PARTIAL/UNKNOWN` 或 exception 场景均保持原 turn result，delivery attempt count 为 1；
- English/简体中文端到端文案；
- focused tests、完整 non-e2e regression、Ruff format/check、Pyright、compileall、lock verification、相关文件 LSP、`git diff --check`。

完成实现与验证后以 uncommitted Task 3 diff 进入 review；后续 commit、push、PR、merge、发布与部署分别确认。
