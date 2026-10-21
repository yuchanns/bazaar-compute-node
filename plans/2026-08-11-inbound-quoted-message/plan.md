# 统一入站消息引用计划

## 状态

- 分支：`research/wecom-quoted-message`
- 基线：`9e79e3719020f2023fb8f59aa877294ef36f572e`
- 当前阶段：Task 1–4 与外部消息统一去重均已实现；最终门禁和“不同问题重复引用同一句原文”的真实
  企业微信验收通过，等待最终 review。

## 问题与证据

用户在企业微信中引用一条消息后继续提问，Runtime 只能看到当前提问，看不到被引用内容。

当前 `InboundMessage.reply_to_provider_message_id` 只允许保存 Channel 的原生引用 ID；
`WeComChannel` 收到的 `body.quote` 却不是 ID，而是 `msgtype + text/image/mixed/voice/file` 内容快照。
adapter 目前只保存 `metadata["has_quote"] = True`，完整快照被丢弃；`bcc message check/read` 又不
输出 metadata，所以 Runtime 必然不可见。

生产 SQLite 的只读证据与代码一致：唯一一条真实引用消息只持久化了
`{"has_quote":true}`，`reply_to_provider_message_id` 为 `NULL`，引用正文已经不可恢复。

## 修订后的核心结论

引用必须只用 bcn 内部业务消息 ID 表达：

- 被引用对象首先是一条普通、可持久化、可读取的 `InboundMessage`。
- 当前消息只保存 `reply_to_message_id`，指向被引用消息的内部 `message_id`。
- Channel 只提供内容快照时，由具体 contrib 先把快照转换成一条普通入站消息，再让当前消息引用它。

core 不出现 quote、snapshot、WeCom payload 或 provider quote type。快照转换与稳定外部身份属于具体
contrib；core 只处理普通消息、内部消息关联与统一的外部消息去重。

## Core 契约

`InboundMessage` 使用内部关联：

```python
class InboundMessage:
    message_id: str
    reply_to_message_id: str | None = None
```

同时完成三个 provider-neutral 修正：

1. repository 保留 Channel 提交的内部 `message_id`，只分配 node-local `seq`，不再把
   `message_id` 替换成另一个 UUID。
2. `notifies_runtime` 成为有效输入；core 仍应用 DM/following/mention 规则，但不得把 contrib 明确标记
   为 non-notifying 的关联消息重新变成通知。
3. 允许 `sender=None`，因为某些 Channel 的引用快照没有发送者；不得伪造当前发送者或 provider
   身份。普通 provider inbound 仍必须传真实 sender。

保存当前消息前，repository 校验 `reply_to_message_id`：目标必须存在、属于同一 bcn session，且 seq
早于当前消息。这样跨会话引用、悬空引用和循环引用在事务内 fail closed。

所有 Channel 的外部消息统一用 `(channel, provider_thread_id, provider_message_id)` 标识。core 在建立
session mapping 前查询该身份，repository 用同一三元组的 unique index 作为持久约束；重投永远返回
首次 canonical 消息，不再次触发 Runtime。内部 `message_id` 只用于 bcn 关联，不参与外部去重。

## Contrib 边界

WeCom contrib：

1. 解析 `body.quote`，复用现有 `_content` 将 text/voice/mixed/image/file 规范化成普通 body 和
   terminal attachments。
2. 生成稳定的 adapter-scoped dedupe identity 与内部 `message_id`，先向现有 receive stream 投递一条
   `notifies_runtime=false` 的普通 `InboundMessage`。
3. 再投递用户当前消息，将 `reply_to_message_id` 设置为前一条消息的内部 ID。

core receive/orchestration 不知道第一条消息来自 quote。两条消息进入同一 conversation ingress queue，
顺序固定为引用原文在前、当前问题在后。

## 重试与崩溃边界

WeCom snapshot 没有 provider message ID，adapter 对规范化引用内容计算 SHA-256 fingerprint，并直接
将 fingerprint 作为快照的 `provider_message_id`。文本和语音使用规范化正文，mixed 保留内容顺序，
媒体使用解密后字节摘要。同一对话中，不同 callback 多次引用相同内容时得到同一个外部 identity；内部
`message_id` 再由 `(conversation, fingerprint)` 确定性派生。

两条消息分别以现有事务落库：

- 快照落库后、当前消息落库前发生 crash：provider 重投后，core 用稳定三元组命中首次 canonical
  快照，随后当前消息正常落库，不产生重复快照。
- 当前消息已经落库：core 对快照和当前消息分别命中首次 canonical 行，不产生新通知。
- ingress queue 保证同 conversation 顺序；repository 的内部引用校验阻止当前消息越过快照落库。

这避免让 WeCom contrib 获得 storage 写权限或让 core 新增 provider-specific batch 类型。

## SQLite migration

`inbound_messages` 的引用列改为内部关联：

```sql
reply_to_message_id TEXT
```

正式 migration 将可解析的旧 `reply_to_provider_message_id` 通过同 Channel 的
`provider_message_id` 映射为内部 `message_id`，无法解析的旧值置空，然后移除旧列并建立
`reply_to_message_id` index。项目尚未发布，不保留双读、fallback 或兼容字段。

v7 将外部消息 identity index 改为
`UNIQUE(channel, provider_thread_id, provider_message_id)`，与 core 查询使用完全相同的键。

引用原文使用现有 `inbound_messages` 与 `inbound_attachments`，不新增 quoted message/attachment
表。attachment reconciliation、历史读取和 restart 恢复自然复用当前实现。

## Runtime 可见契约

每条 canonical message 输出内部关联：

```json
{
  "message_id": "current-message-id",
  "reply_to_message_id": "referenced-message-id",
  "body": "current question"
}
```

`bcc message check/read` 同时返回本次结果直接引用、但未因 non-notifying 进入 unread messages 的普通
消息，放在顶层 `referenced_messages` 数组并按 `message_id` 去重。这样 Runtime 在一次 check 中即可
看到引用原文，serializer 仍保持消息为扁平实体，不递归内嵌 provider 快照。多级历史引用可继续用
`bcc message read --around <message-id>` 读取，不递归展开无界引用图。

## 串行任务

### Task 1：内部消息引用 contract

- 将 inbound 引用改为 `reply_to_message_id`，repository 保留内部 `message_id`。
- 加入同 session、已存在、先于当前消息的引用约束。
- 修正 `notifies_runtime` 与 optional sender 的 provider-neutral 语义。
- 完成 SQLite v5 migration、codec、repository 和 test storage。
- 用真实 SQLite 文件验证 migration、round-trip、dedupe、悬空/跨 session 拒绝与 quick check。
- 完成后停止，等待 review。

### Task 2：WeCom contrib 映射

- 在 contrib 内将 quote 快照转换为普通 non-notifying `InboundMessage`。
- 为快照建立稳定 identity，按引用原文、当前消息的顺序投递。
- 映射 text/voice/mixed/image/file，复用现有媒体下载、AES 解密、大小限制和物化路径。
- 删除 `metadata.has_quote`；媒体 URL、AES key 和原始 quote payload 不落库、不进日志。
- 完成后停止，等待 review。

### Task 3：统一外部去重与 bcc output

- 删除 Channel 的查库/seen set；core/repository 按外部消息三元组统一去重。
- SQLite v7 用同一三元组建立 unique index，并验证同一 provider ID 在不同对话中互不冲突。
- `check/read` 输出 `reply_to_message_id` 和去重后的 `referenced_messages`。
- 保持 cursor、fresh-check、notice 与 outbound reply 行为不变。
- 验证普通消息、引用消息、跨 restart read 与直接 history around。
- 完成后停止，等待 review。

### Task 4：完整验证与 live acceptance

- 运行 Ruff、Pyright、相关文件 LSP、compileall、全量 tests、lock 与 diff 检查。
- 保留原 SQLite 验证 v4 到 v5 migration 与 restart。
- 经明确部署授权后，在真实 WeCom 中引用一条文本消息自然提问；确认引用原文和当前问题分别落库、
  内部 ID 关联正确、Runtime 一次 check 可见原文、outbound 成功。
- 完成后停止，等待最终 review；不自动 commit、push 或部署。

## 风险与控制

- 最大风险是快照消息与当前消息之间发生 crash；稳定 identity、同 conversation 顺序和 repository
  引用约束共同保证重试收敛。
- 引用媒体仍必须在五分钟 URL 过期前由 WeCom contrib 物化；core 只接收 terminal attachment
  descriptor。
- non-notifying 快照不得单独触发 Runtime，但必须可以被 history/read 和 attachment reconciliation
  正常访问。
- 日志只记录关联状态和内部 ID，不记录引用正文、媒体 URL、AES key 或原始 payload。
