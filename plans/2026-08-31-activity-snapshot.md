# Telegram / Lark 单项活动快照与用量总览

## 1. 现状

PR #63 `fix/runtime-event-review` 已合并，是本计划的实现基线。开始 Task 1 前，把本分支 rebase 到
最新 `main`；其 unbounded queue、orphan terminal event、Lark terminal drain、严格
Codex patch schema 与 Claude 安全计数均作为既有行为保留。

Lark 的单卡写入间隔常量当前为 0.1 秒，正好等于官方单卡 10 次/秒上限，没有安全余量，本计划改为
0.2 秒（5 次/秒，保留一半余量）。该常量不作为独立修正先行落地：现有 Lark 投影按每次变化各排一个
写操作，不做合并，一次工具调用产生两个写请求，单独调高间隔会使 turn 结束时 `_finish_turn` 等待的
队列排空时间同比例放大，超出 terminal drain 只分配到剩余 deadline 一半的预算。间隔调整因此与
Task 3 的合并层同时落地。

Core 已通过 `RuntimeOutputEvent` 提供本功能所需的全部中立事件：

- `ToolCallStarted`、`ToolCallCompleted`、`ToolCallFailed` 携带稳定的 `call.call_id` 与 `call.name`；
- `ContextCompactionStarted`、`ContextCompactionCompleted` 携带可选 `compaction_id`；
- `UsageUpdated.total` 携带累计 `input_tokens`、`cached_input_tokens`、`output_tokens` 等字段；
- turn 终态由 `TurnCompleted`、`TurnFailed`、`TurnCancelled`、`TurnUnknown` 表达。

Claude 与 Codex adapter 已产出 `UsageUpdated`。Codex 在 turn 中多次上报累计 total，Claude 在终态
result 上报 total；channel 只需保存最新一份 `UsageUpdated.total`，不对累计值再次求和。Core 与
runtime adapter 无需新增事件或字段。

Telegram 的 `TelegramActivityProjector` 当前按调用追加 row，并在 32 KiB 边界创建 continuation
message；Lark 的 `LarkActivityProjector` 当前按调用添加 CardKit element，并在卡片容量边界创建
continuation card。两者均保留完整活动历史，与本计划的单项快照体验不符。两端目前都忽略
`UsageUpdated` 的展示。

WeCom 的同一流式消息只能在首次发送后 10 分钟内刷新，无法在任意时长的 turn 上同时满足
“单气泡实时替换”与“终态完整总览”。因此 WeCom **不展示过程活动**，只在 turn 终态主动发送一张
总览消息：发出后不再修改，既不使用流式回复，也不使用 `response_url` 或任何卡片更新接口，上述
10 分钟与单次使用限制都与之无关。

总览走 `aibot_send_msg` + `msgtype=markdown`，与 BCN 现有审批卡是同一条主动发送路径。不使用
`text_notice` 模板卡，因为该类型要求必填 `card_action`，而 `card_action` 只支持跳转 URL 或小程序、
没有无动作选项；为渲染卡片而附加一个无意义的跳转不可接受。WeCom 现有正文与审批行为保持不变；
总览是该 turn 额外的一条消息，因此启用后每个 turn 最多为正文一条加总览一条。请求串行与 ACK 顺序由现有 `_send_lock` 保证为
“正文先、总览后”，但客户端可见渲染顺序官方未作承诺，只能由真实 E2E 观察。

飞书官方「[流式更新 OpenAPI 概述](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/streaming-updates-openapi-overview)」
规定单个 card entity 的卡片和组件级 OpenAPI 操作不超过 10 次/秒；现有卡片没有开启
`streaming_mode`，因此不使用该文档所述的 streaming-mode QPS 豁免。官方「[更新卡片实体的指定元素](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/cardkit-v1/card-element/update)」
还给出该接口的应用级频控 50 次/秒、1000 次/分钟，并要求稳定 `element_id`、新的幂等 `uuid`
与严格递增 `sequence`。

Telegram 没有公开 `editMessageText` 专属的固定频率数字。官方「[Bots FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)」
的通用建议是单个 chat 避免超过 1 条/秒，短时突发可能允许，持续超限会收到 429；官方
「[ResponseParameters](https://core.telegram.org/bots/api#responseparameters)」规定超过 flood control 时用 `retry_after`
返回重试前应等待的秒数。本计划因此让 Telegram 使用 1 秒写入间隔、Lark 使用 0.2 秒（两端各自
贴合平台上限，不强行统一），并保留
Telegram 现有 429 `retry_after` 处理，不声称存在 edit-specific 额度。

## 2. 展示契约

活动投影没有开关，三端始终启用。不保留 `[agent.channel] activity` 配置项，也不保留为它存在的
分支与 health 字段。

每个 turn 最多维护一个当前活动快照。收到可展示的生命周期事件后，整个可见内容替换为最新事件：

```text
⌛️ 工具调用 · /bin/bash
✅ 工具调用 · /bin/bash
❌ 工具调用 · /bin/bash
⌛️ 上下文压缩
✅ 上下文压缩
```

工具名沿用 runtime 原始展示名，并保留现有 UTF-8 长度限制与渠道转义。tool delta、patch、terminal
interaction、content delta 与 usage update 不替换当前活动行。高频生命周期事件先按到达顺序进入状态，
writer 可以在 provider 写入窗口内合并中间画面，但每次写出的内容始终是当时最新完整快照。

turn 终态把同一条可见内容替换为活动总览。总览只列非零值，中文顺序固定为：

```text
工具调用 3 次
上下文压缩 1 次
输入 12400
缓存命中 9100
输出 860
```

终态为失败或未知时，总览首行是错误原因，取自终态 payload 的 `error_message`，超过 1000 字符
在 reducer 建总览时截断，三端共用这一个上限；模板为 `activity.error`：

```text
⚠️ runtime turn failed: TimeoutError
工具调用 3 次
```

该行取代原先由 `RuntimeErrorReporter` 发出的独立错误消息。`core/orchestration/error_feedback.py`
连同 `runtime.error.failed`、`runtime.error.unknown` 两个 catalog 键一并移除，`SessionOrchestrator`
不再接收 `error_feedback_detail` 与 `translator`。原先由该路径承担的凭据脱敏改由
`Channel` 在出站事件上完成：`TurnFailed` 与 `TurnUnknown` 的 `error_message` 经注入的
`redact` 回调替换 session capability 与 `*_TOKEN` 取值后才交给渠道。

错误原因随快照进入活动载体，不再写入消息历史；`bcc message read` 不包含该文本。

多 runtime failover 沿用既有规则：只有耗尽全部 runtime 的那次失败对用户可见。`turn_id` 由
`client_user_message_id` 派生，在 failover 循环外只计算一次，各次尝试共用同一个活动载体。
`UsageUpdated` 的 total 是每次尝试各自累计的，因此 reducer 在收到新的 `TurnStarted` 时把当前
total 结算进已完成尝试的合计，总览是各次尝试之和。
`run_turn`、`resume_turn` 与 `finish_turn` 接收 `retry_available`，为真且终态为 `FAILED` 或
`UNKNOWN` 时不向渠道转发该终态——provider 自己的 `<runtime>.turn.failed` 与 bcn 的
`bcn.turn.failed` 同样拦下。活动载体因此保持存活，重试的事件继续进入同一个 reducer，
工具调用与 token 跨尝试累加，由最后一次尝试的终态封口。

provider 的 `TurnUnknown` 一律不转发：它在 reconciliation 得出结论之前只是暂定值，恢复成功时同一轮
会以 `TurnCompleted` 结束。reconciliation 结束且不再交给其他 runtime 时，编排器调用
`RuntimeTurnRunner.notify_terminal` 补发 `bcn.turn.unknown` 封口。

`Channel` 的 ID 命名空间转换在两个方向上对称：`accept_turn_event` 把 bcn session id 换回 provider
id，`send` 同样把 `ChannelSendRequest.session_id` 换回 provider id。渠道内部因此只见到一种 id,
飞书 `_activity_turns` 在 `accept_turn_event` 写入、在 `send` 查找才能命中。

英文 catalog 使用对应的 `Tool calls`、`Context compactions`、`Input`、`Cache hits`、`Output`。
容器标题继续使用既有 `activity.title`；Telegram 在 Markdown body 顶部显示标题，Lark 使用 card header。
数字使用十进制整数，不从 `total_tokens`、cache write、reasoning output 或 cost 派生
额外行。

两个 projector 现有的 `accept` 前置过滤只放行 `ToolCall*`、`ContextCompaction*` 与四种 turn 终态
payload，`UsageUpdated` 在入队前即被丢弃。实现必须把 `UsageUpdated` 加入该白名单，否则 token 总览
恒为空，且不产生任何错误或日志。

一轮在第一个可展示生命周期事件到来前保持 lazy。`UsageUpdated` 只更新内存中的最新累计值；如果该轮
没有工具或压缩事件，但终态时最新 total 中至少一个目标字段非零，则在终态直接创建一条已完成的总览。
工具、压缩与三个目标 token 值均为零或 `None` 时不创建活动载体。

## 3. 共享快照状态

新增 `core/activity.py`，只实现 provider-neutral 的纯内存 reducer 与展示数据，不执行网络
请求，也不改变 Core model。

状态按 `(session_id, turn_id)` 隔离，turn ID 继续取
`envelope.turn_id or envelope.provider_turn_id`。reducer 保存：

- 当前 `ActivitySnapshot(kind, status, name)`；
- 已见工具 `call_id` 集合；started、completed、failed 对同一 ID 只计一次，terminal orphan event
  也先登记该 ID，因此计数包含 runtime 只上报终态的调用；
- 已见非空 `compaction_id` 集合；
- anonymous compaction 的 open/closed 配对状态与计数。无 ID 的 started 在没有 open compaction 时计
  一次并打开，completed 在 open 时只关闭；没有 open 的 completed 作为一个 orphan compaction 计
  一次；
- 最新 `UsageUpdated.total`，后到者整体覆盖前一份；
- turn 是否已经终态。

`ToolCallStarted/Completed/Failed` 与 `ContextCompactionStarted/Completed` 每次都设置当前 snapshot，
即使该 ID 已经计数。turn 级终态生成不可变 `ActivityOverview`，随后该 turn 不再接收新的展示状态。
终态判定继续沿用现有 channel 语义：四种 terminal payload 的 `event_name` 包含 `turn`。

共享 renderer 从 snapshot 或 overview 加 i18n catalog 生成渠道无关的逻辑行；Telegram Markdown 转义与
Lark element JSON 仍由各自 adapter 负责。新增 catalog keys 明确包含 `count`
或 `tokens` 参数，英文和中文变量集合保持一致。

## 4. Telegram 投影

`contrib/telegram/activity.py` 把 page/row history 改为每 turn 一个 `message_id`、一个共享 reducer 与一个
single writer：

1. 第一个可展示 snapshot 到来时用 `send_rich_message` 创建一条消息；topic route 继续透传
   `message_thread_id`；
2. 后续 snapshot 用 `editMessageText` 完整替换同一 `message_id`；
3. 创建与后续编辑共享同一 cadence clock；同一 turn 的实际 provider 写入间隔至少 1 秒，
   窗口内的 lifecycle event 只替换最新
   desired snapshot，不增加 HTTP 请求；
4. terminal overview 替换 latest desired，等待已有请求与剩余 1 秒写入间隔后，必须写入
   最终内容；
5. terminal 时尚未创建消息但 overview 非空，则直接发送最终 overview；
6. 429 继续按真实 `retry_after` 最多重试三次，同一次更新失败时保留最新 dirty snapshot，下一次写入
   仍覆盖同一个 message；
7. channel stop 先取消并收集 activity writer，再关闭 Telegram API session。

单条内容远小于 Telegram rich Markdown 的 32 KiB 上限，因此 continuation page、page ordinal 与旧页
回写状态由单 `message_id` 状态取代。health 保留 sent/edited/failure/rate-limit 指标，并新增
coalesced update 计数。

## 5. Lark 投影

`contrib/lark/activity.py` 每 turn 只创建一个 CardKit card entity，并在初始 card body 中放入唯一、稳定
的 Markdown element，例如 `element_id = "activity"`。该 card 仍按原消息 route 通过
`reply_card` 出现在会话或 thread 中。

后续 snapshot 与 terminal overview 全部调用 `update_card_element` 覆盖这一个 element；不再新增 row
element 或 continuation card。single writer 保存一个 latest desired element：writer 正在等待 ACK 或
频控时，新事件只替换 desired value。每次实际写请求生成新的 `uuid`，`sequence` 只在前一次写入成功
后递增；未知或可重试结果沿用同一 operation identity，保持既有幂等语义。

创建 card 与后续 element update 共享同一 cadence clock；同一 card 的实际写入间隔至少 0.2 秒，
即 5 次/秒，为 10 次/秒的单卡官方上限保留一半余量；
全局 50 次/秒、1000 次/分钟 limiter 保留。
terminal overview 进入同一个 writer 并等待 card queue/drain 完成。PR #63 的 exact-turn terminal drain、
final delivery deadline 分配、session+turn degradation 与 terminal tracking retirement 继续保留；单卡失败
仍通过现有 `activity.final_incomplete` 让普通最终消息报告活动展示退化。

health 以 `activity_cards_created`、`activity_elements_updated`、failures、retries、coalesced updates 表达
单卡投影；append/continuation 专属指标和状态随历史行结构一并收敛。

## 6. 串行 Tasks

### Task 1：共享 reducer、总览与 i18n

- 把 `feature/activity-snapshot` rebase 到包含 PR #63 与任何独立 Lark cadence 修正的最新 `main`；
- 新增 `core/activity.py`，实现第 2、3 节的 snapshot、overview、unique counting、anonymous
  compaction pairing 与 latest-total semantics；
- 更新中英文 locale，加入总览 labels/templates 与 `activity.error`，移除 `runtime.error.*`；
- 移除 `core/orchestration/error_feedback.py` 及其接线，并把凭据脱敏迁到 `Channel`；
- 添加纯 reducer/renderer 测试，覆盖正常与 orphan tool lifecycle、具名与匿名 compaction、重复 ID、
  最新 total 覆盖、零值省略、usage-only terminal overview 与空 turn lazy；
- 运行 focused tests、Ruff、format、whole-root Pyright、compileall、lock 与 diff check，停下等待 review。

### Task 2：Telegram 单消息替换

- 按第 4 节重写 `contrib/telegram/activity.py` 的 turn state 与 writer；
- 更新现有 projector tests 为纯状态、render 与调度测试，明确证明创建/编辑共享 1 秒 cadence；
  provider send/edit、thread route、429 与最终
  overview 使用 Telegram 真实 provider E2E 验证；
- 真实 E2E 使用测试专用 config、临时数据库和隔离 `bcn run` 进程，通过真实 Telegram API 观察同一
  `message_id` 被持续编辑，验证实际写入间隔不小于 1 秒、usage-only terminal 与空 turn；
- 运行 focused tests 与全部静态门禁，停下等待 review。

### Task 3：Lark 单卡单元素替换

- 按第 5 节把 Lark history rows/cards 收敛为一个 card 与一个稳定 element；
- 保留 PR #63 terminal drain/degradation contract，并更新 health；
- 添加纯 reducer-to-element、writer coalescing、0.2 秒 cadence、sequence/uuid 与 terminal drain 测试；创建、回复、更新
  的 provider 行为通过真实 Lark E2E 验证，确认同一 `card_id`/`element_id` 原位变化、实际
  写入间隔不小于 0.2 秒且终态只有总览；
- 真实 E2E 使用测试专用 config、临时数据库与隔离进程，不复用或控制正式 `bcn.service`；
- 运行 focused tests、全量 non-E2E 与全部静态门禁，停下等待最终 review。

### Task 4：WeCom 终态总览消息

- 复用第 3 节的共享 reducer 累计工具、压缩与最新 `UsageUpdated.total`，WeCom 侧不维护任何过程展示
  状态，也不在过程中发送任何消息；
- WeCom 同样始终启用，没有开关；
- turn 终态且总览非空时，通过 `aibot_send_msg` + `msgtype=markdown` 主动发送一条总览消息；总览为
  空时不发送；发送后不再更新该卡；
- 总览进入现有 `_send_lock` 串行路径，位于正文之后；正文返回 UNKNOWN 时不因总览失败而改变正文
  既有语义；
- health 表达 WeCom 总览的发送次数与失败次数；
- 添加纯状态与渲染测试；发送路径、卡片结构与“正文先、总览后”的可见顺序由真实 WeCom E2E 验证；
- 运行 focused tests、全量 non-E2E 与全部静态门禁，停下等待 review。

## 7. 完成标准

1. Telegram 每 turn 最多一条 activity message，所有过程与终态都编辑同一 message ID；
2. Lark 每 turn 最多一张 activity card，所有过程与终态都更新同一 element ID；
3. 同一 turn 的实际 provider 写入间隔，Telegram 不小于 1 秒、Lark 不小于 0.2 秒；terminal overview 不跳过
   间隔，但也不能被合并丢弃；
4. 每个可展示 lifecycle event 都更新 latest snapshot；writer 合并时只丢弃已经被更新内容覆盖的中间
   画面，不丢失计数与最新 usage；
5. tool count 按唯一 call ID，compaction 按具名 ID 或匿名 lifecycle pairing；terminal orphan 计数；
6. token 总览读取最新 `UsageUpdated.total`，只展示 input、cached input、output 的非零值；
7. 三端均无 activity 开关，代码中不保留任何为该开关存在的分支；
8. channel stop 收集 writer/timer，terminal retirement 后不遗留 turn 或 task；
9. WeCom 每 turn 最多一条终态总览消息，过程中不产生任何 provider activity 请求；不使用流式回复、
   `response_url` 或卡片更新；总览为空时不发送；
10. Telegram、Lark 与 WeCom 真实 provider acceptance 与全部仓库门禁通过，每个 Task 分别 review 后才
    进入下一 Task。
