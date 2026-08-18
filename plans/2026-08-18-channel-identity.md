# BCN Channel 身份协议与消息说话人显示

## 状态

- 当前阶段：Plan，implementation 尚未开始。
- 工作分支：`f-20260818-channel-identity`。
- 基线：`main@1f5a24f13536c1bf5c6fc19b8ad7f2afd3e41aca`。
- 当前 plan 文件保持未提交、未推送，先等待 review。
- 每个 Task 串行开发；完成实现与验证后停下 review。commit 和 push 分别等待明确授权。
- 本计划不包含旧消息回填、schema migration、配置迁移、PR、merge、发布或部署。

## 目标

1. 增加 provider-neutral Channel identity contract，让 Channel 在启动后暴露当前 provider account 的 ID 和可选 name。
2. Developer instruction 的 Agent 名称按 `Channel identity name -> Channel identity id -> config agent.name` 解析；稳定的 BCN `agent.id` 不变。
3. Telegram 复用现有 `getMe` 结果，同时提供 bot ID 和 username，不增加网络请求。
4. Telegram inbound message 的说话人优先显示 username，没有 username 时显示 numeric ID；人类、其他 bot 和 quoted message 使用相同规则。
5. Approval 仍发送到触发 turn 的 DM、group 或 topic，但任意能看到有效按钮的人都可以完成 approval。
6. 将可读显示、路由 identity 和授权主体明确分离，禁止 username 进入路由或权限判断。

## Telegram 能力结论

Telegram Bot API 的 `getMe` 返回 `User`。`User` 同时包含 numeric `id` 和可选 `username`；Inbound `Message.from` 也是 `User`，因此人类与 bot 的 ID、username 可以直接从 update 读取。匿名管理员或 channel 发言可能通过 `sender_chat` 表达。

参考：<https://core.telegram.org/bots/api#getme>、<https://core.telegram.org/bots/api#user>、<https://core.telegram.org/bots/api#message>。

## 身份边界

本需求涉及三类 identity，必须保持独立：

```text
BCN Agent identity
    stable agent.id + configured agent.name
    owns workspace, storage scope, session namespace and capability

Channel self identity
    provider account id + optional provider account name
    supplies the runtime-visible Agent name

Inbound speaker label
    one display value copied when a message enters BCN
    only tells the runtime who appeared to speak that message
```

### BCN Agent identity

- `agent.id` 继续是稳定 UUIDv7 identity。
- Channel account ID 不能替代 `agent.id`，不能参与 workspace、storage scope、session namespace、capability binding 或 durable ownership。
- `config.toml` 的 `agent.name` 继续用于配置唯一性、管理、health 和 outbound identity snapshot；本需求只改变 developer instruction 中的可读自称。

### Inbound speaker label

`InboundMessage.sender` 保留现有单字段模型，但语义明确为“消息接收时固化的说话人显示值”：

- Telegram 有 username 时保存 username；没有时保存 numeric ID。
- 写入后不随 Telegram 改名而更新，历史消息展示接收当时的名字。
- 不用于 DM/group/topic 路由、reply、dedupe 或 approval authorization。
- 路由继续由 `provider_thread_id`、`provider_message_id` 和 `target_kind` 承担。
- 不增加 `sender_id`，因为当前没有任何路由或授权消费者需要它。
- username 存储时不带 `@`；现有 bcc header renderer 负责输出 `@sender`，避免 `@@name`。

不处理旧数据：已有 `inbound_messages.sender` 保持原值，新 ingress 上线后采用 username-first 规则；不 backfill、不双读。

## Core contract

### `ChannelIdentity`

在 `core/channel.py` 增加 immutable value object：

```python
@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    id: str | None = None
    name: str | None = None
```

约束：

- `id` 和 `name` 至少一个非空。
- 已提供字段必须是非空、无换行的字符串。
- core 不修改大小写、不添加 mention prefix、不从 name 推导 ID。
- 不增加 `normalize` 或隐藏 fallback 的 helper；fallback 在 composition boundary 明确表达。

`IChannel` 增加同步查询：

```python
def get_identity(self) -> ChannelIdentity | None: ...
```

选择同步查询的原因：provider discovery 属于 `start()` 生命周期；查询只读取 adapter 已确认的内存状态，不应为每个 runtime session 再发网络请求。

- `AgentScopedChannel` 直接委托，不对 provider identity 做 UUID namespace 转换。
- adapter 不支持 identity discovery、尚未 ready 或已经 stop 时返回 `None`。
- 本次 Telegram 提供 identity；WeCom 默认返回 `None`，未来可独立实现。

## Developer instruction 名称解析

当前 `RuntimeCommandContext.agent_name` 在 `AgentApplication.__init__` 时固定为 config name，但 Channel identity 要到 `Channel.start()` 完成后才存在。采用显式 resolver：

```python
@dataclass(frozen=True, slots=True)
class RuntimeCommandContext:
    resolve_agent_name: Callable[[], str]
    agent_id: str
    # existing fields remain unchanged
```

`AgentApplication` 构造 resolver，按以下顺序选择：

```text
channel.get_identity().name
    -> channel.get_identity().id
    -> configuration.name
```

Codex runtime 仅在 `start_session()` 创建新 provider thread 时调用 resolver，并把结果传给 `DeveloperInstructionContext.agent_name`。

当前 orchestrator 虽先启动 Runtime lifecycle、后启动 Channel lifecycle，但只有二者都成功后才接受 ingress；因此第一次 `start_session()` 发生时 Channel identity 已 ready，不需要重构 async composition。已经创建的 Codex thread 不动态改名，避免同一 provider thread 的 developer instruction 漂移。

resolver 返回值继续经过 `DeveloperInstructionContext` 的非空、无换行校验；异常沿现有 runtime session startup failure 路径返回，不静默 fallback 无效值。

## Telegram mapping

### 当前 bot identity

`TelegramChannel.start()` 已执行 `getMe` 并保存 `_bot_id`、`_bot_username`。`get_identity()` 直接返回：

```python
ChannelIdentity(
    id=str(bot_id),
    name=bot_username,
)
```

不新增 API 调用。health 中已有 `bot_id`、`bot_username` 保留用于诊断，但 health 不代替 identity contract。

### Inbound speaker label

Telegram sender extraction 调整为：

```text
if message.from is valid:
    message.from.username
        -> str(message.from.id)
else if message.sender_chat is valid:
    message.sender_chat.username
        -> str(message.sender_chat.id)
else:
    None
```

细节：

- `from` 和 `sender_chat` 是互斥分支，不混合字段。
- username 保持 Telegram 返回的原始大小写，不带 `@`。
- 不使用 `first_name`、`last_name` 或 chat title 冒充稳定 handle。
- 当前消息与 quoted/replied message 复用同一 extraction。
- 顶层 current-bot filtering 继续比较 numeric bot ID，不使用可变 username。
- `metadata.sender_is_bot` 继续来自 `User.is_bot`；`sender_chat` 没有等价字段时保持 `None`。
- message type、canonical target、provider route、mention activation 和 dedupe 语义不变。

## Approval 边界

当前流程把 `InboundMessage.sender` 填入 `ChannelApprovalRequest.provider_sender_id`，Telegram callback 再用 `from.id` 限制点击者。username-first 后该字段会成为显示名，继续比较 numeric callback ID 会产生错误；同时也违背“任意人可点击”的已确认语义。

本需求删除：

- `ChannelApprovalRequest.provider_sender_id`；
- orchestration 中从 `message.sender` 到 approval request 的传递；
- Telegram `_PendingApproval.expected_sender_id`；
- callback sender mismatch 分支及其文案、disposition 和测试。

callback 的 `from` 仍必须满足 Telegram callback query 的结构要求，但其 ID 不参与 authorization。以下 correlation 全部保留：

```text
opaque random token
    + pending request id
    + chat id
    + topic id
    + approval prompt message id
    + unresolved future
```

第一个通过全部 correlation 检查的 callback 完成 future；后续点击继续走 resolved/duplicate 语义。Approval prompt 和结果反馈仍发送到触发 turn 的原 DM、group 或 topic。

## 备选方案与取舍

### 方案 A：持久化 `sender_id + sender_name`

优点是同时保留稳定 provider ID 与可读名字；缺点是引入 schema migration、codec、repository 和所有 adapter 的新字段。当前 sender 不负责路由或授权，新增 ID 没有消费者，属于过度建模，不采用。

### 方案 B：继续限制为消息发送者审批

优点是权限更窄；缺点是与“任意人可点击”的产品决定冲突，并且会把显示字段误当 provider ID，不采用。

### 方案 C：Channel ready 后重建 Runtime composition

可以向 Runtime 传固定字符串，但需要把 `AgentApplication`、orchestrator 和 control handler 拆成多阶段 async composition。resolver 已能在正确生命周期读取 identity，额外复杂度没有收益，不采用。

## Tasks

### Task 1：Channel identity contract 与 runtime resolver

修改范围：

- `src/bazaar_compute_node/core/channel.py`
- `src/bazaar_compute_node/core/runtime.py`
- `src/bazaar_compute_node/app/agent.py`
- `src/bazaar_compute_node/contrib/codex/runtime.py`
- `src/bazaar_compute_node/contrib/wecom/channel.py`
- `src/bazaar_compute_node/contrib/telegram/channel.py`
- `tests/support/src/bcn_test_support/channel.py`
- 对应 core、app、Codex、Telegram、WeCom tests

实现：

1. 增加 `ChannelIdentity` 与 `IChannel.get_identity()`。
2. `AgentScopedChannel` 委托 identity。
3. unsupported adapter 返回 `None`；Telegram 基于已缓存 `getMe` 返回 identity。
4. `RuntimeCommandContext` 改为 `resolve_agent_name`；Codex 在新 session 建立时解析。
5. `AgentApplication` 明确实现 `name -> id -> config name` fallback。

验证：

- value object validation；
- Channel 未启动、ready、stop 后的 identity；
- name、ID、config 三层 fallback；
- developer instruction 首句使用 resolved name，Runtime Context 的稳定 `Agent ID` 不变；
- focused tests、Ruff format/check、相关文件 LSP/pyright。

完成实现与验证后保持一个 Task diff，停下 review；未获授权不 commit、不 push。

### Task 2：Telegram inbound speaker projection

修改范围：

- `src/bazaar_compute_node/contrib/telegram/channel.py`
- Telegram contrib/e2e tests
- 受 sender fixture 影响的 orchestration、SQLite 或 bcc tests

实现：

1. `Message.from` 使用 `username -> id`。
2. `sender_chat` 使用 `username -> id`。
3. current message 与 quoted message 共享同一投影。
4. 保留 numeric bot filtering、`sender_is_bot`、routing、activation 和 dedupe。

验证至少覆盖：

- human username 最终 header 为 `@realyuchanns`；
- other bot username 最终 header 为 `@bkaiBot`；
- username 缺失 fallback 到 numeric ID；
- `sender_chat` username 和 ID fallback；
- quoted/current message 表达一致；
- current bot 顶层消息仍被过滤；
- SQLite round-trip 保持接收时的 sender 显示值。

完成实现与验证后保持一个 Task diff，停下 review；未获授权不 commit、不 push。

### Task 3：任意可见用户完成 Telegram approval

修改范围：

- `src/bazaar_compute_node/core/channel.py`
- `src/bazaar_compute_node/core/orchestration/turn.py`
- `src/bazaar_compute_node/contrib/telegram/approval.py`
- approval 相关 core、orchestration、Telegram 和 e2e tests

实现：

1. 删除 approval request 的 sender authorization 字段与 plumbing。
2. 删除 Telegram pending expected sender 与 mismatch rejection。
3. 保留 route、prompt、token、pending/resolved 和 first-writer-wins 检查。

验证至少覆盖：

- 不同于消息发送者的用户可以 approve 或 reject；
- 不同 chat/topic/prompt 的 callback 仍拒绝；
- unknown、expired、duplicate token 语义不变；
- callback race 只产生一个 terminal decision；
- approval 仍回复到原 DM/group/topic。

完成 focused tests 后执行：

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run --with pyright pyright
uv lock --check
```

Telegram external e2e 在凭据存在时验证 `getMe` identity、username inbound 和 cross-user approval；凭据缺失时明确报告 skip，不把 skip 报告为通过。

最终检查：

- 无 schema 或 lock 变更；
- 无旧数据 compatibility 分支；
- provider identity 未进入稳定 `agent.id` ownership；
- sender 显示值未进入 routing 或 authorization；
- README、CHANGELOG 没有真实用户文档缺口时不制造改动。

完成实现与全量验证后保持一个 Task diff，停下 review；commit、push、PR、merge 和 branch 删除分别等待明确授权。

## 风险与控制

- 把 Telegram username 当稳定 ID：contract 分离 `id` 与 `name`，路由和过滤继续使用 numeric ID。
- runtime 在 Channel ready 前解析 identity：resolver 只在 runtime session startup 调用，orchestrator ready 前不接受 ingress；用 lifecycle test 固化该顺序。
- username 产生双 `@`：adapter 存原始 username，不添加 `@`，由 bcc renderer 统一展示。
- 开放 approval 扩大群内操作人范围：这是已确认产品语义；按钮只出现在原 conversation，并保留不可猜 token、严格 route/prompt correlation 与单次完成。
- 新旧消息 sender 显示不同：项目未上线且明确不处理旧数据，不引入 backfill 或 compatibility complexity。

## 完成标准

- Telegram Agent 的 developer instruction 首句优先使用 `getMe.username`，缺失时使用 `getMe.id`，再 fallback 到 config `agent.name`。
- Telegram inbound 中有 username 的人类和 bot 以 username 出现在 `@sender` header；无 username 时显示 ID。
- Channel core API 不包含 Telegram-specific 类型或命名。
- Approval 不依赖 inbound sender identity，任意能看到有效按钮的用户都可完成 pending request。
- 全量测试、Ruff、pyright、lock check 和相关 LSP diagnostics 通过。
- 每个 Task 串行完成并在 review 点停止；所有 Git 写操作遵守单独授权。
