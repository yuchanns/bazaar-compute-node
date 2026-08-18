# BCN Channel 身份协议与消息说话人显示

## 状态

- 当前阶段：Code，Task 3 implementation 与验证已完成，未提交 diff 等待 review。
- 工作分支：`f-20260818-channel-identity`。
- 基线：`main@1f5a24f13536c1bf5c6fc19b8ad7f2afd3e41aca`。
- Plan 已作为独立 commit `53d4e29` 推送；Task 1/2 已分别提交并推送为 `08f0dfe`/`810faff`，Task 3 尚未 commit 或 push。
- 每个 Task 串行开发；完成实现与验证后停下 review。commit 和 push 分别等待明确授权。
- 本计划不包含旧消息回填、schema migration、配置迁移、PR、merge、发布或部署。

## 目标

1. 增加 provider-neutral Channel identity contract，让 Channel 在启动后暴露当前 provider account 的 ID 和可选 name。
2. Channel bot 名称只按 `Channel identity name -> Channel identity id` 解析；Developer instruction 将它与 config `agent.name` 分离表达为 `You're {bot_name}, A.K.A {agent.name}`，稳定的 BCN `agent.id` 不变。
3. Telegram 复用现有 `getMe` 结果，同时提供 bot ID 和 username，不增加网络请求。
4. Telegram inbound message 的说话人优先显示 username，没有 username 时显示 numeric ID；人类、其他 bot 和 quoted message 使用相同规则。
5. Approval 仍发送到触发 turn 的 DM、group 或 topic，仅触发 turn 的 original sender 可以完成 approval。
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

Inbound sender identity
    transient provider sender id + durable display name
    separates authorization from runtime-visible presentation
```

### BCN Agent identity

- `agent.id` 继续是稳定 UUIDv7 identity。
- Channel account ID 不能替代 `agent.id`，不能参与 workspace、storage scope、session namespace、capability binding 或 durable ownership。
- `config.toml` 的 `agent.name` 继续用于配置唯一性、管理、health 和 outbound identity snapshot；本需求只改变 developer instruction 中的可读自称。

### Inbound sender identity

`InboundMessage.sender` 使用 `SenderIdentity(id, name)`：

- Telegram `id` 是当前内存周期内的 numeric provider sender ID，用于 approval authorization。
- `name` 是可选 username，只用于展示；缺失时显示 fallback 到 `id`。
- SQLite 只存现有 `sender TEXT` 显示值，不存可恢复授权语义的独立 ID。
- 写入后不随 Telegram 改名而更新，历史消息展示接收当时的名字。
- sender 不用于 DM/group/topic 路由、reply 或 dedupe；路由继续由 `provider_thread_id`、`provider_message_id` 和 `target_kind` 承担。
- username 不带 `@`；bcc header renderer 负责输出 `@sender`，避免 `@@name`。

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

当前 Channel identity 要到 `Channel.start()` 完成后才存在。Runtime context 同时保留固定 config name 和显式 bot name resolver：

```python
@dataclass(frozen=True, slots=True)
class RuntimeCommandContext:
    agent_name: str
    bot_name: Callable[[], str | None]
    agent_id: str
    # existing fields remain unchanged
```

`AgentApplication` 构造 bot resolver，按以下顺序选择：

```text
channel.get_identity().name
    -> channel.get_identity().id
    -> None
```

Codex runtime 仅在 `start_session()` 创建新 provider thread 时调用 resolver。identity 存在时，Developer instruction 首句为 `You're "{bot_name}", A.K.A "{agent.name}"`；unsupported Channel 没有 identity 时只使用 `You're "{agent.name}"`，不伪造 bot identity。

当前 orchestrator 虽先启动 Runtime lifecycle、后启动 Channel lifecycle，但只有二者都成功后才接受 ingress；因此第一次 `start_session()` 发生时 Channel identity 已 ready，不需要重构 async composition。已经创建的 Codex thread 不动态改名，避免同一 provider thread 的 developer instruction 漂移。

config name 和 bot resolver 返回值继续经过 `DeveloperInstructionContext` 的非空、无换行校验；异常沿现有 runtime session startup failure 路径返回，不静默 fallback 无效值。

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
    SenderIdentity(
        id=str(message.from.id),
        name=message.from.username or None,
    )
else if message.sender_chat is valid:
    SenderIdentity(
        id=str(message.sender_chat.id),
        name=message.sender_chat.username or None,
    )
else:
    None
```

细节：

- `from` 和 `sender_chat` 是互斥分支，不混合字段。
- username 保持 Telegram 返回的原始大小写，不带 `@`。
- 不使用 `first_name`、`last_name` 或 chat title 冒充稳定 handle。
- 当前消息与 quoted/replied message 复用同一 extraction。
- 顶层 current-bot filtering 继续比较 numeric bot ID，不使用可变 username。
- 移除无业务消费方的 `metadata.sender_is_bot`，避免保留无效的 provider metadata。
- message type、canonical target、provider route、mention activation 和 dedupe 语义不变。

## Approval 边界

`InboundMessage.sender` 改为 provider-neutral `SenderIdentity`：`id` 是当前内存周期内的稳定 provider sender ID，`name` 是可变的显示名。runtime header 使用 `name -> id`，approval authorization 只使用 `id`。

SQLite 仍只保存现有 `sender TEXT` 显示值 `name -> id`，不保存可用于授权的独立 sender ID。当 username 缺失时，numeric ID 会作为显示 fallback 写入该列，但从 SQLite 重读时它被解码为 `SenderIdentity(name=..., id=None)`，不能恢复授权语义。新收到的消息在 append 后继续使用同一个内存模型运行 turn，因此 live ID 能传递到 pending approval。Telegram pending token、expected sender ID 和 future 本来就只存在 Channel 内存中，stop/restart 后旧 approval 失效，因此不需要 sender ID persistence 或 schema migration。

Telegram callback 必须使用 `from.id` 匹配 pending approval 的 original sender ID。以下 correlation 全部保留：

```text
opaque random token
    + pending request id
    + chat id
    + topic id
    + approval prompt message id
    + unresolved future
```

第一个由 original sender 发出且通过全部 correlation 检查的 callback 完成 future；后续点击继续走 resolved/duplicate 语义。Approval prompt 和结果反馈仍发送到触发 turn 的原 DM、group 或 topic。

## 备选方案与取舍

### 方案 A：持久化 `sender_id + sender_name`

能跨重启恢复 sender authorization，但 Telegram approval 的 token/future 本身不持久化，重启后必须失效。因此引入 schema migration 没有业务收益，不采用。

### 方案 B：使用显示字符串限制审批

不需要 sender 模型变更，但 username 可变且无法与 callback numeric ID 稳定比较，会把展示字段误当授权主体，不采用。

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
4. `RuntimeCommandContext` 分离固定 config `agent_name` 与 callable `bot_name`；Codex 在新 session 建立时调用 resolver。
5. `AgentApplication` 明确实现 bot `name -> id -> None`，Developer instruction 将 bot name 与 config alias 分离。

验证：

- value object validation；
- Channel 未启动、ready、stop 后的 identity；
- bot name 的 name/ID fallback 与 unsupported identity `None`；
- developer instruction 首句分别表达 bot name 与 config alias，Runtime Context 的稳定 `Agent ID` 不变；
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
4. 移除无消费方的 `sender_is_bot` metadata，保留 numeric bot filtering、routing、activation 和 dedupe。

验证至少覆盖：

- human username 最终 header 为 `@realyuchanns`；
- other bot username 最终 header 为 `@bkaiBot`；
- username 缺失 fallback 到 numeric ID；
- `sender_chat` username 和 ID fallback；
- quoted/current message 表达一致；
- current bot 顶层消息仍被过滤；
- SQLite round-trip 保持接收时的 sender 显示值。

完成实现与验证后保持一个 Task diff，停下 review；未获授权不 commit、不 push。

### Task 3：分离 sender 显示与 Telegram approval 授权

修改范围：

- `src/bazaar_compute_node/core/channel.py`
- `src/bazaar_compute_node/core/models/entities.py`
- `src/bazaar_compute_node/core/orchestration/turn.py`
- `src/bazaar_compute_node/app/command.py`
- `src/bazaar_compute_node/bcc.py`
- `src/bazaar_compute_node/contrib/sqlite/codec.py`
- SQLite repositories（仅写入 display value，无 migration）
- `src/bazaar_compute_node/contrib/telegram/approval.py`
- approval 相关 core、orchestration、Telegram 和 e2e tests

实现：

1. 将 sender 收敛为 `SenderIdentity(id, name)`，header 使用 `name -> id`。
2. SQLite 仅持久化 display value，从库重读时 ID 为 `None`。
3. orchestration 只将 live `sender.id` 传给 approval request。
4. Telegram pending approval 必须有 expected sender ID，callback 继续拒绝 sender mismatch。
5. 保留 route、prompt、token、pending/resolved 和 first-writer-wins 检查。

验证至少覆盖：

- original sender 可以 approve 或 reject，其他用户仍拒绝；
- username 变化或展示值不影响 numeric ID authorization；
- SQLite round-trip 保留 display name 但不保留 sender ID；
- stop/restart 后 pending approval 失效；
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

Telegram external e2e 在凭据存在时验证 `getMe` identity、username inbound 和 original-user approval；凭据缺失时明确报告 skip，不把 skip 报告为通过。

最终检查：

- 无 schema 或 lock 变更；
- 无旧数据 compatibility 分支；
- provider identity 未进入稳定 `agent.id` ownership；
- sender display name 未进入 routing 或 authorization；
- SQLite 没有可恢复授权语义的 sender ID 字段；
- README、CHANGELOG 没有真实用户文档缺口时不制造改动。

完成实现与全量验证后保持一个 Task diff，停下 review；commit、push、PR、merge 和 branch 删除分别等待明确授权。

## 风险与控制

- 把 Telegram username 当稳定 ID：contract 分离 `id` 与 `name`，路由和过滤继续使用 numeric ID。
- runtime 在 Channel ready 前解析 identity：resolver 只在 runtime session startup 调用，orchestrator ready 前不接受 ingress；用 lifecycle test 固化该顺序。
- username 产生双 `@`：adapter 存原始 username，不添加 `@`，由 bcc renderer 统一展示。
- 可变 username 进入 authorization：通过 `SenderIdentity` 分离 ID/name，callback 只比较 numeric ID。
- 新旧消息 sender 显示不同：项目未上线且明确不处理旧数据，不引入 backfill 或 compatibility complexity。

## 完成标准

- Telegram Agent 的 developer instruction 首句使用 `getMe.username -> getMe.id` 作为 bot name，并用 `A.K.A` 独立表达 config `agent.name`。
- Telegram inbound 中有 username 的人类和 bot 以 username 出现在 `@sender` header；无 username 时显示 ID。
- Channel core API 不包含 Telegram-specific 类型或命名。
- Approval 仅允许发起 turn 的 original sender 完成，且不依赖可变 display name。
- 全量测试、Ruff、pyright、lock check 和相关 LSP diagnostics 通过。
- 每个 Task 串行完成并在 review 点停止；所有 Git 写操作遵守单独授权。
