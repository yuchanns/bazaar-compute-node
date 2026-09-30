# 智能体消息搜索与历史定位

## 背景

`bcc message read` 适合在已知会话和消息位置后读取上下文。用户问「之前怎么决定的」时，智能体还需要先从自己
拥有的会话中找出相关消息。BCS 的联系人与聊天记录也需要同一能力：搜索选中智能体的全部会话，点开结果后定位到原消息。

消息正文已经保存在节点 SQLite 的 `messages.body` 中；存储 scope 固定绑定一个 agent，下行通道可以按选中的
agent 调用它的命令服务。搜索在这个结构内增加一项读取能力，CLI 与 BCS 共用节点查询。

## 目标

- `bcc message search` 支持关键词、会话、发送者、时间、排序和分页，返回能直接用于历史读取的消息位置。
- `session` 模式的搜索与历史读取固定在当前会话，命令和 runtime 输出沿用公共 target 格式。
- `dangerous_individual` 模式与 BCS 支持搜索选中 agent 自己拥有的全部会话，目标过滤落到一个独立会话。
- 智能体通过搜索找到历史线索，再用 `message read --around` 读取原讨论，引用原消息作答。
- BCS 在聊天标题栏右侧提供搜索图标与 Ctrl+F 入口，打开 Raycast 式悬浮搜索；输入后展示可滚动的结果，点开后定位原消息。
- 搜索、筛选与自绘日历沿用网站现有样式和 i18n；定位原消息后短暂亮起，再恢复普通消息样式。
- 页面订阅刷新保留搜索条件、已加载结果与滚动位置；定位历史消息后持续保留正在阅读的上下文。
- 手机支持触摸搜索、筛选、日历与原消息定位，软键盘、横竖屏及安全区变化后仍可操作并保留状态。

## 设计

### 所有权与读取范围

搜索请求沿用当前 runtime 的会话绑定校验。`CommandDispatcher` 从绑定关系取得命令服务，服务持有的
`IStorageScope` 决定 agent；CLI 入参不提供 agent 选择。SQLite 查询继续使用 `/*agent_id*/?` 的绑定约定。

所有结果、预览、目标名称、分页判断都由同一 agent scope 内的数据生成。所有权、review 状态、消息可见状态和用户过滤
必须进入 SQL 的 `WHERE`，然后才排序和 `LIMIT/OFFSET`，不能先从节点全库截一页再过滤。即使同一外部群出现在多个
agent 的存储中，也只返回当前 agent 的记录。

- CLI 的搜索与历史读取只读 `approved` 会话。已 unfollow 的会话仍可搜索已保存的历史；follow 控制通知，review 控制可读性。
- BCS 按已有 `agents.view`、computer/agent 可见性检查选择一个 agent。节点 control 在该 agent scope 内查询，
  操作者按现有历史读取规则可查看各 review 状态的会话；搜索请求中的 review 过滤由 control 边界提供。
- target 过滤使用现有 `resolve_inbox_target`，返回一个已保存、属于该 agent 的会话。
  使用结果里的精确 target；不根据群名展开其他会话，每个 thread 独立查询。

`session` 模式从已经验证的 runtime binding 取得 `Thread` actor，搜索 SQL 始终包含该 `thread_id`。
搜索提供与其他模式相同的 `--target` 参数；传入时按现有 target resolver 解析并核对当前会话边界，
传入其他会话的 target 沿用现有 read/send 的 `INVALID_COMMAND` 错误。省略 search target 时搜索当前会话。
read、send 与 thread unfollow 继续沿用现有的必填 target 参数及 `_require_in_reach` 会话边界。
query、sender、时间、排序与分页参数按各自语义校验。`dangerous_individual` 的 `Agent` actor 在自身 storage scope
内搜索和读取，省略 search target 时查询全部可读会话。两种模式都由服务端 actor 决定范围。

命令结果、消息头、inbox notice、回执、草稿与 reminder 使用现有公共格式，包含已有 target 与来源坐标。
两种模式使用相同的消息操作提示词、命令参数与调用示例，结果里的精确 target 可直接交给历史读取。

搜索只产生工具审计，不消费未读、不移动 consumer cursor，也不调用 `_observe_freshness`。
历史读取沿用当前 actor 可答复范围与 `_observe_freshness` 的现有规则。

### CLI

```text
bcc message search [--query <text>] [--target <exact-target>]
                   [--sender <@handle-or-id|self>]
                   [--after <time>] [--before <time>]
                   [--sort time|relevance] [--limit <n>] [--offset <n>]
```

| 参数 | 语义 |
| --- | --- |
| `--query` | 正文查询，去除首尾空白后按空白分成搜索片段，同一条消息必须包含全部片段。 |
| `--target` | `dangerous_individual` 模式限定一个精确会话，省略时查当前 agent 的全部可读会话；`session` 模式只接受当前会话的 target，省略时查当前会话。 |
| `--sender` | `@` 后是消息头显示的 handle；provider 没有 handle 时使用显示在该位置的 sender id。按完整值匹配。`self` 选择当前 agent 的出站消息。 |
| `--after` | 消息时间下界，包含该时刻。 |
| `--before` | 消息时间上界，包含该时刻。 |
| `--sort` | 默认 `time`，消息时间倒序；`relevance` 优先相关度，再按消息时间倒序。 |
| `--limit` | 默认 20，允许 1–50；超过上限在输入边界报参数错误。 |
| `--offset` | 默认 0，非负整数；在已过滤、已排序的结果中跳过这些项。 |

`session` 模式自带当前会话筛选，可省略 query 和其他过滤来列出本会话消息；`dangerous_individual` 模式的
`query/target/sender/after/before` 至少有一项有效筛选。省略 query 时按其他过滤列消息；空白 query 视为未提供。
发送者 handle 的英文大小写不敏感，opaque sender id 按完整值匹配，display name 用于展示。
这个筛选直接作用于该 agent 的消息行，不通过「找到最后一个同名发送者」把范围固定到某个 channel。

时间接受 ISO 8601 日期、日期时间和带时区的日期时间。没有时区时使用 CLI 所在电脑的本地时区；日期作为 after
取当天开始，作为 before 取当天结束。CLI 转成 epoch 毫秒传给节点；BCS 使用当前页面时区做相同转换。
节点边界校验 `after_ms <= before_ms`，查询和排序统一使用
`COALESCE(provider_time_ms, received_at_ms, created_at_ms)`，以 `seq DESC` 作为稳定的次级顺序。

`message read/send` 与 `thread unfollow` 的 `--target` 继续为必填，沿用原有目标解析和会话范围检查。
搜索的 `--target` 对所有模式提供相同参数；session 按当前会话边界检查，dangerous_individual 可选择其自有会话。

两种模式使用相同调用格式：

```bash
bcc message search --query '发布计划'
bcc message search --query '部署 staging' --target '<exact-target>'
bcc message search --sender '@yuchanns' --after '2026-09-01' --before '2026-09-30'
bcc message search --query 'SQLite' --sort relevance --limit 20 --offset 20
bcc message read --target '<target-from-result>' --around '<message-id-from-result>' --limit 50
```

请求里的 query 是普通文本。引号、百分号、下划线和 FTS 运算符都按字面内容搜索；每个长片段转义成一个 FTS phrase，
片段之间由服务组合 AND。未命中返回空结果，由调用者修改关键词。

### 匹配与 SQLite FTS5

采用 FTS5 的 **trigram tokenizer + 短片段子串过滤**。这是中文、英文混用的聊天搜索，要求「部署计划」能命中
连续正文片段。`unicode61` 把连续 Unicode 字母作为 token，不能直接提供这种中文片段匹配；trigram 支持子串匹配，
但 `MATCH` 中少于三个 Unicode 字符的片段没有结果。这两个行为已用隔离 SQLite 实测。

依据：[SQLite FTS5 — Trigram tokenizer](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)、
[External content tables](https://www.sqlite.org/fts5.html#external_content_tables)。

查询分为三种：

1. 存在至少三个 Unicode 字符的片段：把这些片段组成 AND 的 `MATCH`，与 `messages` 按 `seq = rowid` 联结；
   再对一到两个字符的片段加 `instr(lower(message.body), lower(?)) > 0`。
2. 全部片段只有一到两个字符：在该 agent 的可读消息范围内对子串做同样的 `instr` 过滤。
3. 只有结构过滤：直接查 `messages`，应用 target、sender 和时间条件。

例如「部署 staging」先用 `staging` 的索引取得候选，再筛选「部署」；「部署」单独查询也能命中。
长片段按 trigram 默认大小写规则匹配，短片段使用 SQLite `lower`，英文 ASCII 大小写不敏感。
这提供连续字符匹配，关键词可由调用者用空白拆分；实现沿用 SQLite 内建能力。

时间排序始终按上述时间字段与 seq。相关度排序由 FTS 取得候选，再对查询长片段按 BM25 公式评分，
使用 k1=1.2、b=0.75：词频取正文中该字面片段的非重叠出现次数，正文长度取 Unicode 字符数。
文档数、平均长度和每个片段的文档频率只统计当前 agent 可读且符合会话、发送者和时间筛选的消息，
在同一个 reader snapshot 中完成统计和候选评分；得分倒序后以时间、seq 倒序打破平局。
短片段作为必须满足的过滤。只有短片段或结构过滤时按时间排序，并在返回的 `sort` 中标明 `time`；
结果不给调用者内部评分。

所有条件在排序、分页前一起执行。每次读取 `limit + 1` 条判断 `has_more`，展示 limit 条；`next_offset` 为
`offset + shown`，没有更多时为空。每页使用一个 reader snapshot。offset 是实时结果的偏移；会话有新消息时，
后续页位置可能移动，指定 before 可固定新增消息的时间边界。

新增 `v32_message_search.py` 并在 migration registry 中注册：

```sql
CREATE VIRTUAL TABLE message_search USING fts5(
    body,
    content = 'messages',
    content_rowid = 'seq',
    tokenize = 'trigram'
);

CREATE INDEX idx_messages_agent_search_time
ON messages (
    agent_id,
    COALESCE(provider_time_ms, received_at_ms, created_at_ms),
    seq
);
```

`messages.seq` 已是全库唯一的正整数，用作 FTS rowid。external content 直接读取 `messages.body`，正文仍只有一份。
同一个 migration 创建三项触发器：

- `AFTER INSERT`：向 FTS 插入 `new.seq/new.body`。
- `AFTER DELETE`：用 FTS `delete` command 删除 `old.seq/old.body`。
- `AFTER UPDATE OF body, seq`：先删除旧索引，再插入新索引；delivery state 更新由查询过滤生效。

触发器与消息写入处在同一事务。创建触发器后执行一次 `INSERT INTO message_search(message_search) VALUES('rebuild')`
索引已保存正文；整个 migration 成功后再提供查询。已发布的 migrations 保持原样。隔离 SQLite 已验证 rebuild、
插入、修改、删除、事务回滚与 external content 完整性检查。

搜索加入 storage 的 `_READ_OPERATIONS` 与 `_SNAPSHOT_READ_OPERATIONS`，目标解析、命中读取及 target projection
在同一个 reader 事务中完成。短片段查询使用 agent/time 索引缩小扫描范围。
搜索 SQL 使用五秒执行预算，在占用的 reader connection 上设置 progress handler，中断后清除 handler 再归还连接，
返回 `SEARCH_TIMEOUT` 和缩小关键词、时间范围的提示；全会话搜索可另提示指定 target。`SqliteSession` 提供这一连接级查询预算入口，
生产查询与隔离验收共用；超时涵盖 SQL 执行，外层取消仍按已有 reader transaction 的 interrupt/rollback 处理。
SQLite 缺少 FTS5/trigram 能力时 migration 明确报告依赖缺失。

可搜索集合与历史读取一致：入站消息，以及 `queued/sent` 出站消息；system 消息也是已有会话历史的一部分。
索引内容是保存的正文，包含 Markdown 文本和代码；附件通过原消息读取查看。

### 命令服务与返回值

在 `core/command.py` 定义 `MessageSearchRequest/MessageSearchHit/MessageSearchResult` 和
`ICommandService.search_messages`，在 `core/storage.py` 增加 scoped search 读取接口。
SQLite repository 承担条件、FTS、排序与分页；命令服务组合 target projection、actor 坐标并写 `bcc.message.search` 审计。

`app/command.py` 新增 `_MessageSearchRequest` 与 dispatch 分支；Pydantic 在本地命令和 control 输入边界校验
查询、时间、limit、offset、sort。CLI 在 `cmd/bcc/message.py` 注册命令，`_format.py` 与
`resources/bcc/search.tpl` 负责人类可读输出。请求仍包含已绑定的 `actor_id`。
`_MessageSearchRequest` 的 target 为可选，read/send/unfollow 请求沿用现有必填参数。服务端根据已验证 actor 处理目标：session
按现有规则解析 target 并检查当前会话边界，dangerous_individual 按各命令的目标规则解析。control 仍以选中 agent 的读取入口查询。

两种模式的命令与 BCS control 使用相同返回形状：

```json
{
  "query": "部署 staging",
  "sort": "time",
  "shown": 1,
  "offset": 0,
  "has_more": false,
  "next_offset": null,
  "messages": [{
    "message_id": "<local-message-id>",
    "thread_id": "<owned-thread-id>",
    "actor_id": "<actor-for-source-thread>",
    "target": "<exact-display-target>",
    "canonical_target": "<canonical-target>",
    "channel": "<channel-name>",
    "target_kind": "group",
    "direction": "inbound",
    "sender": {"id": "<provider-id>", "name": "<handle>", "display_name": "<name>"},
    "sender_kind": "human",
    "at_ms": 1790755200000,
    "snippet": "部署计划发布到 staging 环境。",
    "parts": [["", false], ["部署", true], ["计划发布到 ", false], ["staging", true], [" 环境。", false]]
  }]
}
```

搜索序列化复用 `core/serialize.py` 的 sender、target 与消息身份语义，节点统一返回最多 240 个 Unicode
字符的纯文本 `snippet` 和普通文字／命中片段 `parts`，CLI 与 BCS 直接渲染同一份视图。
snippet 围绕最早出现的查询片段截取，空白折叠，前后截断时显示省略号；只有结构过滤时取开头。
命中片段在节点按查询片段大小写不敏感地划分。命令与 control 的 Pydantic 请求边界只取前 5 个空白分隔的
查询片段，超出的忽略；BCS 页面查询采用相同规则。目标名称来自已有 target projection，actor 由
`Actors.for_thread(thread_id)` 取得。历史读取使用 `bcc message read --target '<target>' --around '<message_id>'`。

CLI 采用既有 agent 搜索展示结构，由 `cmd/bcc/_format.py` 与 `resources/bcc/search.tpl` 实现：

- 有关键词时使用 `Search results for: "<query>" (<n> result/results)`；仅筛选时使用
  `Filtered message results (<n> result/results)`。每项用 `<result ref="msg:<完整消息 ID>">` 分隔。
- 每项列出 `Source`、`Sender` 与 `Time`。Source 使用可直接读取的精确 target，并转义原文中的结构标签；
  outbound 的 Sender 显示 `self`，其他 Sender 是 handle 或 sender id
  加发送者类型；Time 是 CLI 本地时间并包含 `+08:00` 形式的时区偏移。两种模式采用同一格式。
- 摘要放在 `<preview>` 中，直接按节点返回的 `parts` 用 `<match>…</match>` 标记匹配，截断使用节点的省略号。
- 将正文预览中的 `@handle`、`#channel`、`dm:@handle` 或 `task #123` 等
  字面引用改为 `user:handle`、`channel:channel`、`dm:user:handle` 与 `task:123`。
  原文内的 result/preview/match 标签与 `<omit />` 转为转义文本，区分原文和 formatter 标记。
- 有结果时末尾保留原有上下文读取提示；分页行显示 shown、offset、实际 sort、has_more 与可用的 next_offset。
  零命中返回 `No search results.` 和相同分页行。参数错误沿用现有错误格式；不可读或超出 actor 会话范围的
  target 沿用现有 `INVALID_COMMAND`。工具审计记录实际生效的筛选和展示数量，中英文活动文案使用现有 catalog。

预期输出（示例编号与 target 为占位值），关键词搜索：

```text
Search results for: "deploy" (1 result)

<result ref="msg:00000000-0000-4000-8000-000000000001">
Source: #engineering:11111111-1111-4111-8111-111111111111
Sender: alice (human)
Time: 2026-04-21 15:00:00 +08:00

<preview>
we should <match>deploy</match> the fix today
</preview>
</result>

If a result may be relevant but its preview is not enough, read the surrounding context for that result before answering.

Search: shown=1 offset=0 sort=time has_more=false
```

仅结构筛选的预期输出：

```text
Filtered message results (1 result)

<result ref="msg:00000000-0000-4000-8000-000000000001">
Source: #engineering:11111111-1111-4111-8111-111111111111
Sender: alice (human)
Time: 2026-04-21 15:00:00 +08:00

<preview>
we should deploy the fix today
</preview>
</result>

If a result may be relevant but its preview is not enough, read the surrounding context for that result before answering.

Search: shown=1 offset=0 sort=time has_more=false
```

长正文截断时，preview 使用如下标记，完整结果块与上述结构相同：

```text
<preview>
…此前的讨论正文，最后确认 <match>deploy</match> 发布方案，后续讨论正文。…
</preview>
```

零命中：

```text
No search results.

Search: shown=0 offset=0 sort=time has_more=false
```

### instruction.md 的具体修改

实际源模板是 `src/bazaar_compute_node/resources/developer_instructions.md`，由 `core/instruction.py` 渲染。
在这个模板直接修改，利用已有的 `mode` 模板变量渲染对应模式的提示词。

**位置一：`## Communication — bcc CLI ONLY` 的 Messages 命令列表。** 将第一项替换为：

```markdown
1. **Messages** — `bcc message check`, `bcc message send`, `bcc message read`, `bcc message search`.
```

**位置二：`### Reading history` 的 read 用法段落。** 两种模式使用同一段描述和命令示例，
`--limit` 的现有说明继续保留。直接替换为：

```markdown
`bcc message read --target "<exact-target>"`

To jump directly to a specific hit with nearby context, use `bcc message read --target "..." --around "messageId"`.
Use `--limit <n>` to bound the history window.
```

**位置三：`### Historical references`。** 两种模式使用同一段历史检索要求，直接替换为：

```markdown
When a user refers to prior bcn discussion and the relevant context is not already available, first use `bcc message search` and `bcc message read` to find the original thread, decision, or owner before answering. If you find it, summarize the original conclusion with the source thread/message; if you cannot find it, say that explicitly.

When you cite a retrieved discussion, include the original message ID from the search/read result.
```

搜索范围和 target 的会话边界由命令服务实施；其余消息操作说明、命令示例和 runtime 消息格式沿用现有内容。

### BCS 搜索与跳转

界面依据为原讨论最终采纳的五张图：`v2-bold-keywords/01-悬浮搜索.png`、
`v2-bold-keywords/02-定位原消息.png`、`04-会话筛选.png`、`05-发送者筛选.png`、
`08-日历与搜索框.png`。2026-09-30 的最终确认消息为
`1055c42f-b478-5aa7-9ca9-17b9ecc4750a`；2026-10-01 的修正授权为
`1b683451-c1a9-531e-bb8d-f8757eab8a75`。原消息按后续确认短暂高亮后恢复普通样式。

#### 入口与悬浮结果

在 `templates/chat.html` 的聊天标题栏右侧加入搜索图标，点击或按 Ctrl+F 打开 Raycast 式悬浮搜索。
桌面快捷键提示位于图标左侧，定位提示同处标题栏右侧；手机返回按钮在左，搜索按钮在第一行右侧，渠道信息在第二行。
入口与当前选中 agent 绑定，有选中会话时默认搜索当前会话，未选中会话时默认搜索该 agent 的全部会话。
切换聊天会话后首次打开搜索更新默认会话；同一会话内关闭重开保留用户手动选择的全部会话或其他筛选。
桌面使用居中、靠上的弹层，搜索框在上、结果列表在下；手机使用同一结构并适应可用宽高。
桌面弹层宽 680px、顶距 120px、圆角 8px，首行包含搜索图标、输入框与关闭按钮。
当前 agent 的头像和名称位于第二行，与会话、发送者、时间筛选并列；排序位于该行右侧。
在 `templates/agents.html` 放置搜索弹层，使用现有 Alpine dialog、`x-trap.inert.noscroll` 与 HTMX 片段更新方式，
弹层打开后聚焦输入框，关闭后焦点返回搜索入口。

关键词输入采用 300ms debounce 自动发送下行查询；更改会话、发送者、时间或排序后从 offset 0 查询。
前端记录当前查询与筛选的请求版本，结果只应用到对应状态。结果列表在弹层内独立滚动，靠近底部时按
`has_more/next_offset` 加载下一页并追加；请求进行中保持已有结果与滚动位置。
搜索框下方依次展示当前 agent、会话、发送者和时间筛选，排序使用节点的 `time/relevance` 语义，默认时间倒序。
结果区展示已加载数量，并提供清除筛选；关键词与结构筛选都为空时显示输入提示。

每项结果显示来源会话、发送者、时间和正文片段。来源行左侧为会话头像和名称，右侧为日期、时间与当前选中项的
跳转箭头；正文和下方发送者名称与来源文字对齐。正文使用节点返回的纯文本 snippet，列表最多显示两行，
截断处保留省略号；关键词按查询片段加粗并使用网站深色正文色，片段其余文字使用 `--muted`。
BCS 直接渲染节点返回的 `parts`，由 Jinja 自动转义并在命中片段上使用 `<strong>`；关键词背景保持透明。
搜索输入框内的上下方向键选择结果，Enter 打开当前结果；其他控件的键盘事件由对应控件处理。
弹层底部显示方向键选择、Enter 打开记录和 Esc 关闭提示；桌面聊天标题栏显示 Ctrl+F 提示。
Esc 依次关闭日历、筛选面板、搜索弹层；点击弹层外侧也可关闭。

#### 订阅刷新与搜索状态

现有 `static/poll.js` 每轮检查结束后等待 5 秒再次检查订阅变化，有变化才读取对应页面片段。
`templates/contacts.html` 对 `#contacts` 使用 `outerMorph`；`templates/history_end.html` 对 `#tail`
使用 `outerHTML` 追加历史，空记录或读取失败后的恢复路径会整体替换 `#history`。
搜索弹层放在这些更新区域之外，搜索响应只更新独立的结果容器，不作为联系人或聊天历史的订阅刷新目标。
联系人、消息和在线状态订阅继续更新各自区域，搜索打开期间也照常接收新消息。

在 `static/components.js` 注册页面生命周期内的 Alpine search store，以 `computer_id/agent_id` 为键保存
关键词、已应用筛选、排序、已加载结果、分页游标、当前选择和结果滚动位置；弹层、筛选及日历的展开状态与
未应用日期草稿也由该状态持有。替换联系人或聊天片段不重建搜索状态；同一 agent 内会话导航使页面片段重建时，
从 store 恢复搜索视图。切换 agent 使用对应状态，返回原 agent 可恢复原搜索。

每次搜索请求携带对应的 computer/agent 与查询版本，收到响应后先核对当前状态，再更新结果容器。
关键词或筛选变化、清除操作及 agent 切换时取消失效请求；较早的响应不覆盖当前结果。
订阅刷新本身不清空、重排或重新查询搜索结果，输入焦点、键盘选择、筛选草稿与滚动位置持续保留。
修改关键词、筛选或排序才重新查询；滚动加载只追加下一页，重新打开弹层沿用已保存状态。
搜索结果使用虚拟列表：store 缓存已加载结果与逐项高度，累计高度用于二分定位视口，
只挂载可见区域及前后一屏缓冲的结果，前后用占位高度保持滚动范围。
翻页保留当前窗口内的旧节点，ResizeObserver 测量高度并按消息锚点恢复位置；键盘选择使用全局结果序号，
跨窗口时先滚动并挂载目标。重建页面或重开弹层恢复原结果窗口与阅读锚点。

#### 筛选与日历

筛选面板锚定对应按钮下方，覆盖结果列表；打开选项与日历时保持搜索框、结果区域和弹层几何位置。
会话和发送者浮层宽 260px，选项显示头像、名称、当前已加载结果计数和选中标记；计数直接从结果收集。
会话筛选展开当前 agent 的会话列表，包含“全部会话”，选择一个具体项后使用其精确 target 查询。
会话选项沿用现有 `read="contacts"` 的分页与 target projection，每次重开会话筛选时重新读取首屏，
后续按需分页。发送者展开“全部发送者”和当前已加载
搜索结果中的发送者，沿用聊天页从已加载消息收集成员的方式：按 handle/id/self token 去重，名称取结果中的
显示身份，继续加载搜索结果时补充选项。新查询首批结果替换选项，空结果清空选项；关闭重开及 agent 切换
与搜索结果一起恢复。当前 agent 只有出站消息出现在已加载结果时才作为 `self` 选项。
选择发送者后沿用节点搜索的 sender 条件，与 CLI 的 handle/id/self 语义一致。会话、发送者、时间三项可组合，
已选项显示网站浅粉选中态。

时间筛选提供“不限时间”“今天”“最近 7 天”及自定义起止日期。“最近 7 天”包含今天及之前六个本地日历日；
日期范围包含开始和结束当天，按当前页面时区转为节点的 `after_ms/before_ms`，沿用 CLI 相同的包含边界语义。
开始与结束日期并排显示；点击日期框后，在同一时间筛选面板内展开自绘日历。
时间浮层宽 304px，顶部并排显示三项快捷范围，下方为起止日期、日历和今天/取消/应用按钮；月份左对齐，翻月按钮在右侧。
日历提供月份前后切换、星期标题、可选日期、今天标识与范围选中态；选完开始日期继续选择结束日期，
应用按钮提交筛选，取消恢复打开面板前的日期。结束日期早于开始日期时调整另一端，应用时仍校验有效范围。

日历及搜索控件直接使用 `static/app.css` 的字体与 `--panel/--float/--fg/--muted/--line/--edge/--hi/--accent`，
复用 `--r-sm`、`calendar/chevron-left/chevron-right` 图标和现有按钮样式；选中日期使用网站浅粉底，
今天使用 accent 标识。所有筛选弹层在搜索容器与视口内定位，五周、六周月份都完整显示。

网站已由 `i18n.py` 的 Translator 和 `resources/locales/en.toml`、`zh-CN.toml` 提供界面文案，
`templates/shell.html` 把 `t.language` 写入 `<html lang>`。搜索、筛选、日历的按钮、提示和无障碍标签
在这两个现有 catalog 中同时增加；模板通过 `t.text(...)` 渲染，动态标签按现有 JSON data 属性方式传给 Alpine。
月份、星期、日期显示使用 `Intl.DateTimeFormat(document.documentElement.lang, ...)`，跟随界面语言；
日期到时间范围的转换继续使用页面时区。日历网格提供方向键、月份翻页键和日期确认，焦点样式使用网站 accent，
同时适应现有浅色、深色主题。

#### 手机布局与操作

沿用 `static/app.css` 的 `max-width: 959px` 单栏断点，以及
[现有手机适配计划](2026-09-27-bcs-mobile.md) 的共享模板、`data-view/data-pane` 与联系人/聊天路由。
手机从聊天标题栏的搜索图标进入同一个悬浮搜索；弹层宽度限制在当前可见区域内，四周至少保留 12px
并考虑 `env(safe-area-inset-*)`。搜索输入框和关闭按钮保持在顶部，结果区采用
`min-height: 0; overflow-y: auto; overscroll-behavior: contain`，弹层打开时沿用焦点限制和背景滚动锁定。

搜索输入沿用手机 16px 字号；搜索入口、关闭、筛选、月份切换、应用/取消和结果行至少 44px 高。
中文等输入法按 `compositionstart/compositionend` 管理组词状态，组词结束后按 300ms debounce 查询完整输入。
筛选按钮允许换行，长会话和发送者名称截断；展开选项在弹层内滚动。日期区在窄屏纵向排列起止日期，
日历保持七列并按容器宽度缩放，展开后仍覆盖结果；可见高度较小时允许筛选面板内部滚动，使五/六周月份和底部操作均可到达。
所有操作有直接可点的按钮，搜索头部始终显示关闭入口；点击结果整行即可定位，触摸操作沿用同一套筛选和日历状态。
打开筛选或日历时结束搜索输入的聚焦，让软键盘收起；关闭这些面板后保持搜索条件和结果位置。

软键盘可能只缩小 visual viewport，而 layout viewport 与视口单位仍保持原尺寸；
因此弹层的可用尺寸与位置读取 `window.visualViewport` 的 `width/height/offsetTop/offsetLeft`。
搜索组件打开时监听其 `resize/scroll` 并更新弹层边界，关闭或组件销毁时移除监听；
输入、关闭按钮和结果区域随实际可见高度调整。横竖屏、地址栏伸缩、键盘弹出/收起只重算布局，
关键词、筛选草稿、已加载结果和分页继续使用同一个 search store，保持当前选择与结果阅读锚点。
依据：[MDN VisualViewport](https://developer.mozilla.org/en-US/docs/Web/API/VisualViewport)、
[Chrome 的软键盘视口行为](https://developer.chrome.com/blog/viewport-resize-behavior)。

点击命中后关闭弹层并收起软键盘，按现有手机路由显示该会话单栏，再执行原消息附近读取与定位。
聊天标题栏保留搜索入口，再次打开恢复关键词、筛选、结果分页及滚动位置；联系人返回链接与浏览器前进后退
沿用现有导航机制，同一页面生命周期内的组件重建从 search store 恢复状态。
手机上的消息订阅继续运行，使用前述独立搜索区域、请求版本与历史阅读锚点规则。

#### 下行查询与原消息定位

`contrib/server/control.py` 新增 `_SearchRead(read="search", agent_id, query, target, sender, after_ms,
before_ms, sort, limit, offset, review)`，沿用 `commands_of(agent_id)` 调同一个 search 服务；默认 review 为 None，
与操作者历史读取一致。沿用 `getUpdates/control.result` 信封，BCS 读取结果后直接渲染，正文与全文索引由节点持有。
BCS 搜索路由经现有 `agents.view` 与 `@sees("computer")/@sees("agent")` 后才排队。
control 在 `commands_of(agent_id)` 的 storage scope 内以 `Agent(agent_id)` 调用搜索服务，使用完整来源投影。
节点配置为 session 模式时，操作者仍可搜索选中 agent 的全会话；runtime 命令使用绑定 actor 的范围与投影。

搜索命中已有 `thread_id/actor_id/target/channel/message_id`，加上当前路由的 computer/agent 就是完整来源坐标。
点击结果沿用联系人聊天页，history 请求用命中的 message id 作为 `around_message_id`。
目前 `pages/agents.py` 把 `latest` 传给 `history.latest(around=...)`，且消息 DOM 已有
`message-<message_id>`；在此基础上增加独立的定位参数 `focus=<message_id>`，先加载原消息附近的历史窗，
再沿定位方向做一小段上下滚动，使原消息处于可见区域。
定位参数始终与所请求会话一起使用。`history` 组件初始化时按 focus 定位，普通打开沿用当前的底部位置规则；
定位后的新消息追加保留用户正在阅读的位置。

历史定位同时保存当前 computer/agent/thread、阅读锚点与视口偏移，随用户滚动更新；阅读状态独立于短暂高亮的计时器。
`static/components.js` 的 history 初始化与 after-swap 在定位阅读期间按该状态恢复位置；
即使原消息位于已加载窗口底部，也保持该位置，直到用户主动跳到底部或普通切换会话后恢复跟随规则。
`#history` 整体替换与离线恢复仍围绕当前阅读锚点读取并恢复视口，临时高亮到期只清除样式。
点击另一个结果或切换会话时使旧历史请求失效；较早发出的 tail 刷新或附近历史响应在 swap 前核对
会话与定位版本，取消失效响应的替换，当前订阅继续更新。

定位完成后整条原消息使用网站浅粉 focus 样式约 1.8 秒，再用约 350ms 淡出恢复普通消息样式。
临时定位标记、提示及临时关键词样式到期一并清除，恢复时正文、字重、排版与滚动位置保持原状。
再次选择结果时取消上次的定位计时器，以新消息为准；`prefers-reduced-motion` 使用即时滚动和样式恢复。

`static/components.js` 的当前会话点击优化也要区分消息定位：普通重复点击保留现有聊天，带 focus 的搜索结果则执行
定位；原消息已在 DOM 时直接滚动，尚未加载时重新读取围绕该消息的历史窗。
选中结果后关闭搜索弹层并打开对应会话；再次打开搜索保留原关键词、筛选、分页和结果滚动位置，
手机使用同一悬浮搜索与原消息定位流程。
电脑离线、响应超时、搜索超时、空结果使用现有页面反馈方式，中英文文案同时补齐。

## Tasks

按任务串行开发；每项完成后停下来等待 review。每项代码修改执行 Ruff 检查和格式检查，以及仓库根目录
`uv run scripts/pyright_lsp_check.py --outputjson .`。

### Task 1：节点查询与存储索引

- [x] 定义 search 输入、命中、分页结果与 storage 接口；完成 v32 migration、FTS 触发器、初始化与 scoped 查询。
- [x] 补齐长短片段混合匹配、thread/sender/time/review/状态筛选、排序分页和 reader 查询预算。
- [x] 在真实临时 SQLite 上验收中文长词、两字词、中英组合、代码片段、过滤查询、跨多个自有会话的结果与分页；
  同一数据库配置多个 agent，验证每个 scope 返回自己的会话集合。
- [x] 验收索引初始化、写入、正文修改、删除和事务恢复后继续检索；用实际查询计划和隔离数据记录短片段的性能。

2026-09-30 验收：新增真实 SQLite 搜索测试及相关回归共 64 项通过；全仓 Ruff 检查、格式检查与
根目录 Pyright LSP 检查通过（344 个文件、0 条诊断）。隔离数据库含 100,002 条消息和两个 agent，
两字词搜索约 49ms，限定时间后约 25ms；实际查询计划使用 agent／时间索引及时间范围约束。

### Task 2：bcc、历史读取与 instructions

- [x] 接入 CommandService、dispatch、CLI 输出和工具审计；session 搜索限制在绑定的当前会话，target 沿用
  现有解析与会话范围检查；read/send/unfollow 参数和行为保持原有约定，历史读取继续按现有规则观察 freshness。
- [x] 修改三处搜索及历史引用 instructions，使用公共消息格式，并补齐搜索活动的中英文 catalog。
- [x] 在隔离节点使用真实 `bcc` 验收参数、输出与分页：dangerous_individual 完成跨自有会话搜索，session 搜索
  当前会话；两者均完成 `search → read --target … --around …`、携带 target 回复及 thread unfollow。
- [x] 分别在两个绑定会话的 session runtime 中搜索各自历史，核对相同 target 参数、公共消息头、instructions、
  inbox notice 与回执；搜索展示含 result/ref、Source/Sender/Time、preview/match、省略号与上下文读取提示。
- [x] 用 TestChannel 向真实 provider runtime 注入自然问题，例如「上周关于 staging 的发布方案最后怎么定的？
  当时的争议和回滚责任人是谁？」；验收 agent 自行检索、读取上下文后引用原消息，搜索后未读 cursor 与 freshness 保持。

2026-10-01 验收：全仓回归 602 项通过（9 项跳过，46 项外部 e2e 单独执行）；Ruff 检查、格式检查与
根目录 Pyright LSP 检查通过（345 个文件、0 条诊断）。真实 Codex provider 经 TestChannel 完成两个
session 的独立历史问答和 dangerous_individual 跨会话问答，均自动搜索、读取上下文并引用原消息 ID。
两项 provider e2e 覆盖三个 runtime 场景，在真实 binding 中执行 bcc 参数、日期边界、分页、target 搜索与
读取/回复/unfollow、草稿、提醒与取消验收。搜索前后未读 cursor 和发送 freshness 快照保持一致。
9 种示例与既有源码 formatter 对照通过，覆盖长文截断、Markdown、组件标签与引用字面量；plan 的完整预期输出
与实际渲染结果一致。现有消息头、notice、回执、提醒和 read/send/unfollow 用法保持公共格式。

### Task 3：control 与 BCS 搜索入口

- [x] 增加 search 下行读取与 BCS 页面查询，会话选项复用现有 contacts 分页。
- [x] 在聊天标题栏右侧接入搜索图标、Ctrl+F 与悬浮弹层，输入 debounce 查询、请求版本匹配、结果独立滚动加载。
- [x] 将搜索状态保存到按 computer/agent 区分的 Alpine store，搜索容器独立于联系人与历史刷新区域；
  保留展开状态、筛选草稿、结果分页与滚动位置，并在渲染前核对响应的 agent 与查询版本。
- [x] 实现会话/发送者/时间组合筛选、排序、数量与清除筛选；片段最多两行，关键词使用加粗深色文字。
- [x] 实现网站风格自绘日历、起止日期与应用/取消；接入现有 en/zh-CN catalog 和 Intl 日期显示。
- [x] 沿用 959px 断点实现手机悬浮布局、安全区、16px 输入、44px 触控操作、窄屏筛选与日期排布；
  根据 visualViewport 更新软键盘和横竖屏下的弹层边界，并在关闭/销毁时清理监听。
- [x] 验收筛选展开及应用后的结果变化、日期包含边界、月份切换、五/六周布局、键盘焦点与关闭顺序；
  中英文、浅色/深色主题及手机弹层均使用现有网站样式。
- [x] 使用真实隔离 BCS 与节点验收相同筛选的 CLI/control 结果一致，操作者通过现有可见性检查选中 agent 后查询。
- [x] 搜索加载多页并滚动后，保持弹层、筛选或日历打开，用真实节点产生消息并经历至少三轮 5 秒订阅检查；
  核对刷新实际发生，搜索条件、草稿、结果、焦点、当前选择与滚动位置保持，再关闭并重新打开核对恢复。
- [x] 隔离浏览器覆盖 320/390/430/768/959/960px、中英文、浅深色与短视口，验收触摸展开筛选、日期选择、
  关闭与结果滚动；覆盖 composition 组词事件、视口缩放和日期键盘选择。
- [ ] 在 iOS Safari、Android Chrome 实机验收输入法组词、软键盘、地址栏、安全区及横竖屏变化，记录设备与结论。

2026-10-01 验收：真实隔离 BCS、SQLite 节点与 TestChannel 的两项浏览器 e2e 通过（65.56 秒），
其中自然历史问答使用真实 Codex provider 建立 session binding。相同 query/target/sender 下，真实 bcc
与 control 的 43 条来源一致；操作者在 session 节点搜索两个自有会话。日期过滤包含本地起始日 00:00:00.000
和结束日 23:59:59.999 的实际消息。切换两个 agent 后恢复原筛选与结果。
搜索加载两页并滚动、保持日历与日期草稿打开，经历三轮真实消息入库和 5 秒订阅检查；联系人片段实际刷新，
搜索 DOM、查询、结果、分页、键盘选择、草稿、焦点与滚动位置保持。关闭/重新打开、键盘选择命中、同 agent
整页片段重建与跨 agent 返回均恢复搜索状态。桌面及 320/390/430/768/959/960px、中英文、浅深色、
五/六周月份共 48 组页面组合通过，另覆盖短视口内部滚动、月份按钮、日期键盘、取消和 composition 事件；
截图已检查，浏览器错误为 0。全仓回归、Ruff 与根目录 Pyright LSP 检查通过。
当前环境只有 Linux Edge，未连接 iOS/Android 设备；实机软键盘、安全区和地址栏行为尚未验收，设备项保持未勾选。

2026-10-01 review 验收：发送者下拉从当前已加载搜索结果收集并去重，实际第一页 20 个发送者，
加载后补充到 30 个；选择发送者后按新结果更新选项，跨 agent 返回恢复结果与选项。
真实隔离浏览器两项 e2e 通过（76.50 秒），601 项回归通过；Ruff、格式检查与根目录
Pyright LSP 检查通过（347 个文件、0 条诊断）。

2026-10-01 视觉 review 修正：按最终五张设计图恢复搜索输入首行、agent 与筛选同行、带头像的来源行、
两行加粗片段、独立发送者行和底部快捷键提示。筛选与日历覆盖结果，展开前后结果区域位置保持。
使用原图同一组六条示例消息，在真实隔离节点与 BCS 验证会话筛选 2 条、发送者筛选 3 条、日期筛选 3 条，
中文浅色桌面和 390px 触摸视口截图已对照。原有两项搜索浏览器流程复测通过，覆盖中英文、浅深色、
窄屏与短视口、多页结果、日期和订阅刷新；相关页面 10 项检查通过。

2026-10-01 快捷键提示 review 修正：底部上下键使用官方 `lucide-static 1.46.0` 的
`arrow-up`、`arrow-down` SVG，通过现有 `c.icon` 宏内联；键帽内图标为 12px，大小与居中对齐统一。

### Task 4：原消息定位与界面验收

- [ ] 接入来源坐标、附近历史窗、短距离上下滚动、短暂 focus 高亮后恢复、同会话定位与搜索状态恢复。
- [ ] 保留独立的历史阅读锚点，在 tail 追加、整体历史替换与离线恢复时恢复视口；定位或会话切换后
  在 swap 前取消旧会话、旧定位版本的历史响应，用户主动跳到底部后恢复普通跟随规则。
- [ ] 真实浏览器验收桌面与手机：跨会话命中、旧消息未加载、当前会话命中、连续选择多个结果、返回结果后翻页。
  定位后继续查看前后文，验证高亮到期后恢复原有文字与排版且位置稳定，以及新消息追加和普通联系人切换。
- [ ] 定位旧消息并等待高亮结束后，产生真实新消息并经历至少三轮订阅检查，核对原消息、上下文和阅读位置持续保留；
  同时验收旧刷新请求与连续定位交错、空记录/读取失败后整体历史替换，以及电脑离线再恢复的刷新路径。
- [ ] 在手机完成搜索 → 触摸命中 → 单栏原消息定位 → 查看前后文 → 重新打开搜索，并覆盖联系人返回与前进后退；
  搜索、日历打开和历史定位期间均经历真实消息刷新，核对状态恢复与阅读位置稳定。

所有 BCN/provider/BCS e2e 使用测试专用配置、临时数据库和隔离进程；provider e2e 使用 TestChannel 控制面。
验收产物记录查询、结果来源、页面效果和检查结论。

## 验收标准

- 自然历史问题可以在对应模式的会话范围内找到原消息、读取上下文并引用结论。
- session 的搜索、读取与回复均限定当前会话，target 沿用现有会话边界检查；所有模式提供相同参数和公共输出格式。
- dangerous_individual 与 BCS 可以搜索所选 agent 的全部可读会话，并用完整来源坐标读取上下文和跳转。
- 在相同范围与筛选下，关键词、结构过滤、分页和实际排序在 CLI 与 BCS 之间一致；“部署”这样的短中文词可用。
- 查询结果的所有来源均属于所选 agent，review 与历史可见状态按各入口现有规则生效。
- 搜索是只读检索；未读消费与发送 freshness 按各自命令规则工作。
- BCS 点击命中后显示对应会话并定位原消息，桌面和手机都能继续查看上下文。
- 聊天标题栏右侧图标和 Ctrl+F 都打开悬浮搜索，输入后自动出结果，列表独立滚动并继续加载；重新打开保留搜索状态。
- 连续至少三轮 5 秒订阅检查及实际消息刷新后，搜索弹层、筛选草稿、已加载结果、分页、焦点和滚动位置保持；
  过期响应按 agent 与请求版本隔离，同一 agent 内会话导航及重新打开搜索都能恢复该状态。
- 会话、发送者、时间筛选均可展开与组合使用，自定义时间包含开始和结束当天；结果预览截断并以加粗深色字强调关键词。
- 日期框展开网站风格日历，选日期、切月份、应用与取消均有效；月份、星期、按钮及提示跟随现有 i18n，
  中英文与两种主题下的字体、选中态和图标都符合网站样式，桌面与手机完整显示五周及六周月份。
- 手机搜索、筛选和日历在 320px 至 959px 窄屏内可触摸操作，页面无整体横向溢出；软键盘、地址栏及横竖屏变化后
  输入、关闭与结果仍可到达，安全区正确，搜索状态保留；实机完成单栏定位与重新打开搜索流程。
- 原消息定位先加载附近记录并短距离滚动，短暂亮起后恢复普通样式，文字、排版与阅读位置保持稳定。
- 高亮结束后，新消息追加、整体历史替换和离线恢复仍保留历史阅读锚点与视口；连续定位时旧响应不会覆盖新位置，
  用户主动跳到底部后恢复普通消息跟随。
