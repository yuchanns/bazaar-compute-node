# Task 12：统一被动订阅轮询实施规格

状态：已实施，验收记录见 [task12-review.md](task12-review.md)。本文是 Task 12 的执行依据，代码路径相对仓库根目录。基线为 Task 11 完成后的实现；计划修订并入本分支最初的 plan commit。

## 1. 已核实的代码与决定

- `pages/agents.py:contacts()` 先比较 `latest_message_event()` 和在线状态，未变化返回 204，再调用节点 `contacts`。`history.py:news()/later()` 对单会话执行同一规则，未追平时保留旧 `since`。
- `contrib/sqlite/storage.py:latest_message_event()` 查询 `events.MAX(id)`；消息类型固定为 `channel.inbound.persisted`、`channel.outbound.queued`、`channel.outbound.sent`、`channel.outbound.partial`、`channel.session.reviewed`。
- `activity.py:event_lines()` 排除 `QUIET`，一次 50 条；带 `after` 时 SQLite 从游标之后升序取前 50 条，再倒序展示。因此活动游标必须取实际返回行的最大 ID。
- `fleet.py:agent_page()/fleet()/computer_view()` 已实现列表范围、权限过滤和状态派生。在线判断是 `now_ms() - last_event_at_ms <= 120000`；智能体工作状态来自 `_TURN_BOUNDARIES`。本任务复用这些读取和派生，列表检查仍有数据库查询成本，收益是消除无变化的 HTML 生成、浏览器片段请求及消息类节点读取。
- `computer_rows.html` 显示版本及“多久前上报”；版本由元数据订阅更新，时间经过由本地 clock 更新。智能体列表的成员集合、每个智能体的状态、每行姓名/图标分别由独立订阅负责；列表/详情头像呼吸灯直接消费相同状态通知。悬浮卡里的工作会话、时间、用量由独立订阅负责。
- 本地 htmx 4.0.0 `ajax()` 从 source 读取 `hx-get/hx-vals/hx-target/hx-swap` 等上下文；Promise 完成不等于请求成功。`htmx:finally:request` 中 `ctx.status`、`ctx.response.raw` 才用于确认，错误由内部捕获。`ctx.request.abort()` 可取消请求。
- `Gate` 已处理登录、同源 POST 和 1 MiB 请求上限；`Stale` 对带 `HX-Request: true`、旧 `X-Build` 的请求返回 204 + `HX-Trigger: stale`。新的 fetch 必须处理这些响应。

## 2. 模块与接口落点

| 文件 | 具体修改 |
| --- | --- |
| `polling.py`（新增，位于 `src/bazaar_compute_server/`） | Pydantic 请求模型；`Scope`、各 topic 的 seen 模型；状态摘要函数和各 topic 检查及状态通知 payload；每次请求内复用相同 scope 的读取 |
| `pages/poll.py`（新增） | `PollPages(storage, renderer, refs).check(request)`；解析请求、批量展开短 ID、检查权限、返回 JSON |
| `pages/__init__.py` | 构造 PollPages，注册 `Route('/poll', poll.check, methods=['POST'])`；同时注册下文单行、头部、提醒局部读取路由 |
| `storage.py` 与 `contrib/sqlite/storage.py` | 增加 `latest_activity_event()`、`latest_named_event()` 聚合接口；消息游标直接复用现有接口 |
| `rendering.py`、`activity.py` | _viewer 提供客户端时间翻译模板；ActivityLine 增加原始 at_ms，支持本地时钟格式化 |
| `pages/agents.py`、`pages/computers.py`、`pages/profile.py` | 生成初始订阅状态、局部响应的 `X-Poll-State`；保留实际数据接口及其业务语义 |
| `resources/static/poll.js`（新增） | 单例调度器、订阅表、被动回调分发、htmx 回调适配器、Alpine `x-poll` 指令 |
| `resources/templates/scripts.html` | 在 `components.js` 前添加 defer 的 `asset('poll.js')` |
| `resources/templates/shell.html` | body 增加 `data-build="{{ build }}"` 供 fetch 读取当前构建标记 |
| `resources/static/components.js` | application 接收 `poll-online` / `poll-offline`；增加 agentStatus/computerState/relativeTime 组件与 agentLink 绑定，activity 组合 agentStatus；具体订阅见第 9 节 |
| 下文 8 类定时区域、头像状态及元数据片段 | `x-poll`/状态组件/时间组件，data-poll 描述及初始状态，定时触发改成订阅回调调用 |
| `tests/server/test_poll.py`、`tests/server/test_poll_browser.py`（新增） | 真实存储/API 和隔离浏览器验收；原有页面用例同步模板变化 |

## 3. 请求和响应协议

`POST /poll`，JSON，`credentials: same-origin`，发送 `Content-Type: application/json`、`HX-Request: true`、body.dataset.build 对应的 `X-Build`、浏览器 IANA 时区 `X-Timezone`。

```json
{
  "subscriptions": [
    {"id":"1","scope":{"topic":"agents","until":null},"seen":{"digest":"abc..."}},
    {"id":"2","scope":{"topic":"events","computer":"12","agent":"34"},"seen":{"since":125}},
    {"id":"3","scope":{"topic":"agent-status","computer":"12","agent":"34"},"seen":{"status":"running"}}
  ]
}
```

```json
{
  "changes": [
    {"id":"2","state":{"since":130}},
    {"id":"3","state":{"status":"busy"},"data":{"status":"busy"}}
  ]
}
```

`id` 是客户端本次请求的关联键，服务端原样返回。无变化返回 200 `{"changes":[]}`。资源已不可见/不存在返回该项 `{"id":"2","unavailable":true}`，其余项继续处理。语法、字段、重复 id 错误返回 400 `{"error":"invalid_request"}`；限制沿用 Gate 的请求体上限。

Pydantic `Scope.topic` 使用 Literal 列出允许主题；Scope 的 after validator 按 topic 检查必需/可选字段，Subscription 的 after validator 用 STATES 表选择 seen 模型校验。各层模型均使用 `extra='forbid'`、strict=True。引用 ID 使用十进制字符串，沿用 Refs 最多 18 位规则；游标为非负整数。`seen` 可为 null，表示该消费者尚未展示任何快照，应通知一次；其余状态字段见下表。每个 topic 只接受自己的 scope 和 seen 字段。change.data 为通知携带的业务数据，当前 agent-status 提供 status，computer-state 提供 online/last_event_at_ms/queued，其他失效通知省略 data；回调既可以用 data 直接更新界面，也可以请求数据接口。

| topic | scope 额外字段 | seen / state |
| --- | --- | --- |
| `agents` | `until: string|null`，短 computer/agent 组合，格式与当前列表 edge 一致 | `{digest: string}` |
| `agent-status` | `computer, agent: string` | `{status: running|busy|failed|offline}`；通知 data 同样携带 status |
| `agent-info` | `computer, agent: string` | `{digest: string}` |
| `computers` | `until: string|null`，短 computer ID | `{digest: string}` |
| `computer` | `computer: string` | `{digest: string}` |
| `computer-state` | `computer: string` | state/data 都为 `{online:boolean,last_event_at_ms:integer|null,queued:integer|null}` |
| `computer-info` | `computer: string` | `{digest:string}` |
| `contacts` | `computer, agent: string; review: approved|pending` | `{since: integer, offline: boolean}` |
| `messages` | `computer, agent, thread: string` | `{since: integer, offline: boolean}` |
| `events` | `computer, agent: string` | `{since: integer}` |
| `activity` | `computer, agent: string` | `{since: integer, digest: string, day: string}` |
| `health` | `computer, agent: string` | `{digest:string,since:integer,day:string}` |
| `reminders` | `computer, agent, thread: string` | `{since:integer,offline:boolean}` |

`contacts.review` 区分消费区域；消息变化查询仍按整位智能体执行，才能同步审核数量。分页展示的 `until/last/after/below` 等数据请求参数继续由原 hx 属性计算，只有决定检查范围的列表 `until` 进入 scope。

一次 `Refs.expand()` 展开本批次所有短 ID。列表调用 `Access.allows('agents.view'/'computers.view')` 并按 `subject_id` 查询；对象项先检查 computer_owner，智能体项再检查 agent_owner，并用 `agent_view` 验证智能体属于该电脑。会话权限沿用现有 messages 接口的电脑/智能体访问边界。任何未通过或不存在的引用都返回同一种 unavailable。JSON 响应设置 `Cache-Control: private, no-store`。

## 4. 每类检查的确定算法

摘要统一使用 `sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()`；value 是下表给出的有序结构。列表按现有分页顺序，字段数组保留显示顺序。摘要是响应数据指纹，由客户端保存；服务端每次从数据库重新计算。

| topic | 本次读取及比较依据 |
| --- | --- |
| `agents` | 复用 `agent_page(storage, access, until=展开后的 until)`；摘要 value 为 `[members, page.more]`；members 每项仅 `[computer_id,id]`。新增、删除、可见成员范围变化才刷新整列；状态变化直接更新绑定，姓名/图标变化读取单行。 |
| `agent-status` | 从同电脑共享的 computer_view.agents 选择对应 AgentView，只比较 status；返回 state 与 data 都为 `{status: agent.status}`。列表头像、详情头像和电脑 roster 同一智能体消费相同通知。 |
| `agent-info` | 同一个 AgentView；摘要 `[computer_id,id,name,computer_name,system,list(channels),list(runtimes)]`，供单行与详情标题消费者读取元数据片段。 |
| `computers` | 复用 `fleet(storage, access, until=展开后的 until)`；摘要 `[有序 computer.id 数组, page.more]`；单行元数据与状态分别消费 computer-info/computer-state。 |
| `computer` | 复用 `computer_view()`；摘要 `[computer.id,computer.name,system,rows]`，rows 每项 `[id,name,list(channels),list(runtimes)]`；状态灯消费 agent-status，添加按钮与队列消费 computer-state。 |
| `computer-state` | 同一个 computer_view 的 online、last_event_at_ms、queued，逐字段比较并返回 data，供行/详情/接入提示直接赋值。 |
| `computer-info` | 同一个 computer_view 的 `[id,name,version,system]` 摘要，供单行元数据请求。 |
| `contacts` | `latest_message_event(computer,agent)` 与 agent.status 的 offline；当 current.since > seen.since 或 offline 不同则通知。 |
| `messages` | 同上，调用带 thread_id 的 `latest_message_event()`。 |
| `events` | 下述 `latest_activity_event(computer,agent,skipping=QUIET)`；current.since > seen.since 则通知。 |
| `activity` | `latest_activity_event(..., skipping=QUIET 中去掉 usage.updated)`；摘要 agent `[name,working_on,working_since_ms]`；day 用 `day_text(now_ms(), Renderer.zone(request))`。任一变化则通知。 |

时间经过由第 9 节的本地 clock 消费者处理，电脑列表成员摘要只比较成员集合。health 与 reminders 的确定查询见第 9.3 节。

新增存储方法签名：

```python
async def latest_activity_event(
    self, computer_id: str, agent_id: str, *, skipping: Sequence[str]
) -> int: ...
```

SQLite 执行 `SELECT COALESCE(MAX(id),0) FROM events WHERE computer_id=? AND agent_id=? AND event_name NOT IN (...)`，排除项来自 `activity.QUIET`，使用参数绑定。沿用现有 `events_by_agent(computer_id,agent_id,id)`；本任务沿用已发布 schema。`QUIET` 对活动流与卡片展示的过滤保持一致，card 额外关注 usage.updated 以更新用量。

请求内用以函数参数元组为 key 的字典复用读取结果：相同 computer_view、同 scope 的列表和事件游标只读取一次，seen 不进入读取 key，只参与结果比较。需要同一电脑多个 agent 时从一次 computer_view.agents 中选取。所有字典在 check 请求结束时释放。先实现正确的既有读取复用，验收记录实际 SQL 与网络成本。

## 5. 初始状态、局部响应和确认规则

每个被订阅的区域输出 `data-poll='{{ descriptor|tojson }}'`，descriptor 为 `{scope,seen}`。通过 Jinja tojson 放在单引号属性中。初始页面、完整局部重绘都从同一次读取到的展示数据生成 seen。状态组件也从同次 AgentView 输出 `{status}` 初值和四种状态的本地化 title 映射。`polling.py` 提供 `Descriptors(refs)` 作为模板的 poll 函数，并导出 agent_info/computer_state/computer_info/computer_detail/activity_state/health_state 等状态函数，供页面和检查端共同构造状态，摘要字段和时间换算只有这一份实现；消息/事件的 value 传实际已展示游标，列表的 value 传原 page/view。

- agent-status 直接回调赋值成功后确认通知 state；agent-info 从本次渲染的 AgentView 元数据计算摘要。
- agents/computers/computer：从实际用于渲染的 page/view 计算摘要。computer-state 使用实际 online/last_event_at_ms/queued。
- contacts：使用 `listing.since`、`listing.agent.status == 'offline'`；messages 使用 `history.since`、`history.agent.status == 'offline'`。silent/refused 的读取仍保留原消费进度；首次页面就是 silent/refused 时 seen 为 null，使后续检查能够触发重试。
- events：初始 `since=max(line.id, default=0)`；追加后为 `max(请求 after, 本次 line.id)`，必须只确认实际展示的行。
- activity：先取得事件游标，再读取 agent/活动/用量，day 在读前记录；响应状态使用该游标与本次 agent 字段摘要。查询期间新事件在下一轮检查仍会触发，允许安全地再刷新一次。

每个被动刷新数据接口成功时提供 `X-Poll-State: <紧凑 JSON>`。完整 HTML 同时更新 data-poll；events 的 afterend 响应依赖 header 更新原 `.fresh` 的 seen。204 只有在确认“已无新内容”时返回与检查一致的 X-Poll-State；silent/refused 导致的 204 沿用现有保留 DOM 行为，省略确认 header，让消费者下轮重试。

contacts/messages 存储检查比节点数据更早，继续使用现有保守 since。messages 一页未追平时 `later()` 已保留旧 since，原样写回 header；events 50 条满页时，header 只写本页最大 id。统一调度器下轮据此继续通知，直到追平。

列表向后分页后将已加载范围同步到订阅：回调适配器在关联 `htmx:finally:request` 后读根区域最新 `.edge.dataset.after`，更新 scope.until。分页只是扩大范围，保留旧摘要，下一轮触发一次整段刷新获得新摘要；该刷新继续保留选中项。此处是分页完成后更新注册参数，消费组件仍只通过回调接收变化通知。

## 6. 客户端订阅 API 与调度

`poll.js` 在 `alpine:init` 中注册 `Alpine.store('poll', manager)` 和 `Alpine.directive('poll', ...)`。manager 使用该次注册闭包保存 Map、计时器和 AbortController；页面只有一个脚本入口。协议如下：

```javascript
const subscription = manager.subscribe({scope, seen}, async (change, signal) => {
  // 只有匹配自己的变化时才进入；直接更新本地状态，或读取接口完成展示。
  return {seen: actualDisplayedState};
});
subscription.update({scope, seen}); // 展示完成或分页范围改变时
subscription.unsubscribe();        // Alpine 指令 cleanup 调用
```

消费者接收自己的 `change`（含业务 data），返回实际确认状态；批量响应的读取、遍历、匹配均属于 manager。回调不限于网络请求，直接状态赋值同样是完整消费者。普通组件通过 x-poll 的 htmx 适配器注册上述回调，现有 `x-data='selection'/'history'` 等保持各自状态，指令 cleanup 管理订阅生命周期。

调度步骤：

1. 第一位消费者加入时安排一次 0ms tick，合并同轮挂载；唯一 setTimeout 循环每秒分发本地 clock，到达 nextPollAt 且无在途检查才请求 /poll。/poll 完成设置 nextPollAt=当前时间+5000；慢速局部刷新与网络请求都不阻塞本地时钟。具体时钟规则见第 9.1 节。
2. 每轮拍下当前订阅记录及其 generation。按完整 `(scope,seen)` JSON 去重生成请求项，保存 id 到订阅记录数组的映射；同 scope 不同进度各自比较，但服务端共享读取。
3. `/poll` 返回后只向仍然注册且 generation 未变的记录分发。不同记录通过 `Promise.allSettled` 并发执行，管理器记录结果供错误处理；每个回调独立确认。
4. 每记录有 `running`、`pending`、`controller`。running 时收到通知只覆盖 pending；本次完成后，成功更新 seen，再处理最新 pending。若已达到通知中的事件进度且状态一致则清除 pending；摘要不一致仍补一次。失败保留 seen，等下一轮通知重试，避免立即死循环。
5. 同一记录 update 改变 scope 时递增 generation，旧检查响应失效；回调结果也按 generation 确认。unsubscribe 删除记录、清除 pending，取消该记录未完成的请求；最后一条网络订阅离开时中止 `/poll`；网络和本地 clock 订阅都为空才清除计时器。
6. 接到 unavailable，调用对应区域回调一次以走原接口的不存在响应，随后注销该记录。权限或资源变化后的列表订阅独立更新可见内容。

`fetch('/poll', ...)` 的状态处理固定为：200 解析 JSON 并发送 `poll-online`；204 且 HX-Trigger 包含 stale 时停止调度并触发既有 stale；401/HX-Redirect 跳转登录；网络失败和 5xx 发送 `poll-offline`，保留游标按下一周期重试；注销造成的 AbortError 不显示离线。400 属实现错误，停止这一批并记录错误，防止无效请求无限重复。403 停止调度并显示已有错误状态。所有路径 finally 都释放在途状态。

## 7. 直接状态消费者、元数据消费者与 htmx 适配

### 7.1 呼吸灯直接消费 agent-status

用户截图中的两处分别是 `agent_rows.html` 经 `components.html:list_item()/dot()` 生成的侧栏呼吸灯，以及 `agent_profile_head.html` 的详情头像呼吸灯。两者注册同一 `agent-status(computer,agent)`，相同初始进度合并成一个 /poll 请求项，调度器向两个回调分发。

状态组件的 data-agent 使用 `{scope,seen,labels}`，labels 四个 key 为 running/busy/failed/offline。`components.js` 定义 `agentStatus()` 返回 `{status, labels, subscription, init(), destroy()}`：init 从元素 `data-agent` 读取 scope、初始 status、本地化 labels，注册回调 `change => { this.status = change.data.status; return {seen: change.state}; }`；destroy 注销。完整 descriptor 的身份保存在 data-agent，morph 同对象时更新初值与订阅状态，切换对象时先注销再注册；在组件的 `htmx:after:swap.window` 绑定调用 sync() 读 data-agent，destroy 由 Alpine 自动清理绑定。

`Alpine.data('agentStatus', agentStatus)` 用于详情头像与 roster；已有 `Alpine.data('activity', ...)` 组合 `{...agentStatus(), left, top, move}`，供列表行保留头像悬停功能。没有 data-agent 的历史消息 activity 组件 init 直接结束。`dot(status, reactive=false)` 增加参数，reactive 时输出静态基础 class=dot，加 `:class="status"`、`:title="labels[status]"`。status 的 busy CSS 已有 breathe 动画，回调改状态即可启动/停止。

列表行使用稳定 anchor：`list_item()` 增加 `live_status=false` 参数；live_status=true 时即便初始 offline 也保留 href/hx-get，绑定 `agentLink`。绑定根据 status 设置 off class、aria-disabled 与 tabindex；capture click 在 offline 且不是 gear 点击时 preventDefault + stopImmediatePropagation，在线后恢复导航。这样离线恢复只改本地状态即可，原来 offline 输出 div 的结构必须一并调整。gear 按钮继续使用原独立逻辑。

电脑 roster 的行同样传 live_status 与 data-agent，并使用 agentStatus；详情头像 face-at 使用独立 agentStatus；活动卡状态灯和第一行 live 点同样绑定 status（live 点 `x-show="status === 'busy'"`），不为换灯重拉卡片。卡片工作对象/用量的变化仍由 activity 订阅刷新实际内容。

### 7.2 列表结构与姓名/图标分离

agents topic 只比较成员 ID 集合和 more。每个列表行额外用 x-poll 注册 agent-info；只有姓名、电脑名、系统、channels/runtimes 等元数据变化时读取这一行。详情头订阅同一个 agent-info，刷新自己的头部；状态变化只经过上一节回调。

新增两个精确局部接口：

- `GET /agents/{computer_id}/{agent_id}/row` → `AgentPages.row_fragment()`；使用与 show 相同的 allowed/expanded/sees，读取 agent_view、加载 refs，返回新增 `agent_row.html`，outerMorph 原行，X-Poll-State 为 agent-info 摘要。`agent_rows.html` 的 for 循环改 include 此片段。通过请求 selected 参数保留选中态；source 的 hx-vals 从当前 class on 生成 selected key。
- `GET /agents/{computer_id}/{agent_id}/profile/head` → `ProfilePages.head()`；相同鉴权，读取 selected 并返回现有 `agent_profile_head.html`（name=selected.name），outerMorph `#profile-head`，X-Poll-State 为同一 agent-info 摘要。路由注册在 profile/{tab} 前。

列表行已有导航 hx-get，元数据适配使用 `data-poll-url` 单独指定请求，适配器固定以该元素为 target、outerMorph 为 swap；新增通用 x-poll 不改变该行点击导航。片段内部活动卡继续 hx-morph-skip，局部更新保持展开/悬停。

### 7.3 请求 HTML 的回调适配器

`x-poll` 是通用订阅的 HTML 读取适配器，用于有 hx-get 或 data-poll-url 的源元素。初始化读取 data-poll，注册 refresh 回调；refresh 优先取 data-poll-url，以源元素为 target、outerMorph 为 swap，否则取原 hx-get 与上下文，调用 `htmx.ajax('GET', url, {source: element, ...显式配置})`；保留 hx-vals。行元数据请求显式设置 push=false、replace=false，避免继承导航 hx-push-url。在调用前安装只匹配此 source 的 before/finally 监听，捕获 ctx；通过 ctx.request.abort 将 signal 接到真实请求。await ajax 后从捕获的 ctx 检查 HTTP 状态与 `ctx.status`，读取 X-Poll-State 才确认 seen；finally 删除监听。

200 必须完成 swap，204 必须有确认 header。请求成功但没有确认 header 时返回未确认结果，保留原 seen；unavailable 分支无论原接口最终返回 404 还是加载成功，都由调度器在这次处理后注销旧记录，后续有效 DOM 通过自己的指令重新注册。htmx 捕获的网络错误也按失败处理。注销时只取消仍在发请求阶段的 ctx，已进入成功 swap 的旧元素 cleanup 不反向取消成功响应。目标绑定使用本次源元素解析出的 DOM，不用全局同名 ID 接收晚到结果。

在 `htmx:after:swap` 后，manager 对仍连接且与本次 target 有包含关系的已注册元素读回 data-poll，更新 descriptor；原地 morph 保留指令时也能同步新状态。外层替换则由旧指令 cleanup 和新指令初始化完成交接。完成请求时的 header 确认只更新仍属于同一 generation 的记录。

| 模板 / 源 | topic | 保留的数据请求与目标 | 注册时机 |
| --- | --- | --- | --- |
| `agent_list.html #agent-list` | agents | `/agents/list?selected=...`，现有 until hx-vals，outerMorph | 根元素存在期间 |
| `computer_list.html #computer-list` | computers | `/computers/list?selected=...`，until，outerMorph | 同上 |
| `computer_detail.html #computer-view` | computer | `/computers/{computer}/detail`，outerMorph | 同上；保留 agents-changed 主动操作事件 |
| `presence.html #presence` | computer-state | 直接更新灯与等待/上线文字 | 区域存在期间 |
| `contacts.html #contacts` | contacts | 原 contacts URL，since/shown/review/until/selected，outerMorph | 根存在期间，保留 selection |
| `history_end.html #tail` | messages | 原 messages URL 的 since/last/shown；有 last 时 outerHTML，否则目标 #history | tail 存在期间，保持原翻页与滚动事件 |
| `agent_profile_activity.html .fresh` | events | `/agents/{computer}/{agent}/profile/events`，after/below，afterend | 活动标签存在期间；空流 after 明确传 0 |
| `activity_card.html` 内层 hx-get 元素 | activity | `/agents/{computer}/{agent}/activity`，closest .card，innerMorph | 所属 `.agent` 或 `.turn` 悬停期间 |

上述八类原定时元素删除 every 5s，presence 改为直接状态消费者；普通订阅源设 `hx-trigger='poll-refresh'` 防止 hx-get 默认 click 被触发，回调仍直接调用 ajax。元数据行保留原点击 hx-trigger，因为订阅使用独立 data-poll-url。computer-view 保留 `agents-changed from:body`。活动卡在指令中定位最近 `.agent,.turn`，监听该宿主 mouseenter/mouseleave；首次加载时已悬停则立即注册，离开注销、重入重新注册；cleanup 移除宿主监听。现有外层卡片 `mouseenter once` 首次加载继续保留。

history 的 `.edge` revealed 分页、contacts 的 load、用户点击和表单提交保留原触发条件；这些是用户操作/初始读取路径。所有周期性自动检查统一由 `/poll` 发起。

## 8. 实施顺序与可重复验收

执行顺序固定为：服务端协议/状态查询 → 各数据接口状态标记 → poll.js 调度与 htmx 适配 → 成员列表/两处呼吸灯/events → 元数据局部接口、其余定时区域、本地时间、状态页/提醒 → 回归与报告。本 Task 全部完成后形成一条实现提交，等待 review；本规格修订始终归入最初的计划提交。

`test_poll.py` 使用现有 `_serving.serving_app/signed_in/enrol`，真实临时 SQLite，通过真实 `/node/reportEvents` 上报 `_health/_event` 格式数据。`test_poll_browser.py` 标记 e2e，使用同一隔离服务、`tests/server/_node.py` 的节点启动方式及 TestChannel/TestRuntime；浏览器使用 `/usr/bin/microsoft-edge`。浏览器脚本、截图、请求统计写入 `artifacts/bcs-poll-task12/`。故障使用 Playwright offline/CDP 实际网络延迟，服务重启只操作该测试创建的实例。

| 用例 | 操作与可观察结果 |
| --- | --- |
| 静默活动页 | 首次稳定后观察 30 秒，浏览器网络中周期性请求只有 /poll，list/events 无周期性 GET；记录各 URL 请求数。 |
| 普通活动与工作状态 | TestChannel 产生新活动；events 在下一轮追加；turn 真正开始/完成后侧栏与详情头像灯同步变化，两个原 DOM 节点保持身份，网络只有 /poll 与所需 events 请求，list/row/head 不因状态变化读取。 |
| 成员与元数据 | 新增/删除智能体触发 agents 列表读取；改名或改变图标字段只读对应 row/head；工作状态只触发状态回调。 |
| 大批活动 | 两次检查间写入超过两页可见活动；每次 events 从实际 after 继续，最终所有已写入事件均可在页面找到，日期分隔正确。 |
| 聊天/审核 | TestChannel 新会话、入站、出站、审核切换；联系人、待审数、空聊天变有消息、长历史追平，滚动及成员抽屉随真实内容更新。 |
| 多订阅分发 | 同一页面两个区域注册同 scope，各自刷新；给一条读取引入真实延迟/断网后恢复，另一条仍完成；延迟期间继续写入消息，恢复后都追平。 |
| 生命周期 | 多次导航、前进后退、切换会话/活动标签、列表 morph、卡片悬停进出；用网络记录证明只有一个周期请求源、已离开范围不再请求数据，晚到响应不覆盖新对象。 |
| 列表分页 | 加载第二页后新增/删除和改变已显示项目；整段刷新保留范围与选中项，more 状态对应真实列表。 |
| 电脑时间/在线 | 时间经过在本地更新文字、新上报通过 computer-state 更新基准；相同状态心跳不刷新智能体列表；停止专用节点、等待真实 120 秒过期后显示离线，重开恢复。 |
| BCS 重启 | 保持浏览器和专用数据库，同端口重开服务；原 cookie 与客户端进度继续生效，新持久化数据正常触发回调。 |
| 权限与删除 | 两个真实账号各拥有不同电脑；按各自合法订阅看到各自变化；资源删除后对应区域走 unavailable 处理，另一合法订阅继续工作。 |
| 登录/stale/失败 | 真实注销/密码变化触发登录；加载旧构建页面后切换测试构建触发 stale；断网恢复后继续刷新且进度未丢失。 |

检查命令：

```bash
uv run ruff check .
uv run ruff format --check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv run pytest tests/server
uv run --with playwright pytest -m e2e tests/server/test_poll_browser.py
node --check src/bazaar_compute_server/resources/static/poll.js
git diff --check
```

交付 `plans/2026-09-27-bcs-mobile/task12-review.md`，列出每类订阅的实际接入结果、静默/活跃请求计数、数据库读取成本、各用例结果及浏览器截图。复核八类模板的周期性自动刷新全部由订阅回调接管；实际执行的检查与未完成项分别如实记录。

## 9. 全部动态显示的接入清单

### 9.1 本地时钟消费者：时间经过直接更新文字

时间文字统一输出 `<time x-data="relativeTime" data-time='...'>首屏文字</time>`，descriptor 为 `{at: 毫秒时间戳, mode: ago|clock|due, labels: 本地化模板}`。新增 `components.html:time(at, mode='ago')` 宏；继续服务端渲染首屏文字，随后由客户端被动订阅 `clock`。

`poll.js` 管理器的同一个调度循环每秒唤醒一次，先给本地 clock 订阅者发送 `{now:Date.now()}`，到达 nextPollAt 且无在途请求才执行 /poll。网络结束设置 nextPollAt=当前时间+5000；网络延迟不阻塞本地时钟。只有本地 clock 消费者时只执行本地回调；网络订阅集合随 /poll 发送，本地 clock 主题留在浏览器。所有订阅清空后停掉唯一计时器。visibilitychange 恢复可见时补发一次 clock 并将下一次 poll 提前为当前时间，由同一调度循环执行。

relativeTime 在 init 注册本地 clock 回调，在 destroy 注销；回调仅在格式化文本变化时赋值到 x-text。morph 后重读 data-time.at，服务器发来新消息时间时替换基准。初始注册立即执行一次；不为每个时间节点设置计时器。

格式规则固定沿用当前 rendering.py：ago 的 <10秒/秒/分钟/小时/天取整阈值；clock 为当天 HH:mm:ss、其他日期 YYYY-MM-DD HH:mm:ss；due 为今天 HH:mm、明天 HH:mm、其他 YYYY-MM-DD HH:mm。通过 Intl.DateTimeFormat 的指定 timeZone 和 formatToParts 构造日期及时间，使用页面 X-Timezone 对应时区；按两个本地日历日期的 UTC 日期序号比较 today/tomorrow，避免夏令时按 24 小时相减。

`Renderer._viewer()` 提供 time_labels：通过现有 Translator.text 把 n 参数传为字面 `{n}`、clock 传为 `{clock}`，生成当前语言模板。浏览器用 replace 替换这两个占位，不复制翻译目录、不使用 innerHTML。新宏输出的 time 标签可安全作为 Jinja Markup 拼入原 subtitle。电脑“最近活跃：”同样把 when 传占位后拆成文本前后缀，让 time 成为实际 DOM 节点。

| 位置 | 基准字段 | 更新方式 |
| --- | --- | --- |
| `contact_rows.html` | item.last_activity_at_ms | ago；没有新消息也会从刚刚变成几分钟前 |
| `computer_rows.html` | item.last_event_at_ms | ago；新报告时间由 computer-state 通知更新 at，时间经过只改文字 |
| `agent_profile_files.html` 文件与目录两处 | entry.modified_at_ms | ago；保留目录开闭、文件列表，时间文字本地变化 |
| `agent_profile_status.html` | channel.last_ms / channel.since_ms | 分别 ago / clock |
| `review_card.html` | asked.at_ms | clock，跨日补日期 |
| `activity_card.html` 工作开始时间、五条活动时间 | working_since_ms / 各活动 created_at_ms | clock；ActivityLine 增加 at_ms，由 recent_lines 保留原事件时间，模板不再只拿预格式化 time 字符串 |
| `profile.html` 提醒时间 | next_fire_at_ms | due；跨日更新今天/明天。repeat_rule 文字只随提醒实际变更 |
| `history_rows.html` 与 `agent_profile_events.html` | turn/line.at_ms | 当前 time/day 是绝对 HH:mm:ss 与 YYYY-MM-DD，保留其静态显示和分页日期分隔 |

### 9.2 电脑状态通知与列表结构分离

增加 `computer-state`：scope `{topic,computer}`，state/data 同为 `{online:boolean,last_event_at_ms:integer|null,queued:integer|null}`。从同一请求共享的 computer_view 取值。消费者直接更新电脑行的灯/off、最近活跃时间基准、详情队列文字和“添加智能体”按钮 disabled，以及接入页面的等待/上线文字。队列文案由服务端提供带 `{queued}` 占位模板；0 时隐藏。online 的本地文字映射也随首屏 JSON 提供。

`computers` topic 使用 `[有序 computer.id, more]` 的成员摘要。新增 `computer-info` scope computer，digest 为 `[id,name,version,system]`。computerState 组件按 agentStatus 同样的 init/sync/destroy 注册机制保存 online、queued、last_event_at_ms，直接回调成功后返回 seen=change.state；时间元素通过父组件绑定 data-time 的 at，并由 relativeTime.sync() 在下一次 clock 回调读取基准。电脑行对 computer-info 变化请求 `GET /computers/{computer_id}/row`，由 `ComputerPages.row_fragment()` 返回新增 `computer_row.html`；原 computer_rows 循环 include 单行，保留当前选中状态。完整列表只因新增/删除/可见范围变化读取。

`computer` 详情摘要为 `[computer.id,computer.name,system,roster]`，roster 包含每位智能体 id/name/channels/runtimes；queued/online 使用 computer-state 直接绑定，每位智能体灯使用 agent-status。roster 成员和静态资料改变才读取 detail。

presence 使用 computer-state 直接消费者。原 presence 接口继续用于已有显式加载入口，订阅回调直接修改灯与文字。

### 9.3 已有状态与提醒来源的完整接入

增加 `health` scope computer/agent，state `{digest,since,day}`：digest 为 `agent_health()` 的 status/problems/channels 所有字段加 agent offline，since 是 usage.updated 最大 ID，day 为当前时区日期。用 `dataclasses.asdict(health)` 做稳定摘要；health 为 None 时编码 null。状态页根包在独立区域，订阅变化后请求现有 `/agents/{computer}/{agent}/profile/status` 到自身 outerHTML，成功确认实际 health/usage 状态。只有 status 标签存在时订阅。使用量和跨日归零均覆盖，普通工具事件不触发此读取。

增加 `reminders` scope computer/agent/thread，state `{since,offline}`。BCN 已在 commands/reminders.py 与 orchestration/reminder.py 上报 `reminder.scheduled/snoozed/updated/canceled/fired`，correlation.thread_id 明确为 owner_thread_id。新增存储接口：

```python
async def latest_named_event(
    self,
    computer_id: str,
    agent_id: str,
    *,
    names: Sequence[str],
    thread_id: str | None = None,
) -> int: ...
```

SQL 为按 computer、agent、可选 thread、event_name IN names 查询 COALESCE(MAX(id),0)；供 health 的 usage.updated 和 reminders 复用，参数绑定，使用已有 events_by_name/events_by_thread 索引。

将 profile.html 的提醒标题/数量/rows 抽成 `conversation_reminders.html`、根 `#conversation-reminders`。新增 `GET /agents/{computer_id}/{agent_id}/contacts/{thread_id}/reminders`，放在 AgentPages，同 profile 的参数与权限检查；先取提醒 since，再执行现有 review.reminders()，返回片段与 X-Poll-State。保留 actor/target/channel/name 查询参数。仅抽屉 open 时注册；关闭 x-show 时主动 unsubscribe，重新打开时注册。提醒回调只替换该小区，Focus 弹窗、成员列表与删除确认保持原节点；失败保留游标，下轮重试。提醒触发后的 next_fire_at_ms 由实际节点读取决定。

### 9.4 其余页面行为逐项归属

| 页面行为 | 变化来源 | 消费者动作 |
| --- | --- | --- |
| 智能体侧栏灯、Profile 头像灯、电脑 roster 灯、悬浮卡灯/live | agent-status | 同一通知分别改响应式 status；保留 DOM |
| 智能体名称/头像/渠道与 runtime 图标/系统 tooltip | agent-info | 单行或头部 HTML 消费者；agents.html 会话列标题也抽成 `agent_contacts_head.html`，新增 `/agents/{computer}/{agent}/head`，与 row 同一鉴权，局部更新该标题 |
| 电脑列表行状态、接入提示、队列、添加按钮 | computer-state | 直接绑定，不读取列表/详情 |
| 智能体与电脑集合 | agents/computers | 结构变化后读取原列表，保留分页和选中 |
| 联系人行/未读数/待审核数 | contacts | 读取现有接口，复用 pending-review OOB；消息/审核事件驱动 |
| 当前聊天标题/抽屉会话名 | contacts 片段完成 | 片段行加 data-thread/name/channel/kind；已选会话从该行读取更新显示及后续 profile URL 的 name，更新仅当前同 thread 的标题，用户文本用 x-text；离开已加载范围时保留当前名称 |
| 审核后当前页面与按钮 | 显式审核操作响应 | 继续现有 HX-Retarget、URL 和片段替换，contacts 订阅更新其他区域 |
| 消息内容、分页滚动、底部跟随 | messages + 实际 htmx 展示完成 | 原 history 组件继续 before/after 测量与补偿 |
| 抽屉成员数/成员表 | 已加载消息变化 | 原 conversation.gather() 在消息展示完成后收集并去重，不读成员接口 |
| 抽屉提醒数量/列表 | reminders | 只重取提醒区域；标题中今天/明天由本地 clock 更新 |
| 状态页运行状况、渠道连接/错误、今日用量 | health | 当前 status 标签局部消费者；时间文字另外订阅 clock |
| 悬浮卡活动文字/今日用量 | activity | 悬停时消费，实际事件/usage/day 改变才读取；灯独立消费状态 |
| 配置表单/模型/向导 | 用户输入、kinds/model 请求结果 | 保留 agentForm 的本地数据与显式读取；局部通知不覆盖未保存输入 |
| 技能列表 | 标签打开时节点读取的技能快照 | 保留打开标签时读取；节点当前没有完整技能文件变更事件，标签快照语义在清单中明示 |
| 工作区树内容 | 标签/目录打开、现有刷新按钮 | 保留已有快照/展开读取，文件时间用 clock；节点当前没有完整文件变更事件 |
| 文件夹展开/模型加载/复制反馈/登录保存 loading | 本地用户操作及已有请求生命周期 | 保留已有 Alpine 状态和计时反馈，其一次性计时器不承担周期性数据检查 |
| 连接不可达、登录失效、构建过期 | poll 与现有 htmx 响应 | 统一 application 状态、停止/恢复调度或登录跳转 |

新增局部路由均沿用原页面的 `allowed/expanded/sees`，在通配 `/profile/{tab}` 等之前注册。上述电脑/health/reminders 的模型、查询、头部确认遵循第 3–6 节同一规则；先读取保守游标，再读取对应数据。网络 topic 固定为 agents、agent-status、agent-info、computers、computer-state、computer-info、computer、contacts、messages、events、activity、health、reminders；本地 topic 为 clock。

### 9.5 补充验收

- 无新消息保持会话列表打开超过一分钟，文字按实际时间变化，DOM 行保持，网络只有统一 /poll。文件修改时间、渠道最近活动、电脑最近活跃同样验证；网络断开时本地时间仍更新。
- 真正修改电脑上报数据后，队列、上线提示、添加按钮、行灯更新；浏览器网络记录证明状态变化不读取整列。
- 状态页保持打开，通过真实健康报告改变渠道状态/错误及真实 usage 事件更新用量；提醒通过 TestChannel 的真实命令入口建立、延期、更新、取消并观察抽屉小区变化。
- 跨日格式用浏览器时钟测试覆盖 due 的今天/明天和 clock 补日期，时钟控制仅用于本地 formatter；业务离线仍等待真实心跳阈值。英语与中文保持原模板文案。
- 从真实网络统计和 DOM identity 断言区分三类消费者：本地 clock 更新、服务端状态 payload 直接更新、变化后 HTML 读取。完整验收逐项对应本清单。
