# bazaar compute server

## 背景

一个 bcn 节点对外只有本机 socket 上的 `health` 一种观测手段（`app/application.py:393`、`app/application.py:429`），
返回的是当下的快照。节点上发生过什么无从查看。

节点内已经有一条脱敏的事件出口：`AuditRecorder` 把审计事件交给 `IAudit`（`core/audit.py:76`），
`[node] audit` 选择实现（`app/config.py:100`），entry point 组 `bazaar_compute_node.audits`（`app/registry.py:17`），
唯一实现是进程日志（`contrib/logging/audit.py:20`）。这条出口目前覆盖 turn 生命周期（`runtime.turn.*`；provider 报上来的
turn 事件曾以各自的名字 `claudecode.turn.*` / `codex.turn.*` / `bcn.turn.*` 入审计，正常完成只有 provider 这一条，本计划
把它们统一记成 `runtime.turn.<started|completed|failed|cancelled|unknown>`，provider 自己的叫法放进 metadata 的
`provider_event`）、入站落库与拒绝、出站各投递状态、审批 requested/decided/failed、agent 执行的
bcc 命令（`tool.bcc.*`）、runtime 进程启停、错误反馈。

它缺四样：runtime 内部的工具调用与 token 用量（`ToolCallStarted`、`UsageUpdated` 等只经 `accept_turn_event`
交给 channel 做进度投影，`core/orchestration/turn.py:704`）、消息正文（入站审计的 metadata 只有映射标记，
`core/orchestration/orchestrator.py:1066`；出站同理，`core/orchestration/command.py:561`）、节点 health、
以及从外部回到节点的指令通道。

`IAudit` 没有生命周期与选项（`core/observability.py:17`、`app/registry.py:20`），实现只能是无状态的同步写出。

## 目标

新增 bazaar compute server（bcs）：一个独立进程，接收多个 bcn 节点主动上报的审计事件并持久化，以网页展示。
节点与 bcs 可以部署在不同机器上。bcs 只存储事件和它自己的接入、登录、权限数据；节点的配置、凭据与会话状态仍只在
节点上。事件只用来看活动：任何「现在是什么」——聊天记录、定时器、联系人、配置——都在需要时通过下行通道向电脑
下指令实时查，bcs 不从事件折算副本；电脑离线时这些就查不到，界面上离线的行置灰且不可点开。

节点侧是 `IAudit` 的一个实现，`[node] audit = "server"` 选用。它同时持有到 bcs 的下行通道：从 bcs 拉取指令，
以节点本地命令协议的语义在进程内执行（`NodeApplication._dispatch`，`app/application.py:386`，即 bcn.sock 背后
的同一入口）；若要自建管理面，读写 bcn.sock 即可得到同样的控制能力。

bcs 的界面是 IM 的形状：左侧图标模块栏（智能体、活动、设置，电脑在最下），主体是通讯录式的三栏——智能体列表、
该智能体的联系人（线程与 DM）、选中联系人的聊天记录；智能体有资料页（配置、技能、工作区、状态、活动）；电脑模块
列出每台电脑与其上的智能体。界面文案的对象词是「智能体」「电脑」「活动」。结构原型是 `plans/2026-09-15-bcs/wireframe.html`
（五张粗块图：智能体三栏、联系人审核、智能体资料页、电脑、登录与权限），样式定稿是 `plans/2026-09-15-bcs/style.html`
（智能体一屏的浅色与暗色完整渲染，含标识与悬停活动卡），两份都是独立 HTML，浏览器直接打开。

本计划搭框架：审计出口补全、上行协议与鉴权、bcs 的接收与存储、界面壳子（模块栏、智能体列表与悬停活动卡、电脑
列表与接入）、超级管理员的账号密码登录与改密。下行通道的协议形状在此定死，随第一个需要下发的指令实现；联系人、
聊天记录、审核、第三方登录与角色权限、智能体管理在框架之上各自另行计划。

## 设计

### 部署形态

bcs 与 bcn 同仓库、同发行物、同版本号。源码在 `src/bazaar_compute_server/`，与 `src/bazaar_compute_node/` 并列。
`pyproject.toml` 的 `[tool.uv.build-backend]` 设 `module-name = ["bazaar_compute_node", "bazaar_compute_server"]`，
`[project.scripts]` 增加 `bcs = "bazaar_compute_server.cli:main"`。`uv tool install bazaar-compute-node` 之后
`bcn` 与 `bcs` 两个命令同时可用。

两个模块之间没有 import 关系，只通过 HTTP 通信。协议两侧各自定义自己的 pydantic 模型，契约是 JSON 字段。

bcs 使用 `starlette` + `uvicorn`（`uvloop`、`httptools`）作服务端，`pydantic`、`jinja2`、`aiosqlite` 是现有依赖；节点侧仍用 `aiohttp` 的客户端发送。

### 协议

形状与 Telegram Bot API 一致：每个方法一个路径，`POST /node/<method>`，请求体为 JSON，响应统一信封：

```
{"ok": true, "result": {...}, "protocol": 1}
{"ok": false, "error_code": "unauthorized", "description": "...", "protocol": 1}
```

`protocol` 是协议版本，与包版本无关，两侧各有一个常量。节点每个请求带 `X-BCS-Protocol: 1`；bcs 收到不认识的版本回
`error_code = "unsupported_protocol"`。新增字段与新增方法不升版本，两侧对未知字段一律忽略（pydantic 模型默认
`extra="ignore"`）；改变既有字段语义或删除字段才升版本。

鉴权：节点持有一枚 token，格式 `<computer_id>:<secret>`，与 Telegram bot token 同形，由 bcs 在电脑接入时生成
（`computer_id` 为 uuid，`secret` 为 32 字节随机数的 base64url），只在接入时显示一次。请求头
`Authorization: Bearer <token>`，不放 URL。bcs 从 token 取出 `computer_id`，与库中该电脑的 secret 哈希做比较；
不匹配回 `unauthorized`。
节点不开任何入站端口，与 bcs 的所有交互都由节点发起。secret 泄露的影响面因此只是伪造该节点的事件。

方法：

- `reportEvents`：请求体 `{"run_id": "...", "events": [...]}`。`run_id` 是节点进程启动时生成的 uuid；每个事件带节点内
  单调递增的 `seq`。bcs 以 `(computer_id, run_id, seq)` 唯一，重复上报（请求超时后重试）静默去重。响应
  `result = {"accepted": <count>}`。
- `getUpdates`：请求体 `{"offset": <int>}`，bcs 挂起直到有 `update_id > offset` 的指令或超时，响应
  `result = {"updates": [{"update_id": <int>, "request": {...}}]}`。`request` 是节点本地命令协议的一条请求
  （`app/application.py:386` `_dispatch` 接受的形状），节点执行后以 `reportEvents` 上报一条 `control.result`
  事件携带 `update_id` 与响应。下一次 `getUpdates` 的 `offset` 即确认。本计划只定义该方法，节点侧的轮询与执行
  随第一条指令实现。

### 事件

上报的事件就是 `AuditEvent`（`core/audit.py:33`）的 JSON 形式，外加 `seq`。JSON 形式与
`contrib/logging/audit.py:35` 写入日志的 payload 相同：`event_name`、`state`、`created_at_ms`、`correlation`
（`Actor` 以 `_id` 后缀展开）、`metadata`，以及非空的 `duration_ms`、`error_kind`、`error_type`、`error_message`、
`traceback_ref`。这段序列化从 logging 实现移到 core 的 `AuditEvent.as_payload()`，两个实现共用。

agent 归属取 `correlation.node_id`：core 各处把 agent id 写在这个字段（`core/orchestration/turn.py:424`、
`core/orchestration/orchestrator.py:914`），字段名保持。

core 补四类审计事件，使这条出口覆盖 bcs 需要的全部信息：

1. runtime 事件。`_consume_turn_stream`（`core/orchestration/turn.py:682`）在把事件交给 channel 的同一分支里追加审计：
   `tool_call.started` / `tool_call.completed` / `tool_call.failed`（metadata 为 `call_id`、`name`、`parent_call_id`；
   工具的输入与输出属于 runtime 自己的记录，不进审计）、
   `usage.updated`（metadata 为 `UsageUpdated` 的字段）、`context.compaction.started` / `context.compaction.completed`。
   `ContentDelta` 与 `ToolCallTextDelta` 不审计：它们是逐 token 的文本流，v08 清掉的正是这类事件
   （`contrib/sqlite/migrations/v08_transient_stream_events.py`）；对话记录来自消息事件。
2. 消息正文。`channel.inbound.persisted` 的 metadata 增加 `text`、`sender`、`message_type`、`reply_to_message_id`
   与附件清单（`name`、`media_type`、`size_bytes`）；`channel.outbound.<state>` 的 metadata 增加 `text` 与附件清单。
   键名用 `text`：脱敏器把 `body` 列为敏感键以挡住 provider 原始报文（`core/utils/sanitization.py`），该规则不动。
   logging 实现照常输出整条 payload，因此选用 logging 时正文会进入进程日志。
3. 节点 health。`NodeApplication` 在就绪时写一条 `node.health` 审计事件，之后每隔一个启动预算
   （`TimeoutBudget.startup_seconds`，60 秒）再写一条，metadata 为 `_health()` 的返回值加 `version`、`python`、
   `gil_enabled`、`system`（`platform.system()`）。间隔取启动预算的理由：节点允许自己沉默多久来启动，消费者就允许它沉默多久；bcs 以两个间隔内
   没有任何事件判定节点离线。实现无需回调即可得到 health。
4. 定时器。定时器归属一个会话（`owner_thread_id`）并锚定一条入站消息，节点今天不为它写任何审计。补五个事件：
   `ReminderCommandService` 在写入后发 `reminder.scheduled` / `reminder.updated` / `reminder.snoozed` /
   `reminder.canceled`；`ReminderScheduler` 触发后发 `reminder.fired`（metadata 多一个 `scheduled_for_ms`），
   把无法触发的定时器退掉时发 `reminder.canceled`（`state = failed`，带原因）。这些只进活动卡；定时器列表
   在下行通道的分支里走实时指令。
   metadata 统一为 `reminder_id`、`title`、`state`、`next_fire_at_ms`、`repeat_rule`、`timezone`、`revision`、
   `last_fired_at_ms`、`anchor_message_id`，以及锚点消息的 `target`、`target_kind`、`target_name`；correlation
   带 `node_id`（agent）、`thread_id`（所属会话）与锚点的 channel。
5. 指令结果。`control.result`，metadata 为 `update_id` 与本地命令协议的响应。随下行通道实现。

### IAudit 的生命周期与选项

`IAudit` 继承 `IAsyncLifecycle`（`core/lifecycle.py:34`）。`NodeApplication.start` 在 storage 之后启动 audit，
`stop` 在 storage 之前停止（`app/application.py:141`、`app/application.py:279`）。logging 实现的 start/stop 为空。

审计工厂接收一个 `AuditContext`（`core/observability.py`，与 channel 的 `ChannelContext`、runtime 的
`RuntimeCommandContext` 同一做法）：`options` 来自 `[node.<audit>]` 表——`audit = "server"` 时读 `[node.server]`；
`timer_wheel` 是节点的定时轮，实现需要定时的都用它，不用 `asyncio.sleep`；`timeout_budget` 是节点的
超时预算，实现的请求超时从这里取。`NodeApplication` 先建定时轮再建 audit。配置版本保持 4，这是可选表的
新增。

### 节点侧 `contrib/server`

配置：

```toml
[node]
audit = "server"

[node.server]
url = "https://bcs.example.com"
token_env = "BCN_SERVER_TOKEN"
```

`url` 必填；`token_env` 与 channel 凭据的 `*_env` 约定一致（`app/config.py:35`）。

这段配置由 `bcn server connect --url <url> --token <token> [--env-file <path>]` 写入：把 `[node] audit` 设为
`server`、写入 `[node.server]`（`token_env` 固定为 `BCN_SERVER_TOKEN`），并把 `BCN_SERVER_TOKEN=<token>` 追加到
`--env-file`（默认 `~/.config/bcn/runtime.env`，也是 `bcn system-service install` 不指定时读的文件；不存在则创建，0600）。配置与
环境文件的写入都复用 `bcn agent add` 的写回路径（`app/config.py` 的序列化与原子替换）：环境文件里还有 channel 的凭据，
中途断掉不能把它写空。服务重启仍由用户执行。

`ServerAudit(IAudit)`：`append()` 给事件赋 `seq` 后放入 `asyncio.Queue`，立即返回。`start()` 生成 `run_id`
并启动发送任务：按顺序取已排队事件组成一批，一批的 JSON 不超过 512 KiB（bcs 只收 1 MiB 的请求，
取一半留余量；装不下的那条领下一批），`POST reportEvents`，然后取下一批。一批只发一次：bcs 收下了就计入 `sent`，
没收下（连不上、超时、非 200、`ok=false`）就整批丢掉计入 `dropped`，`last_error` 写明原因，不重试、不退避、不积压——
事件是活动记录，不是不能丢的东西，节点不为它等待。请求超时用 `TimeoutBudget.command_seconds`。
`stop()` 取消发送任务，未发出的事件随进程丢弃，`node.health` 事件已说明节点状态。
配置不可用时（`url` 不是绝对的 http(s) 地址、`token_env` 指的变量没有值）节点照常启动：sink 不发请求，`append()`
直接计入 `dropped`，原因放在 `last_error`，启动时记一条 warning。`bcn server connect` 在输入时就拒绝不是绝对
http(s) 地址的 `--url`。

`health` 属性给出 `queued`、`sent`、`dropped`、`last_error`、`last_sent_at_ms`，`_health()` 以 `audit` 字段并入
（`app/application.py:454` 处目前只有名字）。

### bcs 存储

bcs 使用自己的数据目录 `~/.bcs`，sqlite 文件 `bcs.sqlite3`，`aiosqlite` 访问。三张表：

```sql
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    language TEXT,               -- 阅读语言，空为跟随浏览器
    theme TEXT                   -- 主题，空为跟随系统
);
CREATE TABLE computers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    computer_id TEXT NOT NULL REFERENCES computers (id),
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    agent_id TEXT,
    event_name TEXT NOT NULL,
    thread_id TEXT,
    created_at_ms INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (computer_id, run_id, seq)
);
CREATE INDEX events_by_agent ON events (computer_id, agent_id, created_at_ms);
CREATE INDEX events_by_thread ON events (computer_id, agent_id, thread_id, created_at_ms);
CREATE TABLE relations (                  -- 数据权限点
    subject_id TEXT NOT NULL,             -- 账号 id；*_share_role 时是角色 id
    kind TEXT NOT NULL,                   -- computer_owner / agent_owner，预留 computer_share / agent_share / computer_share_role / agent_share_role
    target_id TEXT NOT NULL,              -- kind 决定它是电脑 id 还是智能体 id
    ext TEXT,                             -- 这段关系附带的权限，JSON；owner 为空
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (subject_id, kind, target_id)
);
CREATE INDEX relations_by_target ON relations (kind, target_id);
```

`accounts` 是登录账号表，本计划只有 root 一行；`computers` 是受信电脑表，由界面上的「接入」写入。密码与
secret 都以 scrypt（`cryptography`）哈希存储：每条记录各自生成 16 字节随机盐，参数 n=2^15、r=8、p=1，
存储格式 `scrypt$<n>$<r>$<p>$<盐 base64url>$<哈希 base64url>`，校验时用记录里的盐和参数重算并做常量时间比较。
一次派生占一个核与 128·n·r（32 MiB）内存，所有派生（登录校验、接入 token 校验、建哈希）都在一个线程数等于
CPU 数的专用线程池上跑，同时算的不会多于机器能算的，多出来的登录请求排队等，不会把内存打满。bcs 只存事件与
自己的接入、登录、权限数据，不存节点的配置、凭据或会话。

`agent_id` 与 `thread_id` 从 payload 的 `correlation` 取出作为列，其余留在 `payload`。`agent_id` 会出现在地址、
元素 id 与 CSS 锚点名里，进门时按 pydantic 校验只接受 `[A-Za-z0-9_-]{1,64}`（节点发的是 uuid）；`created_at_ms`
限定在 0 到页面能显示的最晚时刻（9999 年）之间；智能体名字超过 100 个字符截断（一行能显示的长度，名字还是 identicon 缓存的键）；页面要读的几种事件（`node.health`、`usage.updated`、
`channel.inbound.persisted`、`tool_call.*`）的 metadata 也按各自的模型在这里校验（`protocol.py` 的 `Health`、`Usage`
等，只定页面用到的字段，其余原样保留；存下的是校验后的样子），之后 fleet 与 activity 直接按字段读，不再逐个判断类型；不合规的整批 400。
`reportEvents` 在一个事务内 `INSERT OR IGNORE` 整批，接收条数取自 `rowcount`。

写入与 bcn 的 sqlite 一样只有一个 writer：所有写操作排进一个队列，由一个任务按序执行、逐个提交，不加锁；读取用各自的
reader 连接，随时并发。

留存：配置 `retention_days`，每天删除 `received_at_ms` 早于该期限的行；`node.health` 只保留每台电脑最新一条；留存期内还有
事件的 runtime 会话，其最新一条 `usage.updated` 留作今日用量的基线，沉寂整个留存期的会话连基线一起清。

### 权限

权限分两类点：功能权限点管「能不能用这个功能」，数据权限点管「能碰哪些数据」。

功能权限点按模块命名：`computers.view`、`computers.create`、`computers.delete`、`agents.view`、`agents.chat`、
`agents.approve`、`settings.security`……本计划只有 root 一个账号，它拥有全部；将来功能权限点由角色持有，账号经角色得到。

数据权限点是主体与对象的关系，存 `relations` 表：主体（账号，将来也可以是角色）、关系种类、对象 id、`ext`。
本计划写两种：账号接入一台电脑，就是它的 `computer_owner`；一台电脑的 `node.health` 第一次提到某个智能体，该电脑的
owner 成为它的 `agent_owner`（智能体 id 是节点铸的 uuid，全局唯一，不必带电脑 id；这一行的 `ext` 记 `{"computer_id": …}`，
说明它是经哪台电脑来的）。两者都与对象的出现同一事务写入；删除电脑时按 `ext` 里的电脑 id 找到它的智能体，连同它们的
关系一起删，不依赖事件表。心跳每分钟一次，不能每次都去写库：存储在内存里记着本进程已经写过
`agent_owner` 的智能体 id 与每台电脑的 owner，心跳里的智能体都见过就什么也不做，只有新面孔才 `INSERT OR IGNORE` 并记下；
集合的大小就是舰队的智能体数，不另设上限；删除电脑时把它的条目一并忘掉，进程重启时集合为空，第一轮心跳把它填回来。预留四种：`computer_share` /
`agent_share`（分享给某个账号）与 `computer_share_role` / `agent_share_role`（分享给某个角色，`subject_id` 存角色 id），
这段关系附带的权限记在 `ext` 的 JSON 里（如 `{"permissions": ["view", "chat"]}`）；owner 的 `ext` 为空，拥有一切。
本计划没有任何地方写这四种。

访问层：Gate 认出账号之后再挂一层 `Access`（`access.py`，同样是 ASGI 中间件，放进 `request.state.access`），
把两类权限点的判断收在一处：`allows(point)` 答功能权限点，`can_see(kind, target_id)` 答数据权限点，`visible(kind, ids)`
批量筛，`subject_id` 给列表查询用。处理函数不手写判断，用两个装饰器声明：`@allowed("computers.delete")` 管功能点，
`@sees("computer", "computer_id")` 按路径参数问数据点，不过就 404；只有拼列表的 fleet 收一个 `Access`，用它按主体查电脑
（`list_computers` 多一个主体参数，与 `relations` 连接后再做键集分页）、按 `agent_owner` 筛智能体行。本计划的实现：
功能权限点对 root 一律为真；数据权限点只看 `*_owner`。多角色、`*_share*` 与解析 `ext` 里的权限都在这一层里加，页面不再
改。目前 root 接入的一切都归它，看到的与现在无异。

bcs 还没有发布过，全部建表都在唯一的初始迁移里，不留增量迁移；表结构改动直接改它，库文件重建。

电脑与智能体的当前状态取自每台电脑最新的 `node.health` 事件；两个心跳间隔内没有任何事件的电脑显示为离线；
是否处理中取该智能体最新的一条 turn 边界事件。这些读取按电脑、智能体、线程分组整批查（`MAX(id) … GROUP BY`），
一个页面固定几条查询，不随电脑与智能体的数量增长；互不依赖的读取用 `asyncio.gather` 并行，各用自己的 reader。

### bcs 界面

Starlette 应用，`jinja2` 服务端渲染，模板与静态资源在 `src/bazaar_compute_server/resources/`。界面文案全部经
translator，中英文两份，与 bcn 的 i18n 同一机制。

单页应用，用 htmx：壳子（模块栏、两栏骨架）只加载一次，之后所有导航与更新都是服务端返回的 HTML 片段换进
指定区域——模块栏点击 `hx-get` 换主区域并 `hx-push-url` 更新地址，列表选中换右栏，悬停头像 `hx-trigger="mouseenter"`
拉活动卡，活动卡 `hx-trigger="every 5s"` 自刷新，登录与改密表单 `hx-post`。地址可直达（刷新页面时服务端整页渲染同一
片段）。htmx 4.0.0（2026-08-28 发布，当前最新）的单文件以静态资源随包发布（`resources/static/htmx.min.js`），不引入
任何前端构建。写模板时遵守 4.x 的规则：属性不再隐式继承，父元素上的 `hx-target`、`hx-swap` 等要用
`:inherited` 后缀声明；4xx/5xx 响应默认会被换进 DOM，因此错误也返回可渲染的片段（登录失败即在表单里显示错误）；
后退不再用本地快照而是重新请求，与服务端整页渲染同一片段的做法一致。

**样式定稿**（`plans/2026-09-15-bcs/style.html`，浅色与暗色）。以 CSS 变量表达，浅色值 / 暗色值：

| 变量 | 浅色 | 暗色 | 用途 |
|---|---|---|---|
| `--canvas` | `#f4f1ec` | `#0c0e11` | 页面底：暖灰纸感，栏浮在上面 |
| `--panel` | `#fdfcfa` | `#15181d` | 栏：近白而不是纯白 |
| `--float` | `#fbf8f3` | `#1a1d23` | 浮层：活动卡、对话框 |
| `--rail` / `--rail-fg` | `#3b1a1a` / `#d9c9b3` | `#24100f` / `#b8a68f` | 模块栏：招牌的红木色，米白图标 |
| `--rail-on` / `--rail-on-bg` | `#f2c14e` / 白 10% | 同 | 当前模块图标：招牌的金 |
| `--fg` | `#18181b` | `#e6e8ee` | 正文 |
| `--muted` | `#71717a` | `#8b93a1` | 次要文字 |
| `--line` | `#ececef` | `#23272f` | 分割线、描边 |
| `--hi` | `#ffe4e6` | `#3a1f22` | 选中行 |
| `--accent` | `#ff5a5f` | `#ff6b6f` | 主按钮等强调 |
| `--ok` / `--warn` | `#84cc16` / `#f59e0b` | `#a3e635` / `#fbbf24` | 状态点：运行中 / 处理中；离线为 `--muted` 半透明 |
| `--warn-bg` / `--warn-fg` | `#ffe4e6` / `#be123c` | `#4a1d22` / `#ff9a9e` | 「待审核」标签 |
| channel 色 | telegram `#0284c7`、lark `#65a30d`、wecom `#d97706` | `#38bdf8`、`#a3e635`、`#fbbf24` | 联系人行里的 channel 名 |

字号 13px，系统无衬线（`-apple-system, "PingFang SC", "Noto Sans CJK SC", Inter, "Helvetica Neue", sans-serif`），
圆角 6px，头像 34px 方角 6px。规则：选中态只用平铺底色，没有左侧竖条；任何地方不用渐变；不同发言人不用颜色区分；
按钮文案只写结果（保存、新建、允许），不出现「下发」「重启」这类机制词；行内操作与模块栏用图标。
栏之间留 10px 画布、圆角 10px、发丝线与极淡投影，素白靠层次而不是靠颜色。表单是短的：卡片最宽 420px、输入框与
下拉最宽 260px，从不随栏拉满；只做一件事的按钮只放图标，文字是它的 hover 提示。每个颜色变量用 `light-dark()` 一处写
两色，默认随系统 `color-scheme`；账号选了主题时 `<html data-theme>` 把 `color-scheme` 固定为浅或深。

标识：模块栏左上角一块歪 8° 的红木木牌，金色斜体粗体 `Bazaar`，四角铜钉——木板 `#6b1f1f`、边 `#4a1414`、
木纹为边色的 50% 透明细线、字 `#f2c14e`、钉 `#d4a017`。48×26 的 SVG，`plans/2026-09-15-bcs/style.html` 中 `.logo.sign`。

头像：按名字生成的 5×5 对称像素图（GitHub identicon 的做法），色相取名字哈希，用于智能体、联系人与发言人。

图标：一律用 Lucide（ISC），只把用到的几个 SVG 随包放在 `resources/templates/icons/`，模板里 `icon("users")` 内联，
随文字取色；不自己画。Lucide 不收品牌标志，操作系统的标志取自 Simple Icons（CC0）：Linux 是企鹅、macOS 是苹果；
Windows 的标志已从 Simple Icons 移除，用 Lucide 的四格代替。电脑列表里操作系统图标占智能体头像的位置。下拉框是原生 `<select>` 去掉系统外观后按输入框的样子绘制，箭头也是 Lucide。

**本计划实现的壳子**：

- 模块栏：智能体、活动、设置三个图标在上，电脑在最下；本计划只有智能体与电脑两个模块可进入。
- 智能体模块：左栏智能体列表——头像、名字、`电脑名 · runtime`、右侧状态点（运行中 / 处理中 / 离线，来自最新
  `node.health` 与最近的 `runtime.turn.*`）。悬停头像弹活动卡：正在处理的会话与开始时间、最近五条活动（事件文案见
  下一节，最新一条带实时点）、今日 token 与费用（token 数按 K / M / B / T 缩写；`usage.updated` 是各 runtime 会话的累计值，按 `runtime_session_id`
  取今日最新一条减去午夜前最后一条，跨午夜的会话只算它今天增长的部分。Claude Code 的 result 里 `usage` 是这一轮的、
  没有 `total_tokens`，`modelUsage` 与 `total_cost_usd` 才是会话累计：claude provider 把 `modelUsage` 各模型累加成
  `total`，这一轮的 `usage` 作 `last`，`total_tokens` 都是各项之和；它不认识的模型费用按 0 报，bcs 无法补算）。
  活动卡是 `GET /agents/<computer_id>/<agent_id>/activity` 返回的片段，每 5 秒自刷新。
  卡上的时刻与「今天」按浏览器的时区算：壳子的 `<body>` 用 `hx-headers:inherited` 把 `Intl` 得到的 IANA 时区名放进
  每个请求的 `X-Timezone`，服务端认不出或没有时才用自己的本地时区。
  点击一行选中该智能体（`GET /agents/<computer_id>/<agent_id>`），右栏标题是名字、电脑、runtime 与 channel，
  内容留给联系人分支。离线的智能体在列表里置灰、不可点开；离线的电脑置灰但仍可点开，删除它就在它的页面里。
- 电脑模块：左栏电脑列表——名字、版本、最近活跃时间、在线状态；标题栏「+ 接入」弹出对话框输入名字，生成 token，
  对话框显示一段可复制的接入命令，关闭后不再可见。命令分两个 tab——Linux / macOS 与 Windows，只差装 uv 的第一行——
  默认打开浏览器自己那一种（`Sec-CH-UA-Platform` 客户端提示，没有就看 `User-Agent` 里有没有 Windows），可手动切换，
  纯 CSS 的 radio tab，不带脚本：

  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh      # Windows：powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  uv tool install bazaar-compute-node
  bcn server connect --url http://<bcs 地址> --token <computer_id>:<secret>
  # 首次接入这台电脑：注册并启动服务
  bcn system-service install
  bcn system-service start
  # 已在运行的节点改为执行 bcn system-service restart
  ```

  注释是命令的一部分，整段粘贴也能跑。`bcn system-service install` 不带 `--env-file` 时读的就是 connect 写的那个默认
  文件（`~/.config/bcn/runtime.env`，Windows 是 `runtime.ps1`），三个平台的包装都容忍它不存在，所以卡上不用写路径。

  右栏是选中电脑的智能体列表（头像、名字、runtime、channel、状态点）。
- 模块栏的设置模块：和其它模块一样是两栏——左栏是页的列表（外观、安全），右栏是选中页的表单。外观：语言
  （跟随浏览器 / 中文 / English）与主题（跟随系统 / 浅色 / 深色），存在账号上，选完整页重刷；安全：修改密码。
  账号只有首次启动建的 root 一个，没有角色，也没有任何地方能建角色或账号。角色是后话，形状已定：不内置角色，root
  自建角色、名字自定，权限按模块给（某模块的浏览权，以及各项增删改查各是一个权限）；可把某个角色设为默认，
  新登录的人得到它；没有默认角色时接入任何 OIDC 都被灰色遮罩禁止，遮罩上提示先建一个默认角色。
  退出登录是模块栏底部、电脑图标上方的一个图标。页面语言由账号的选择决定，未选则看浏览器的 `Accept-Language`；bcs 配置里没有语言项。
- 模块栏在换页的响应里以 `hx-swap-oob` 一起带回，当前模块的高亮才会跟着换；图标有 hover 态。
- 列表滚动翻页：电脑按 id（uuid7，即接入顺序）键集分页，一页 50 台，智能体列表的一页是这 50 台的智能体。
  列表末尾有一个 `.edge` 元素记着已加载到的 id（`data-after`）；还有下一页时它带 `hx-get="…?after=<id>"`、
  `hx-trigger="revealed"`、`hx-swap="outerHTML"`——滚到它进入视口就请求下一页的行，下一页把它换掉并带上自己的
  `.edge`（htmx 官方 infinite scroll 的写法）；到了末尾它只留 id 不再请求。详情页与活动卡按 id 直接查那一台电脑，不依赖
  它在哪一页。行的内容放在 `.in` 盒子里，`content-visibility: auto` 加 `contain-intrinsic-size`，屏幕外的行不布局
  不绘制；锚点名与活动卡在行元素上、盒子外——包含会让 `position: fixed` 的卡失去锚点，已实测。
- 页面不是加载完就停：智能体列表、电脑列表、电脑详情各是一个片段，每 5 秒重取自己一次（和活动卡同一节奏，
  `hx-trigger="every 5s"`），选中态通过 `?selected=` 带回，列表以 `hx-vals` 把 `.edge` 上的 id 作为 `?until=` 带回，
  服务端返回从头到该 id 的整段，已滚出来的行不会被刷新缩回去。新片段以 `hx-swap="outerMorph"` 合并进旧的：行元素
  保持同一个（悬停、焦点不丢），活动卡与删除确认卡标 `hx-morph-skip`，已加载的内容和展开的确认原样留下。
- 页面上所有替换默认都是 morph（`<body hx-swap:inherited="innerMorph">`，带外替换用 `outerMorph`）：换页、换选中、
  表单回填都是把新内容合并进旧的，不重建 DOM，滚动位置、悬停、焦点、输入中的值都留着。
  morph 靠 id 对齐元素：列表里的每一行、每张会被单独替换的卡都要有稳定的 id；活动卡的锚点也按智能体 id 命名，
  不按序号，列表重排时卡才跟着自己的行。标了 `hx-morph-skip` 的元素连属性也不更新，只由它自己的请求刷新。
- 出错页：两根绳子从上垂下、歪 4° 的红木牌，牌上是金色斜体的状态码，下面一行标题、一行说明和「回到集市」。
  整页错误和 htmx 片段错误都用它（片段换进主区域，模块栏还在）；`/node/*` 的错误仍是 JSON 信封。
  服务端根本连不上时（请求发不出去，htmx 抛 `htmx:error`），壳子里预先带着一块没有状态码、画着拔掉的插头的牌，
  由页面里的一小段脚本换进主区域，按钮是「再试一次」（整页重载）。
- 默认监听 `127.0.0.1:8765`，`listen` 可配置成别的地址，按 URL 的方式解析出 host 与 port（带不带 scheme、IPv6 方括号都认），
  解析不出 host 或 port 的在配置边界报错。登录后的页面与片段一律 `Cache-Control: private, no-store`、`Vary: HX-Request`，
  缓存反代拿不到别人的页面，htmx 也不会拿到整页。

### 事件文案

界面上的活动条目不显示 `event_name`，显示按名字查表得到的文案；表里没有的名字原样显示。表覆盖 core 与各 contrib
现有的全部审计事件以及本计划新增的事件，作为 translator 的条目（键 `event.<event_name>`）：

| event_name | 中文 | English |
|---|---|---|
| `channel.inbound.persisted` | 消息已接收 | Message received |
| `channel.inbound.rejected` | 消息已拒收 | Message rejected |
| `channel.outbound.pending` | 准备发送消息 | Preparing to send message |
| `channel.outbound.queued` | 消息等待发送 | Message awaiting delivery |
| `channel.outbound.sent` | 消息已发送 | Message sent |
| `channel.outbound.partial` | 消息部分发送成功 | Message partially sent |
| `channel.outbound.failed` | 消息发送失败 | Message failed to send |
| `channel.outbound.unknown` | 消息发送结果未知 | Message delivery status unknown |
| `runtime.request.turn.started` | 开始处理消息 | Message processing started |
| `runtime.request.turn.failed` | 消息处理请求失败 | Message processing request failed |
| `runtime.request.turn.steer.accepted` | 追加消息已加入当前处理 | Follow-up added to current processing |
| `runtime.request.turn.steer.not_accepted` | 追加消息未加入当前处理 | Follow-up not added to current processing |
| `runtime.turn.started` | 运行时开始处理 | Runtime started processing |
| `runtime.turn.completed` | 处理完成 | Processing completed |
| `runtime.turn.failed` | 处理失败 | Processing failed |
| `runtime.turn.cancelled` | 处理已取消 | Processing cancelled |
| `runtime.turn.unknown` | 处理结果未知 | Processing outcome unknown |
| `runtime.process.start.requested` | 已请求启动运行时 | Execution service start requested |
| `runtime.process.reconcile.requested` | 已请求接管现有运行时 | Existing execution service takeover requested |
| `runtime.process.started` | 运行时已启动 | Execution service started |
| `runtime.process.reconciled` | 运行时已接管 | Execution service taken over |
| `runtime.process.failed` | 运行时启动失败 | Execution service failed to start |
| `runtime.process.unknown` | 运行时启动结果未知 | Execution service start outcome unknown |
| `runtime.process.stop.requested` | 已请求停止运行时 | Execution service stop requested |
| `runtime.process.stop.completed` | 运行时已停止 | Execution service stopped |
| `runtime.process.stop.failed` | 运行时停止失败 | Execution service failed to stop |
| `runtime.process.stop.unknown` | 运行时停止结果未知 | Execution service stop outcome unknown |
| `runtime.pool.banned` | 运行时因故障暂停使用 | Execution service suspended after a failure |
| `runtime.pool.released` | 运行时已恢复使用 | Execution service available again |
| `runtime.error_feedback.started` | 开始反馈错误 | Reporting an error |
| `runtime.error_feedback.sent` | 错误已反馈 | Error reported |
| `runtime.error_feedback.failed` | 错误反馈失败 | Error report failed |
| `approval.requested` | 已请求审批 | Approval requested |
| `approval.decided` | 审批决定已记录 | Approval decision recorded |
| `approval.failed` | 审批失败 | Approval failed |
| `tool.bcc.message.send.completed` | 消息发送操作已完成 | Message send operation completed |
| `tool.bcc.message.send.freshness_hold` | 消息发送已暂缓 | Message sending deferred |
| `tool.bcc.message.check.completed` | 新消息已查看 | New messages checked |
| `tool.bcc.message.read.completed` | 消息记录已读取 | Message history read |
| `tool.bcc.inbox.check.completed` | 收件箱已查看 | Inbox checked |
| `tool.bcc.thread.unfollow.completed` | 已取消关注讨论串 | Thread unfollowed |
| `bcc.send.fresh_check.passed` | 发送前检查通过 | Pre-send check passed |
| `bcc.send.freshness_hold` | 发送前检查未通过，发送已暂缓 | Sending deferred by pre-send check |
| `tool_call.started` | 开始工具调用 | Tool call started |
| `tool_call.completed` | 工具调用完成 | Tool call completed |
| `tool_call.failed` | 工具调用失败 | Tool call failed |
| `usage.updated` | 用量已更新 | Usage updated |
| `context.compaction.started` | 开始压缩上下文 | Context compaction started |
| `context.compaction.completed` | 上下文压缩完成 | Context compaction completed |
| `node.health` | 状态已更新 | Status updated |
| `reminder.scheduled` | 提醒已创建 | Reminder created |
| `reminder.updated` | 提醒已更新 | Reminder updated |
| `reminder.snoozed` | 提醒已延后 | Reminder postponed |
| `reminder.canceled` | 提醒已取消 | Reminder cancelled |
| `reminder.fired` | 提醒已触发 | Reminder triggered |
| `agent.start.succeeded` | 智能体已启动 | Agent started |
| `agent.start.failed` | 智能体启动失败 | Agent failed to start |

`tool_call.*` 的条目后接工具名（metadata 的 `call.name`），`channel.inbound.persisted` 后接会话名，`usage.updated`
后接累计 token 数；格式在模板里拼。表的完整性由测试保证：core 与 contrib 里出现的每个 event_name 常量都必须在
catalog 中有条目。

### 登录

首次启动时 `accounts` 为空，bcs 生成超级管理员 `admin` 与一个 24 字节随机密码（base64url），密码只在启动日志里打印
一次，并提示登录后修改。登录页是账号与密码两个输入框和「登录」按钮；登录成功后签发会话 cookie：`HttpOnly`、
`SameSite=Lax`、请求经 https 到达（反代带 `X-Forwarded-Proto`）时加 `Secure`、内容为 `账号 id:签发时间:密码指纹:HMAC`（密钥在首次启动时生成，存 `~/.bcs/session.key`，0600），
有效期 30 天。密码指纹是密码哈希摘要段的前 8 位。除登录页、静态资源与 `/node/*`（节点上报，走 token）之外的所有页面
和接口都要求会话，未登录一律回到登录页：整页请求 303 跳转，htmx 请求回 401 并带 `HX-Redirect` 头，让它整页跳。
登录页在壳子之外单独渲染（招牌加一个对话框）；登录成功回 204 加 `HX-Redirect: /agents`，失败把带错误的表单片段换回去。
登录时未知账号也做一次同样的哈希校验，让「账号不存在」与「密码错误」耗时一致。

设置模块本计划只有「修改密码」（输入当前密码和新密码，新密码至少 8 个字符，取 NIST SP 800-63B 的下限）和「退出登录」。改密成功后其它会话失效：cookie 里的密码指纹
不再匹配；当前会话则换发一枚新 cookie 继续。

### bcs 配置与命令

`~/.bcs/config.toml`：

```toml
listen = "127.0.0.1:8765"
retention_days = 30
storage = "sqlite"
```

受信电脑不在配置里，由界面接入写入库。

命令：`bcs run`（前台运行，读取配置，在进程内建好应用交给 uvicorn，不经 import 字符串、不靠环境变量传配置路径），`bcs --version`。

## Tasks

### Task 1：审计出口补全

`AuditEvent.as_payload()`；`IAudit` 加生命周期，审计工厂接受 `AuditContext`，`[node.<audit>]` 选项表；
turn 流的 `tool_call.*`、`usage.updated`、`context.compaction.*` 审计；入站/出站审计带正文（`text`）与附件清单；
`node.health` 定时审计；`_health()` 的 `audit` 字段；定时器的五个事件。
tests/support 增加记录事件的 `TestAudit`，e2e 用它断言一次真实 turn 产生的审计事件序列。

### Task 2：`contrib/server`

`ServerAudit`、`[node.server]` 配置、`bcn server connect`、发送任务、健康字段。
测试起一个真实的 bcs（`tests/server/_serving.py`）接收 `reportEvents`，验证顺序送达、bcs 不在或拒收时丢弃且不阻碍后续、积压分批、配置不可用时节点照常运行与健康字段。

### Task 3：bcs 服务端

`src/bazaar_compute_server/` 骨架、配置解析、`bcs run`、`reportEvents` 端点、鉴权与协议版本、
sqlite 存储、留存清理。打包：`module-name` 列表、`bcs` script、Release workflow 的冒烟增加 `bcs --version`。

### Task 4：界面壳子

样式变量与浅色/暗色、标识、头像、模块栏、智能体模块（列表、状态、悬停活动卡与其接口、选中）、电脑模块（列表、
接入、智能体列表）、离线置灰、事件文案 catalog 与完整性测试、中英文文案。

### Task 5：超级管理员登录

首次启动生成 `admin` 与密码、登录页、会话 cookie、页面与接口的登录要求、修改密码。

### Task 6：权限

`relations` 建表进初始迁移；接入时写 `computer_owner`，`reportEvents` 收到 `node.health` 时为新智能体写 `agent_owner`，
删除电脑时清关系；`Access` 中间件与 `@allowed` / `@sees` 装饰器；列表按主体过滤，详情、活动卡、接入命令、删除由装饰器 404。
测试用两个账号（第二个由测试直接写库）验证各自只见自己接入的电脑与其智能体。

### Task 7：真实运行验证

本机启动 bcs，用启动日志里的密码登录，改一次密码，在界面上接入当前电脑拿到接入命令，在本机执行 `bcn server connect` 后重启；验证电脑上线、智能体
列表与状态、悬停活动卡随一次真实 turn 更新且文案为中文、库中出现该 turn 的工具调用与两个 channel 的消息事件、
bcs 停止期间节点照常工作、错误 token 被拒绝、暗色模式。

## 验收标准

- `audit = "logging"` 的节点行为与改造前一致，日志中多出工具调用、用量、正文与 `node.health` 事件。
- 节点上报的每条事件在 bcs 恰好存一次，请求重试不产生重复。
- bcs 不可达期间节点的入站、出站与 turn 不受影响；那段时间的事件丢弃，恢复后新的事件照常到达。
- 界面上能看到电脑与智能体的当前状态，悬停活动卡显示最近活动与今日用量，文案不出现原始事件名。
- 离线的电脑与智能体在列表里置灰且点不开。
- 错误的 token 与不支持的协议版本得到明确的 `error_code`，并出现在节点 health 的 `audit` 字段里。
- 未登录打不开任何页面；改密后旧会话失效。
