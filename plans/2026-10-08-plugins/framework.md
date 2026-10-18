# BCN 0.3 contrib 框架与首个模块

## 目标

在 `core/` 的 `bazaar-core` 包中提供统一的 Python 进程内 contrib 框架。Contrib 定义服务声明、生命周期函数和事件处理器，
函数通过公共 Context 提供和使用服务、发送事件。core 是框架运行内核，负责自举阶段、入口发现、配置与依赖校验、服务绑定以及完整启动和停止。
app 以普通 Python 调用提供启动输入与默认资源；具体功能由 contrib 接入。
节点启动流程实际使用这套框架，配置载体与日志以独立发行包接入，完成安装、发现、启用和使用。

新增业务服务的接口、查询、结果、调用入口和配置属于功能模块。宿主依据注册信息完成装配，
新增模块无需向 Context 增加业务字段，也无需向中心分发器追加业务分支。

本分支完整交付服务注册、依赖装配、本地绑定、事件和资源生命周期、独立状态查询模块、包发现、配置载体契约与默认 filesystem 模块、V5 配置及版本转换机制、
节点启动接入、模块及 agent 管理、默认调用／存储／消息／协作／定时／Reminder 和可替换 agent 组合。后续能力依照
[0.3 路线图](roadmap.md) 逐个交付新包。工程的分支与发行规则见 [总计划](README.md)。

## 迁移实施与验收标准

本计划全部 Task 以 main 主分支的原实现为迁移基线。提示词、运行行为、Actor 与文案保持一致；
只调整已经讨论确认的包归属、依赖声明与公共接口边界。实现前先核对 main 对应源码、调用链与资源，迁入原实现。
源码注释和 docstring 描述当前职责、行为及约束；迁移来源和主线对照记录在计划与验收资料中。
本轮确认会话准入由 collaboration 持久化审核状态，调用方经 sidecar 查询、通过或拒绝；新会话默认待审。
本轮另行确认 Task 17 在会话审核通过后触发一次正常入站通知，经 inbox notice 唤醒响应；具体接入和验收见该任务。

developer instruction 以 `src/bazaar_compute_node/core/instruction.py` 与 `resources/developer_instructions.md`
的原始资源、渲染规则、mode 分支及注入时机为准。本次明确批准两处显示调整：删除 `- Agent ID: {{agent_id}}`，
将 `- Runtime session ID: {{runtime_session_id}}` 改为 `- Session: {{runtime_session_id}}`。
验收以 main 原模板应用这两处调整后的内容与渲染结果为基准，逐字比对。
Actor 沿用 `src/bazaar_compute_node/core/actor.py` 的 `Agent`／`Thread`、`Actors` 与 `Mode`，
作为内部流通的身份原语，类型保持 `Actor = Agent | Thread`。
内部服务接口、编排调用、命令业务以及表示操作者归属的状态与容器键统一使用 Actor；
业务内部直接传递和匹配 Actor，不传递 `session_id`／`agent_id` 等字符串代替 Actor，也不在各层重新根据 id 或 mode 推断身份。
只有在入站、配置、进程协议及序列化等边界，才将 `session_id`／`agent_id` 与 Actor 相互转换：
边界入口构造或解析 Actor 后传入内部，边界出口将 Actor 转为外部协议所需的 id。

| mode | `Actors.for_thread(thread_id)` | 代表的业务范围与运行归属 |
| --- | --- | --- |
| `session` | `Thread(thread_id)` | 当前会话；不同会话具有不同 Actor，各自持有运行会话及调度状态 |
| `dangerous_individual` | `Agent(agent_id)` | 整个 Agent；该 Agent 各会话映射到同一个 Actor，按该 Actor 持有运行会话及调度状态 |

`Actors.for_thread(thread_id)` 在会话入站边界选择 Actor；`Actors.resolve(actor_id)` 在进程边界按当前 mode 恢复类型：`session` 恢复 `Thread(actor_id)`，
`dangerous_individual` 校验 id 等于本 Agent 后恢复 `Agent(agent_id)`。
main `core/runtime.py` 与 `core/orchestration/orchestrator.py` 以 Actor 为运行会话、runtime holder、状态、
队列、worker 和 timer 的归属键；迁移保留两种模式下的会话复用、隔离、串行调度及释放语义。
`core/orchestration/services.py` 与 `commands/base.py` 按该类型确定消息、未读与命令 target 范围：
Thread 处理自己的会话，Agent 处理该 Agent 存储范围内的会话。
跨进程传递的 actor id、当前 runtime session id 与会话 capability 沿 main `app/agent.py` 的活跃绑定进行校验，
恢复后的业务调用继续传递有类型的 Actor，保留身份、会话及命令可见范围的关联。

消息、调度、审批、提醒和命令沿各自 main 调用链迁移；bcc 与其他面向人或 agent 的文案沿用
`src/bazaar_compute_node/cmd/bcc/`、`resources/` 的原格式化实现、模板、字段、措辞和输出行为。

每个 Task 均以以上一致性要求作为验收标准，review 同时核对原源码与迁入实现；涉及模板时检查资源字节和实际渲染，
涉及行为与 Actor 时通过已有真实场景验收身份、路由、时序、结果与生命周期。发现偏差时回到该能力的引入提交修正，
满足标准后再完成该 Task 并交付 review。

## 首阶段的发行结构

首个日志发行包固定为 `bazaar-contrib-logging`，Python 模块为 `bazaar_contrib_logging`，模块入口为 `logging`。
它接收标准库 logging 记录，并纳入节点默认组合及可选 server 组合。
公共配置契约归 `bazaar-contrib-config`，默认载体归 `bazaar-contrib-config-filesystem`，入口为 `config-filesystem`。
配置版本服务归 `bazaar-contrib-config-versions`，提供 `config-v1` 至 `config-v5` 五个入口；载体与版本组合在完整配置读取前装配。
状态查询服务归 `bazaar-contrib-status`，入口为 `status`；模块管理服务归 `bazaar-contrib-manager`，入口为 `contrib-manager`；各包的分包与内置归属见
[路线图清单](roadmap.md#发行包模块入口与内置组合)。
app、core 和上述功能包在 BCN 仓库内分别具有 `pyproject.toml`、src、wheel、资源与验证流程。
app 发行名为 bazaar-compute-node，core 为 bazaar-core，必要包位于 contrib/<功能>/，随 BCN 使用同一版本；可选包在独立仓库发行并声明公共接口兼容范围。
首阶段采用 [本地 workspace 开发](README.md#本地-workspace-开发)，未发布依赖从共享 workspace editable 安装。
交付安装验证使用独立构建的 wheel，在 workspace 外的临时环境执行，并纳入 [版本发行清单](roadmap.md)。

## 装配结构

模块提供 typed 服务，消费者声明依赖，宿主按依赖进入 lifespan，并管理服务与事件监听的归属。
资源的创建、后台任务的管理和退出清理由模块的上下文管理器完成。
服务类型和共享 key 由功能契约包定义；框架通过通用 Context 完成注册和获取。
已安装的发行依赖与运行时启用实例分别记录，包操作统一交给宿主自身的 uv。
全部组合使用同一个 `ContribHub` 与 `bcn run`。`bazaar-contrib-server`、`bazaar-contrib-web` 与其他模块一样按配置启用，
服务端能力由 server 组合的模块提供；agent 可为空，也可与服务端模块同时运行。

统一模块入口承担服务注册和依赖装配。各阶段在新成员中实现业务与调用者，按新契约组成独立的 app／core／contrib 组合。
旧 src 与旧测试保留原样，全部新能力齐备、整体验收通过后统一切换并一次清理。各功能包独立持有实现与依赖。

## 生命周期与任务管理

core 要求 Python 3.15。ContribHub 是异步上下文管理器，拥有管理插件实例的 `asyncio.TaskGroup`；
`async with ContribHub() as hub` 进入后才可启用插件，`run(bootstrap, load)` 自动进入该宿主上下文。
每个实例在其独立 Task 中进入和退出完整 lifespan；实例内的 AsyncExitStack 与任务组按原任务、逆序退出。
生命周期任务和监听器 eager start，确保取消前已进入拥有者的异常与清理逻辑。
事件在宿主任务组并发启动，用 `gather(return_exceptions=True)` 等待全部监听器；取消发布或停止操作时取消所属监听器，
用 `asyncio.shield` 等待清理任务完成后继续传播调用者取消，再按依赖逆序收回服务。
初始化失败与运行故障按实例隔离。宿主沿已绑定的服务依赖计算传递闭包，停用这些实例的监听器、
取消在途回调和启动中的任务，再按依赖逆序结束 lifespan；独立插件及同一定义的其他独立实例继续运行。
并发故障及显式停止使用同一实例任务完成清理，消费者结束后才收回提供者的服务。
TaskFailureSignal.observe 接受 awaitable，由拥有者的任务组运行 guard，异常与意外结束经信号交给所属实例。
生命周期异常在进入宿主任务组的边界捕获；故障来源、实例位置、影响范围及清理错误通过标准 logging 记录。
此处依据任务组的异常取消规则在实例边界处理错误，避免把独立插件放进同一个故障传播范围。
core 的 SQLite、文件、TCP、子组合、故障和取消测试使用 pytest-asyncio；SQLite 资源使用 aiosqlite，
文件资源使用 aiofiles，TCP 资源使用标准库 streams，包级 pytest 配置选择自身测试目录。

## 框架契约

### 模块与公共类型

`bazaar-core` 导出 ContribHub、Contrib、ContribEntry、Composition、Candidate、Enablement、Context、ServiceKey、EventKey 与生命周期状态。
ContribHub 直接管理具体实例、服务依赖图、事件索引与任务；启用条目每次生成一个实例。
Contrib 声明 offers、accepts、可选参数模型、lifespan 和 listen；条目给出定义、参数及配置位置。
未声明 config 时，lifespan 只接收 Context；声明 config 模型时，core 校验条目参数后调用 lifespan(ctx, config)。
无配置模型的条目省略参数或使用空表，配置校验仍拒绝额外参数。
Context 提供实例 id／name、声明后注入的服务、事件、组合校验与更新，以及受本次生命周期管理的子组合启用。
app 持有命令、默认发行依赖与业务组合，普通插件管理自己的资源及子组合。

```python
@asynccontextmanager
async def server_lifespan(ctx: Context, config: ServerConfig):
    composition = Composition(
        entries=[
            ContribEntry(
                name="database", definition=sqlite, config={"path": config.database}
            ),
            ContribEntry(name="history", definition=history),
        ]
    )
    async with ctx.enable(composition, local=(DB,)):
        yield
```

`ctx.enable(composition, local=())` 返回异步上下文管理器；Hub.enable 提供相同启用语义。
进入前检查完整已声明目标，再按服务依赖启动；退出、异常或取消时等待回调并逆序释放资源。
子组合在创建者的 lifespan 内持有，创建者停止或失败时其后代也释放；此归属由具体插件实例记录。
嵌套启动允许子实例使用创建者已提供的声明服务，不等待创建者进入 yield；缺少尚未提供的创建者服务时报告启动依赖错误，防止父子互相等待。

### 本地绑定与实例声明

默认服务和事件直接以公共 key 作为绑定身份。local 接受 ServiceKey 和 EventKey，给指定 key 分配新的 UUID 身份；
创建者的其他绑定继续沿用，提供者与消费者始终使用同一公开 typed key。
本地 key 缺少提供者时报告缺失，同一绑定重复提供时报告冲突；绑定只查询选定身份。
事件以相同身份匹配监听器：各 agent 的消息事件可分别本地化，CONTRIB_CHANGED 默认共享。
业务插件代码继续通过 ctx.offer／ctx.accept／ctx.announce 与框架交互。

ContribEntry.bindings 接受 typed key→BindingRef 引用，用于在一份静态组合或不同子组合之间连接具体服务。
ContribEntry.local 为该条目及其后代声明私有 key，框架使用注册身份分配内部绑定；ctx.enable 的 local 则建立整个子组合的私有范围。
条目声明的 entry.ref(KEY) 引用其所属组合内的具名声明，框架按 name 与 location 定位，候选校验时检查目标唯一且提供该服务；
Enablement.ref(KEY) 引用启用句柄中的服务，框架在同一 Hub 的完整候选中选择该句柄所属的唯一提供者。事件引用沿用目标声明或组合的事件绑定。
BindingRef 保留 typed key 与声明位置或启用句柄，Pydantic 边界校验引用与映射 key 一致；框架在根选择和建图前解析实际绑定，解析循环和缺失沿用声明诊断。
组合作者只表达私有范围及提供者选择，UUID 由框架内部生成和维护；业务输入不携带装配标识。
配置更新保留未变注册及子组合的绑定身份，服务引用重新解析到候选中的实际提供者，消费者仍通过 accepts 与 ctx.accept 访问服务。
共享 DB、两个不同数据库后端及每个 agent 的执行实例都通过具体提供者与消费者的绑定表达。

agent 插件拥有实体模型与 AGENT 服务，从 config.agents 构造每个实体的局部组合；
所选 channel／harness 在对应接入 commit 中由 agent 组合按公共服务依赖与 provider 入口创建。
顶层 contribs 统一启用一次；同一实现可以用不同参数生成多个实例，选中的 provider 和完整已知依赖在资源启动前展开并预检。
动态 lifespan 创建的子组合在 ctx.enable 进入前检查其完整声明，校验结论只覆盖已经声明的输入。

### 公共契约类型命名

公共契约按能力命名：`TaskFailureSource`、`Audit`、`Channel`、`ApprovalHandler`、
`HarnessTurnStream`、`Harness` 和 `ThreadConcurrency`。`Timer` 表示计时服务，
`Timer.create()` 返回 `TimerHandle`；`ContribStatusQuery` 表示状态查询服务，
core 的 `ContribStatus` 保持表示状态快照。公共 `ServiceKey` 继续携带相应契约类型，
提供者通过 offer 发布实现，消费者通过 accepts 与 accept 取得有类型的实例。

统一更新定义、公开转导出、泛型 key、函数签名和现有消费方的引用，沿用各能力的真实资源与独立安装验收。
检查接口包、实现包、agent／messages 消费者与根目录类型检查，并在各能力的引入提交中完成签名修订。

### Python 3.15 导入策略

本分支新包与配套 Python 文件使用 Python 3.15 的显式模块级 `lazy import`／`lazy from`。
首次使用名称才加载模块；公开包的转导出延迟到访问所需名称，CLI 的 run 依赖延迟到执行该命令。
仅用于函数体或延迟注解的导入改为 lazy，类型专用导入直接在模块级声明。
模块初始化需要的基类、装饰器、模型字段与默认值、typed key 和全局实例保持立即可用；同一 from 的名称按实际使用拆开。
测试启动器的框架模型与资源在进入被监测的事件循环前解析，避免首次模型导入的文件访问发生在异步业务中。
Pydantic 模型创建和校验继续执行原有 schema 构造；发行发现仍通过实际 entry point 加载选定定义。
保留解释器支持的显式语法及逐模块声明；导入异常在真正使用依赖的位置进入现有加载诊断和资源清理流程。

从 workspace 与独立 wheel 验证所有包的新测试、公开转导出、配置模型、CLI 帮助／版本／run、真实资源及退出，
使用独立进程记录 core 与 CLI 的首次导入模块清单并对比实际延迟效果。各模块的修改归回首次引入它的 Task，
后续 Task 新增的导入按同一规则适配；运行 Ruff、根目录 LSP 和独立 sdist→wheel 验收。

### CI 支持平台

各发行包在自己的 `pyproject.toml` 中通过 `[tool.bcn.ci].platforms` 声明测试平台，
取值为 `linux`、`macos`、`windows`；缺省覆盖三系统。Unix sidecar 声明 Linux／macOS，Windows sidecar 声明 Windows。
workspace 成员读取并校验该配置，CI 规划器把受影响包展开为允许的 package／runner 条目。
工作流命名为 `CI`，规划任务命名为 `Plan`。单个 `Checks` 任务读取 Plan 输出的受影响包清单，
在同一环境中逐包执行 Ruff 和 Pyright/LSP；测试、system 测试及发行验证使用展开后的平台矩阵。
命名参考 [OpenDAL 的按职责划分工作流](https://github.com/apache/opendal/blob/main/.github/workflows/ci_check.yml)
及 [组件／平台测试项](https://github.com/apache/opendal/blob/main/.github/workflows/test_behavior.yml)。
矩阵提供成员目录名作为组件短名，测试任务显示为 `core / ubuntu-latest`、`sidecar-windows / windows-latest`。
GitHub Actions 通过 `fromJSON` 接收完整的 `matrix.include`，逐条创建已声明的组合，依据
[GitHub 动态矩阵文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations#using-an-output-to-define-two-matrices)。
真实临时 workspace 验证通用包、单平台包与 Unix 包的规划结果，保留各包的真实测试和发行安装验收。

### ContribHub 运行与组合更新

ContribHub.run(bootstrap, load) 先启用自举条目，再启用提供 COMPOSITION 的普通配置加载器，并将其提供的组合绑定到该加载器。
Hub 的异步上下文管理器持有一个任务组；应用在就绪后等待退出信号，业务退出后再释放加载器和自举资源。
Context.accept 仅取得自己的服务和已经声明、绑定并注入的依赖；其他实例的内部记录及其 Context 由 ContribHub 管理。
状态快照记录具体实例 id、name、配置 location 和父实例 id；日志保留发布实例及 Python 调用位置。

首次启用、validate、保存预检和 update 使用同一个完整目标构造及 Diff。
目标包含未修改的其他组合和实例；按已校验参数、定义、来源与绑定匹配既有注册，配置位置用于诊断。
保留实例的绑定身份和未变资源；服务提供者变化时沿实际依赖重新绑定受影响消费者。
创建者变化时撤销其动态子组合，新 lifespan 创建新的子实例；其他创建者拥有的组合继续保持。
新增服务可恢复先前缺少依赖的消费者，实际故障仍保留 Failed／Blocked 及来源。

Context.update 修改由该配置加载器产生的组合，Enablement.update 修改该启用句柄管理的组合。
更新由 Hub 任务组串行协调，退出时取消并等待在途请求；候选应用前保护请求者自身的 lifespan，避免请求一边等待一边结束自己。
图锁在应用变更和停止旧实例期间串行保护注册记录，等待新 lifespan 就绪时释放；子组合进入父实例的清理任务时只等待自己的后代，实例关闭状态支持重复加入。

### 生命周期、故障与事件

每个实例在同一 Task 中进入和退出完整 lifespan，AsyncExitStack 按逆序清理资源。
提供者到达 yield 时校验全部 offers；消费者等待其已绑定提供者就绪，然后接收具体对象。
候选应用前将实际服务 ready 等待与 lifespan Task 正在等待子组合启用的边一起检查，跨层级或多层嵌套的等待环在资源获取前报错；已就绪服务和祖先服务的直接提供不构成等待边。后台 Task 启用子组合不会被当作父 lifespan 的等待。
同批清理中，属于待停止实例的 Context 关闭子组合时只等待自己的子实例，避免后台 Task 等整批清理、父实例又等该 Task 的循环。
实例内的后台任务故障通过现有 TaskFailureSignal 传播到依赖者和所属子组合，独立实例继续工作。
停止前禁用监听器，取消或等待在途回调，再按依赖逆序退出；异常与取消等待资源清理后传给原调用者。
事件监听按绑定身份建索引，派发在 ContribHub 的任务组中并发执行、等待所有结果；监听器异常返回发布者，由 Hub 保持其他实例运行。
状态变化通过 CONTRIB_CHANGED 发布，私有事件由组合指定 local。

### 状态查询模块

`contrib/status/` 的 `bazaar-contrib-status` 独立发行包导出 `ContribStatusQuery`、`CONTRIB_STATUS` 和 `status` 定义，
只依赖兼容范围内的 `bazaar-core`，首版为 `bazaar-core~=0.3.0`。`status` 在 `bazaar_compute_node.contribs` 登记入口，声明 `offers=[CONTRIB_STATUS]` 和空配置，通过普通 lifespan 提供查询服务。
`ContribStatusQuery.snapshot()` 同步返回 `tuple[ContribStatus, ...]`，每次读取 `ctx.snapshots` 的状态记录；
已返回的快照保留查询时的状态，包含仍被宿主保留的 failed、blocked 记录。
core 持有状态记录、数据类型和变更事件；查询服务的协议、key 与插件实现由 status 包持有。
app 默认组合和 server 的本地组合分别启用 `status`，消费者声明 `accepts=[CONTRIB_STATUS]` 并通过 accept 查询，
这些 status 实例均通过 ctx.snapshots 查询所在 ContribHub 的全部实例状态，local 控制查询服务的绑定。状态通知仍订阅 core 的 `CONTRIB_CHANGED`。

### 发现与配置

公共 entry point group 为 `bazaar_compute_node.contribs`，入口返回上述 `Contrib` 定义。
模块以功能名称导出 `Contrib` 定义，例如 `storage`、`database`、`recorder`；入口声明引用模块路径与对应的导出对象名。
`core` 的宿主通过 `importlib.metadata` 读取当前 Python 环境中的发行包及其入口，建立
`发行包 → 入口名 → 导入对象` 的对应关系。启动时以配置中的 `entry` 值查找入口，再调用 `EntryPoint.load()` 取得定义。
管理服务可以在独立的目标解释器进程中加载定义，取得配置模型、API 兼容信息和服务声明。
实例资源在宿主进入 lifespan 时创建；已安装列表通过元数据展示包和入口。
包名、版本、入口名和导入对象来自发行元数据，安装源来自配置中的依赖声明。
同名入口冲突在加载边界报告具体发行包、版本和入口，模块定义使用该入口名作为管理及诊断名称。

入口分组与发现协议由 core 持有。`core/README.md` 提供登记、发现、加载和启动的简明示例；
完整插件开发指南放在 `core/docs/plugin-development.md`，说明统一分组、可自取的发行包及入口名、
多入口包、导出 `Contrib` 对象、配置模型、服务与事件声明、本地绑定和 lifespan 的资源清理。
社区插件使用同一分组登记，并安装到运行宿主的 Python 环境；登记与 uv、pip 或具体构建工具无关，
遵循 PyPA entry points 规范。
后续站点发布安排见 [文档站点](README.md#文档站点)。

```toml
# 独立模块发行包
[project.entry-points."bazaar_compute_node.contribs"]
logging = "bazaar_contrib_logging:logging"
```

`entry` 选择 entry point 的名字，发行包名用于包管理。一个包可以提供多个入口，
这些入口统一在顶层配置；组合按已校验的业务输入明确创建各实例，框架按服务绑定建立依赖。

```toml
# 节点 config.toml 的节选。
version = "5"

contrib_indexes = [
    "/home/user/.bcn/contrib/catalog.json",
]

[[contribs]]
[[contribs.enabled]]
entry = "storage"

[[contribs]]
[[contribs.enabled]]
entry = "sqlite"

[contribs.enabled.config]
path = "bcn.sqlite3"

[[contribs]]
[[contribs.enabled]]
entry = "database"

[contribs.enabled.config]
backend = "sqlite"

[[contribs]]
[[contribs.enabled]]
entry = "logging"

[contribs.enabled.config]
level = "info"

[[contribs]]
[[contribs.enabled]]
entry = "recorder"

[[contribs]]
[[contribs.enabled]]
entry = "agent"

[[contribs.enabled.config.agents]]
name = "A"

[[contribs.enabled.config.agents]]
name = "B"
```

示例为配置节选，完整 agent 字段及 channel／harness 选择和参数由 agent 插件与所选 provider 的模型定义，保存到该条目的 config.agents。
storage、database 来自 bazaar-contrib-storage，
SQLite、日志与 recorder 由各发行包声明入口。
storage、sqlite、database 和 logging 各创建一个默认共享实例。agent 组合使用普通 Python 函数从 config.agents 生成具体执行与 provider 条目；每个 agent 的私有服务和消息事件使用稳定绑定，DB 和公共审计事件可以共享。
组合的 lifespan 使用 `ctx.enable(composition, local=(...))` 管理需要动态创建的子资源；全部已知静态条目显式展开后交给候选校验，动态子组合在启用边界校验。
provider 由业务 builder 根据输入选择，运行资源由被创建的具体实例管理。

filesystem 用户可以直接编辑 config.toml；CLI／web 页面选择入口、填写参数后，调用同一配置管理服务，经当前 CONFIG_STORE 保存。
启用配置统一存于顶层 contribs；agent 插件拥有 agent 列表及 channel／harness 参数。管理服务校验模型和已展开的完整依赖图，再条件提交配置。
配置列表展示来源声明，运行列表展示它生成的实例及状态；编辑来源条目影响其派生实例。具体实例的启停由拥有它的组合管理。

### 配置载体与启动自举

`bazaar-contrib-config` 是公共配置契约包，导出 `ConfigStore`、`ConfigSnapshot`、`CONFIG_STORE`，
`ConfigConverter`、`ConfigCandidate`、`CONFIG_CURRENT`、`CONFIG_V1` 至 `CONFIG_V5` 及
`ConfigConflict`、`ConfigUnavailable`、`ConfigFormatError`、`ConfigCommitUnknown`。载体负责文档读写、序列化与条件提交；
版本模块负责历史输入识别、逐版转换、字段映射和候选生成；配置包通过 `CONFIG_CURRENT` 指定与其文档模型一致的当前版本服务，普通加载器消费该服务，
经 core 的通用模型／服务依赖校验后组织条件提交。载体只处理数据序列化与存储，不解释业务字段或格式版本。
core 的运行接口执行自举与加载阶段，app 提供存储后端启动输入和业务默认资源，版本包提供 `VERSIONS` 装配清单。CLI、管理页面、组合导入和升级预检使用同一管理入口、版本服务与当前载体。

```python
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from bazaar_core import ServiceKey


@dataclass(frozen=True)
class ConfigSnapshot:
    payload: Mapping[str, object]
    revision: str


class ConfigStore(Protocol):
    async def read(self) -> ConfigSnapshot | None: ...

    async def write(
        self, payload: Mapping[str, object], *, expected_revision: str | None
    ) -> ConfigSnapshot: ...


CONFIG_STORE = ServiceKey[ConfigStore]("config.store")
```

`read()` 仅在所选来源尚无配置时返回 None，连接或权限失败抛出 ConfigUnavailable，
文档解码失败抛出 ConfigFormatError。快照内容是独立的配置文档数据，宿主复制它生成候选；
revision 是载体返回的不透明修订标识，与文档的结构 version 分别管理。
`write(expected_revision=None)` 仅在配置不存在时创建；传入修订标识时仅替换仍匹配该快照的配置。
检查和提交由载体作为同一个条件操作完成，竞争失败抛出 ConfigConflict。
普通启动按输入与目标结构版本判断是否需要迁移回写；版本相同时直接加载候选，旧版本经过候选检查后条件提交。
加载、默认配置创建、迁移和组合导入遇到修订冲突时直接向调用者报错；调用者重新读取、校验后决定是否再次提交。
提交成功返回实际提交后的快照；明确未提交的失败保持先前配置可读取。
写入后响应丢失等结果未知的情况抛出 ConfigCommitUnknown，管理入口重新读取并核对当前内容与修订，
显示已提交、冲突或仍无法确认的实际状态，后续操作依据该状态继续。
宿主记录载体入口、旧／新修订和变更摘要。

默认发行包为 `bazaar-contrib-config-filesystem`，模块为 `bazaar_contrib_config_filesystem`，
入口为 `config-filesystem`，声明 offers=[CONFIG_STORE]，由 app 的发行依赖安装。
它使用自身的 FileConfig 模型将 options 校验为路径序列，使用最后一个路径；未指定时使用 `(BCN_HOME 或用户主目录)/.bcn/config.toml`。
读取 TOML 并以原始字节摘要作为 revision；
经该接口提交的写入在同一配置锁内复核当前摘要，
以同目录临时文件、fsync 和原子替换提交 TOML。
权限及父目录处理复用现有平台规则，POSIX 上配置权限为 0600。来源没有配置时，宿主生成 V5 默认候选并以条件创建提交。

默认载体沿用统一模块生命周期，FileConfigStore 实现上述文件读写契约：

```python
import os
from contextlib import asynccontextmanager
from pathlib import Path

from bazaar_contrib_config import CONFIG_STORE
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: tuple[Path, ...] = ()


@asynccontextmanager
async def lifespan(ctx: Context, config: FileConfig):
    path = (
        config.options[-1]
        if config.options
        else Path(os.environ.get("BCN_HOME") or Path.home()) / ".bcn" / "config.toml"
    )
    async with FileConfigStore(path) as store:
        ctx.offer(CONFIG_STORE, store)
        yield


filesystem = Contrib(
    config=FileConfig,
    offers=[CONFIG_STORE],
    lifespan=lifespan,
)
```

```toml
# bazaar-contrib-config-filesystem 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
config-filesystem = "bazaar_contrib_config_filesystem:filesystem"
```

自定义载体依赖 bazaar-contrib-config，在相同 entry point group 登记自己的入口，声明并实际提供 CONFIG_STORE。
app 将按传入顺序收集的原始字符串序列绑定到载体模型的 options 输入；每项的格式、解析、校验和默认值由所选载体定义。
其配置模型解析连接地址、配置标识、凭据环境变量引用等作者需要的参数；客户端库属于自己的发行依赖，
连接由 lifespan 创建并在退出时关闭。配置中心载体按其后端实现条件写入和旧版本保留。
宿主通过公共接口读写，实际协议与客户端实现由载体拥有。

启动选择固定使用以下输入规则，配置命令与升级入口复用相同选择：

- `--configer <entry>` 选择已安装入口；缺省为 config-filesystem。
- `--configer-options <option>` 可重复使用，按出现顺序提供所选入口的原始 options；缺省为空序列。
  格式解析和 Pydantic 校验由该载体的模型在装配边界执行。
- `--config <option>` 是 --configer-options 的别名，可重复使用并与其混用；帮助说明为 `Legacy alias for --configer-options.`。
  filesystem 将每项解释为路径，最后一个路径生效；其默认路径也由 filesystem 处理。

以下第三方入口示例采用该载体约定的 key=value 格式：

```sh
bcn run
bcn run --config /srv/bcn/config.toml
bcn run --configer acme-config --configer-options url=https://config.example.com --configer-options key=node-a --configer-options token_env=ACME_CONFIG_TOKEN
```

core 的 ContribHub 从 Python 发行元数据发现入口，检查 API、配置参数和期望服务。配置载体先提供 CONFIG_STORE，版本链提供 CONFIG_CURRENT，然后普通加载器读取并转换文档。
载体的连接资源由自己的 lifespan 创建，所声明的依赖按当前完整图校验。加载器提供经校验的 `COMPOSITION`，在同一个 Hub 装配业务条目；业务和配置操作结束后按依赖逆序释放版本服务与载体。

### 配置版本模块

`bazaar-contrib-config-versions` 的模块为 `bazaar_contrib_config_versions`，由 app 以各自所需的三段式兼容范围安装，首版为 `~=0.3.0`。
五个入口省略配置模型，lifespan 只接收 Context，分别提供公共包中唯一的 typed key。
本次发行的自举组合选择 config-v5，按下表依赖先装配 V1，再逐个装配到 V5；
载体选择来自启动输入，版本组合来自发行定义，二者都先于完整业务文档装配。
版本包导出 `VERSIONS`，保存五个入口名及期望服务；`CONFIG_CURRENT` 当前引用 `CONFIG_V5`，由配置契约与模型共同维护。
app 直接装配这份清单，宿主按已安装元数据取得定义并依据 accepts 建立依赖图。新增版本时更新配置模型、当前服务引用和版本包的清单。

| 入口 | accepts | offers | 本版服务负责的行为 |
| --- | --- | --- | --- |
| config-v1 | 空 | CONFIG_V1 | 识别和解析 V1；BCN 文档缺省 version 作为 V1 输入 |
| config-v2 | CONFIG_V1 | CONFIG_V2 | 将支持的旧输入转换到 V2，持有 V1→V2 转换 |
| config-v3 | CONFIG_V2 | CONFIG_V3 | 将支持的旧输入转换到 V3，持有 V2→V3 转换 |
| config-v4 | CONFIG_V3 | CONFIG_V4 | 将支持的旧输入转换到 V4，持有 V3→V4 转换 |
| config-v5 | CONFIG_V4 | CONFIG_V5 | 将支持的旧输入转换到 V5，持有 V4→V5 字段映射及所需包生成 |

各 key 的类型为 `ServiceKey[ConfigConverter]`，诊断名采用 config.version.1 至 config.version.5。
`ConfigConverter.convert(payload: Mapping[str, object]) -> ConfigCandidate` 接收原 BCN 文档，返回目标版本内容、
字段变更摘要与 required packages；候选模型和包需求类型由 bazaar-contrib-config 定义，包规格使用已确定的 package/source/版本范围。
每版收到已是本版的输入时返回该版本候选；更早输入先调用前一版的 convert，再执行上一版到本版的转换，
各步合并已有摘要及包需求。目标服务在文档读取边界诊断不支持的版本。
例如宿主把 V3 文档交给 CONFIG_V5：V5 调 V4，V4 调 V3 取得原内容，然后只执行 V3→V4→V5。
新版入口和已有入口都保持实际服务供给与 ContribHub 的就绪校验。

V5 模块的完整登记方式如下；V2 至 V4 按相同方式取得上一版服务，V1 提供最初解析服务。
V5Converter 的 convert 按上面的规则调用上一版，并执行本节的 V4→V5 映射。

```python
from contextlib import asynccontextmanager

from bazaar_contrib_config import CONFIG_V4, CONFIG_V5
from bazaar_core import Context, Contrib


@asynccontextmanager
async def v5_lifespan(ctx: Context):
    ctx.offer(CONFIG_V5, V5Converter(ctx.accept(CONFIG_V4)))
    yield


v5 = Contrib(
    accepts=[CONFIG_V4],
    offers=[CONFIG_V5],
    lifespan=v5_lifespan,
)
```

```toml
# bazaar-contrib-config-versions 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
config-v1 = "bazaar_contrib_config_versions:v1"
config-v2 = "bazaar_contrib_config_versions:v2"
config-v3 = "bazaar_contrib_config_versions:v3"
config-v4 = "bazaar_contrib_config_versions:v4"
config-v5 = "bazaar_contrib_config_versions:v5"
```

已发布的前三步转换内容及历史行为保留，分别由 V2／V3／V4 服务持有。
下一版追加 config-v6、CONFIG_V6 和 V5→V6 转换，更新配置契约的 CONFIG_CURRENT、文档模型及版本包的 VERSIONS，前五版继续提供其原有转换服务。
旧文档中的 version 标识输入格式，自举目标入口标识本次发行需要的输出格式。
版本模块与载体的连接资源、服务归属及退出使用同一个 ContribHub；随本次宿主退出，在业务及在途配置操作结束后释放。

### V5 配置与版本转换

配置文档以顶层 `version="5"` 标识结构版本，与 BCN／模块的发行版本分别管理。
顶层保存 `version`、`contrib_indexes` 和统一 `contribs`；默认 agent 的实体配置归 `entry="agent"` 条目的 `config.agents`。本地传输地址、数据库、
审计和版本检查等能力参数保存在其所属模块的 config。
默认 agent 模型保留 id、name、mode、idle_timeout、channel 数组及 provider kind 和业务参数的实体含义；旧 runtime 数组映射到该模型的 harness 数组。
运行实例由组合明确声明，内部标识由宿主生成，私有绑定由组合持有。

版本服务沿 V1→V2→V3→V4→V5 推进，保留前三步的已发布转换，由 V5 服务追加 V4→V5 转换和目标版本候选。
版本缺省的既有 BCN 文档沿 V1 入口处理，载体返回 None 时直接生成 V5 默认组合。
V4→V5 在内存中生成候选配置和 required packages；
用原字段的有效默认值补全输入，再根据以下映射形成对应入口及参数。
官方旧 provider 的名称到新入口使用本节的明确映射表，转换在配置边界执行并接受相同 Pydantic 校验。
额外旧 provider 没有既定映射时，保留原快照，列出其原入口、参数位置及目标模块配置需求，由用户选择对应的新入口。

| V4 输入 | V5 结果 | 保留与默认规则 |
| --- | --- | --- |
| node.storage="sqlite"、node.database_name | storage、sqlite、database；database.config.backend="sqlite"，sqlite.config.path 为原数据库位置 | 缺省使用原 bcn.sqlite3；相对路径按原数据目录解析，保持同一数据库文件 |
| node.audit="logging"、node.logging | logging 与对应 config | 保留日志参数；默认 audit 仍映射 logging |
| 每个节点的本机通信 | commands、sidecar 及本机实现入口 | 承接 0.2 的 bcn.sock 通信，默认启用；与 node.control 的选择无关 |
| node.endpoint | 所选 sidecar 实现的 config.endpoint | Unix 保留路径，Windows 按原路径身份生成原生管道地址；缺省由实现包提供本机地址 |
| node.version_check | node-management.config.version_check | 显式 false 保持 false，缺省为 true |
| node.lang | — | V5 文案由所属功能包持有，默认使用英语 |
| 原 agent、channel／runtime 配置 | agent.config.agents 及其 channel／harness 数组；对应公共登记与 provider 启用条目 | id、name、顺序、mode、超时、模型、effort、sandbox、env、凭据引用及 provider 参数逐项保留 |

执行、消息、协作、Reminder 等原有固定能力写成顶层模块条目；channel／harness 注册按原有 provider 选择生成。
server／starlink 相关字段随第 11 阶段交付对应功能时补充 V4→V5 映射；已有 V5 读取保留原启用列表。
默认 agent 的配置整体写入 agent 启用条目的 config；原 runtime 字段转为 harness，按 provider 模型保留数组顺序和参数。
Telegram、Lark、WeCom 分别映射到同名 builder 和运行入口；Codex 映射 codex-builder／codex，
Claude Code 的原 kind="claudecode" 映射 claudecode-builder／claudecode。
不同 agent 使用同一 provider 时复用相同定义，各输入保留自己的参数，由组合明确创建独立实例。
node.server 被审计和控制共用时只生成一个 starlink 配置，原参数表中的每个字段明确归入该入口的连接、事件上报或请求设置。
事件上报与远端请求独立选择；原 audit="logging" 的节点仍保留 logging，原 control="none" 的节点保持远端请求停用。
未被原 audit／control 选择的 logging／server 参数表按原读取行为忽略。旧存储实现固定为 SQLite，迁移继续指向原数据库。
已设置的自定义 provider 使用已确定的映射和目标入口，缺少对应关系时提示用户补齐该部分配置。

每个被转换字段都记录目标配置位置；没有映射的非空字段或歧义返回具体位置及处理建议，原快照保持可恢复。
凭据引用、环境映射、数据目录、工作区、数据库文件和 agent 身份继续指向原资源。
转换产生新配置与包需求，数据迁移由相应 schema 模块按原 ledger 执行。
版本读取、状态推进、V4→V5 映射和候选生成归配置版本模块；配置包的普通加载器经 `ctx.validate()` 校验候选后通过 CONFIG_STORE 条件提交，各能力阶段同步其字段映射及新配置模型。
首阶段交付 V5 模型和版本链、候选及提交机制；数据库、provider、远程和管理阶段分别补齐映射和产物，
通过真实已交付组合验证其转换。最终发行阶段使用全部正式模块完成完整旧配置升级。

升级与首次加载使用以下顺序：

1. 根据自举选择装配 CONFIG_STORE 和版本包的 VERSIONS 组合，读取原快照与修订，调用 CONFIG_CURRENT 服务在内存中推进到当前格式，生成候选配置、字段变更摘要及所需包清单。
2. 升级使用含目标宿主、所选载体包和固定版本组合的隔离预检环境，转交同一自举参数，由目标解释器执行迁移预检，
   将所需包和保存的默认／可选／第三方依赖一起交给自身 uv，
   保持已有来源与版本固定；所需包在最终安装前加入同一个候选 pyproject 和 pylock。
   额外 provider 使用其已有来源或用户选定来源。
3. 安装完成后在目标解释器加载入口定义，以 Pydantic 校验配置，展开实例与 accepts 并检查完整依赖图。
   直接 bcn run 加载旧文档时使用已安装环境完成同样检查；缺少包时报告所需包和安装操作，待安装后重试。
4. 使用读取时的 revision 调用 CONFIG_STORE.write，条件提交由载体完成。
   ConfigConflict 时重新读取，重新执行转换及全部候选校验后再提交；更新后的所需包一并重新解析。
5. 记录转换结果、载体、旧／新修订、所需包与配置摘要；V5 后续读取使用已保存文档，
   启用条目、停用选择和后端参数继续保留。

配置并发保护由载体的条件提交完成，依赖解析和安装使用同一环境操作锁。
版本推进和候选校验期间保留原快照，包安装失败保留原配置及声明并报告实际环境状态。
未知未来版本在读取边界诊断；重复读取成功迁移后的 V5 得到相同启用配置。
V5 手写文件、CLI、页面和显式配置导入使用同一模型与保存入口。
默认组合用于新建，组合导入按已有条目保留参数、停用及 DB 选择。

以下展示 V4 存储与管理配置及对应的 V5 节选，其余能力按映射表同步生成：

```toml
# 升级前的 V4 节选。
version = "4"
[node]
storage = "sqlite"
database_name = "bcn.sqlite3"
audit = "logging"
version_check = false
```

```toml
# 升级后的 V5 节选。
version = "5"

[[contribs]]
[[contribs.enabled]]
entry = "storage"

[[contribs]]
[[contribs.enabled]]
entry = "sqlite"
[contribs.enabled.config]
path = "bcn.sqlite3"

[[contribs]]
[[contribs.enabled]]
entry = "database"
[contribs.enabled.config]
backend = "sqlite"

[[contribs]]
[[contribs.enabled]]
entry = "logging"

[[contribs]]
[[contribs.enabled]]
entry = "node-management"
[contribs.enabled.config]
version_check = false
```

### 0.2 BCS 配置与数据源连续升级

拆包后的 server／web 组合独立管理 BCS 配置与数据源。BCN 主配置启用 server／web 模块，server lifespan 在同一 Hub 中用 `ctx.enable(..., local=(CONFIG_STORE, STORAGE, DB, ...))` 启用其载体、存储、schema、API 与业务条目。
节点使用默认绑定，服务端使用明确选中的私有绑定，公共审计事件按组合需要共享。消费者只能取得自己声明且绑定一致的提供者。
复用的 bazaar-contrib-config／bazaar-contrib-storage 提供公共契约与实现，资源和服务实例由各自组合管理。

server 启动时通过自己的配置载体直接读取 BCS 文档。默认文件仍位于
`~/.bcs/config.toml`，SQLite 数据库为 `~/.bcs/bcs.sqlite3`，登录签名密钥为 `~/.bcs/session.key`。
旧 BCS 的配置结构与有效默认值继续由 server 的 Pydantic 模型直接解析；这些设置的读写归服务端配置载体，
BCN 的 V1→V5 版本链只处理节点主配置。server 的运行组合由自己的发行资源与 BCS 配置确定。

`bazaar-contrib-server-config-filesystem` 提供 server-config-filesystem，在 server 组合提供私有 CONFIG_STORE，
默认读写 ~/.bcs/config.toml，独立持有文件锁、快照与条件提交。
服务端设置的请求边界使用原 BCS 配置模型校验，经服务端 CONFIG_STORE 条件提交到同一来源；读取已有文件保持原结构，来源不存在时生成原 BCS 默认配置。
server 模块的启动参数模型包含 config_provider 与 config_options，缺省分别为 server-config-filesystem 与空对象；
用户只在选择额外配置中心或原 BCS 自定义配置位置时填写它们。第三方服务端配置中心在 server 组合提供同一契约。
原 BCS 自定义配置路径由 server 的 config_options.path 保留；bcn run 的 --config 选择节点主配置。
旧 BCS 自定义配置位置只改变配置文件，数据目录始终为原运行用户的 ~/.bcs，启动与升级沿用该规则。

`bazaar-contrib-server-sqlite` 提供 server-sqlite，消费服务端自己的 CONFIG_STORE 与 STORAGE，
按原 BCS 的 storage 及后端参数装配通用 SQLite 插件，在服务端注册表登记 sqlite 后端；database 提供本地 DB。
默认 SQLite 仍打开原 ~/.bcs/bcs.sqlite3，连接和退出由服务端存储模块管理；
额外存储实现使用同一服务端注册表与 DB 契约，选择与参数保存在独立 BCS 配置中。
同进程运行 agent 和 server 时，节点与 server 各自选择数据库后端，配置载体、连接与迁移记录分别绑定其具体实例。

server-api 接受本地 HTTP、管理两端私有协议入口并提供 API。
web、GUI 与页面模块进入 server 组合，通过 API、UI 和访问服务等公共契约连接；配置服务生成 Composition，由 ContribHub 校验依赖并启动条目。
服务依赖保证页面先退出，随后业务、API、存储与配置载体退出。

| 0.2 BCS 配置或原资源 | 服务端直接读取与保留规则 |
| --- | --- |
| listen，缺省 127.0.0.1:8765 | server-api 使用原监听值，保持 host:port、IPv6 及原解析语义 |
| storage，缺省 sqlite；同名后端参数表 | 服务端存储组合使用原后端选择及参数，由所属后端实现解析 |
| 默认 SQLite 数据源 | server-sqlite 直接打开原 ~/.bcs/bcs.sqlite3，保持旧实现的数据库定位规则 |
| session_minutes，缺省 10 | access 从 BCS 配置读取，保持本地登录有效时长 |
| retention_days，缺省 30 | history 从 BCS 配置读取，保持原事件保留策略 |
| ~/.bcs/session.key | access 直接读取原密钥，保持签名和 cookie 语义，既有未过期登录继续校验 |
| accounts、roles、account_roles、auth_settings、oidc_providers、oidc_identities、relations | access 使用原账户、密码哈希、角色、权限、分享和 OIDC 设置；账户 theme 继续由页面读取 |
| computers、events、refs 等既有业务数据 | fleet／history／contacts 查询原表和标识，保持 computer 身份、secret_hash、事件顺序与关联 |

包拆分完成后，业务查询直接使用上述数据布局，既有主键、凭据、事件关联和权限在事务内保留。
Node 的 agent、messages-node、collaboration、reminder 及 Server 各自持有业务表的迁移定义，
通过统一 schema 服务登记当前结构的初始基线和按版本表达的抽象操作／数据变更。
已有数据库按原 schema_migrations 或统一账本的版本记录确定起点；业务包显式声明自己的历史身份及账本字段，
schema 引擎为登记的业务 schema 接续缺少的版本，并把进度记录在统一的 contrib_schema_migrations 中。
旧账本保留已有记录，初始化后以统一账本推进。版本进度与 checksum 无关，统一实现按版本判断执行进度。
空库直接创建所选模块的基线结构并记下基线版本；后续依次执行新增版本。
业务数据库服务同时依赖所选 DB 和对应迁移插件的就绪服务，确保业务读写发生在迁移完成后；Node 与 Server 可分别绑定数据源。

升级验收从实际 0.2 发行环境生成隔离的 BCS 配置、数据库和密钥，再以 0.3 候选宿主与实际模块 wheel 启用 server／web，启动时直接读取这些原资源。
覆盖默认路径、原 BCS 自定义配置位置、有效缺省值及既有迁移版本，核对原数据库路径、历史 ledger 与业务身份，
验证旧账户登录、未过期 cookie、角色／分享权限、OIDC 配置、历史与 refs 查询，
并让原 0.2 节点使用原 token、地址和 `/node/reportEvents`、`/node/getUpdates` 协议继续上报与接收控制。
server 保留这些既有协议入口及请求／响应含义；0.3 两端的服务调用与结果使用该包私有 HTTP 协议。
原 0.2 节点的 control.result 按既有协议接收；0.3 client 直接通过私有协议回传调用结果。
升级后新事件仍写入原数据库，并能通过同一 web 页面查询；重复启动保持独立 BCS 配置、数据与迁移结果稳定。
同进程使用另一份隔离节点配置与数据库，分别修改节点和 BCS 设置并写入各自业务数据，核对两边配置文件、数据库路径与实际查询结果。
完整验收使用隔离环境的真实 V1／V2／V3／V4 文件，覆盖多 agent／provider、远端连接、凭据引用和关闭版本检查，并验证重复加载与迁移后真实运行结果。

### 默认配置生成与后端切换

`bazaar-compute-node` 的 `bazaar_compute_node/defaults.toml` 提供初始节点组合模板。
manager 构建从 BCN uv.lock 的 contrib/ 工作区成员自动生成 package/source 条目，按包名排序形成官方索引；官方源使用 PyPI，未安装的包同样随 manager 索引资源发行。
默认配置通过 `resource://bazaar_contrib_manager/catalog.json` 明确选择包内索引，由 manager 读取并管理缓存；配置中的索引列表继续支持追加和删除。
初次创建配置时，经 CONFIG_STORE 将 V5 组合提交到所选来源，filesystem 默认写入 `config.toml`，包括 `commands`、`storage`、`sqlite`、`database` 等启用配置，
并明确写入 `database.config.backend="sqlite"`、默认 agent 的 `config.agents=[]`。必要包通过宿主发行依赖安装，启动按保存的配置发现入口和建立服务依赖。
可选 server／web 包通过发行元数据导出节点入口，用户选择入口及参数后经节点配置管理服务校验并保存；
节点已有存储选择、参数及停用选择继续保留。
服务端自己的配置与业务组合由 server 的独立载体和发行资源管理，监听、登录及 storage 等 BCS 设置直接从原配置读取。
安装操作将包依赖声明写入配置并展示导出的入口；`add --enable ENTRY` 在安装后保存指定入口，`add --enable` 保存该包的全部入口。
后续启动读取用户保存的配置，升级通过配置版本迁移保留停用条目、参数和后端选择。
已有配置缺少 DB 选择入口或所选后端时，装配诊断列出缺失服务及消费方。

切换 PG 的完整流程为：

1. 从模块索引选中 PG 的发行包和包源，将其加入候选依赖组合并交给目标宿主 uv 安装。
2. 安装成功后，从发行元数据取得 PG 入口；用户在目标宿主顶层 contribs 启用，并填写连接参数。
3. 更新同一组合已有 `database` 条目的 `config.backend` 为 PG 注册的后端名，保存配置。
4. 下次启动按 `storage → PG → database → 业务` 装配；业务仍使用 `ctx.accept(DB)`。
5. 用户选择保留 SQLite 启用条目时，两后端各持有自己的连接；移除 SQLite 条目后，其连接随旧实例退出关闭。

例如假设 `bazaar-contrib-pg` 的入口名和注册后端名均为 `pg`，保存后的存储配置如下：

```toml
# config.toml 的存储组合；SQLite 与 PG 同时启用，业务选择 PG。
[[contribs]]
[[contribs.enabled]]
entry = "storage"

[[contribs]]
[[contribs.enabled]]
entry = "sqlite"
[contribs.enabled.config]
path = "bcn.sqlite3"

[[contribs]]
[[contribs.enabled]]
entry = "pg"
[contribs.enabled.config]
dsn = "postgresql://localhost/bcn"

[[contribs]]
[[contribs.enabled]]
entry = "database"
[contribs.enabled.config]
backend = "pg"
```

`entry="pg"` 对应发行包声明的模块入口，`backend="pg"` 对应该入口登记的存储后端名。
入口名和后端名分别由模块作者声明，package 名称、入口名、后端名之间的对应关系由元数据和服务声明建立。
database 的配置模型编辑 `backend` 字段；用户修改已有选择条目，管理服务更新原条目。
包安装操作提交配置中的依赖声明，启用和切换操作提交入口及其参数，管理页面分别提供这些操作。
所选连接上的模型、schema 和业务查询继续由对应功能模块管理。

### 模块索引与安装入口的关联

`contrib_indexes` 是宿主配置中的本地文件路径、HTTP(S) 地址或 `resource://<package>/<path>` 包内资源地址列表，首次配置声明 manager 发布的官方包内索引，用户可以追加私有或其他维护者的索引。
管理服务按位置类型读取文件、在线 JSON 或包内资源，分别记录缓存与读取状态；来源读取失败显示原因及已有缓存，节点继续启动。
索引是已登记模块包的安装清单，每个条目包含 `package` 和 `source`，例如：

```json
{
  "contribs": [
    {
      "package": "bazaar-contrib-logging",
      "source": "https://pypi.org/simple/"
    }
  ]
}
```

`source` 是 uv 使用的 Python 包源 URL，与读取这份模块索引的本地路径或 HTTP(S) 地址分别保存。
contrib 市场展示各索引中登记的包，用户按包名和来源选择安装目标；包版本、依赖和下载交给 uv。
安装后通过发行元数据展示该包的入口，用户再选择入口和参数，生成上面的运行配置。
完整对应关系为：

```text
模块索引(package, source)
    → uv 安装发行包
    → 发行元数据(package, version, entry point, import target)
    → 配置文档的 contribs(contrib, config)，由所选 CONFIG_STORE 保存
    → 按服务依赖进入 lifespan、登记服务
```

同一包和包源在多个索引中出现时合并条目并保留索引来源；同名包的不同包源分别展示，安装时显式选择。
同一宿主的一个发行包名对应一个已安装来源，改选其他来源时更新该包的候选依赖声明，再解析完整组合。
包依赖规格和所选包源写入目标宿主的配置，升级沿用该记录。索引位置被移除或暂时不可用时，
已安装模块仍依据配置中的依赖声明及入口参数管理。
索引发布、缓存、详情展示与 web 操作流程见 [contrib 市场计划](bazaar.md)。

## 统一宿主与可选 server／web

启动入口统一为 `bcn run --config <path>`，模块统一配置在顶层 contribs，具体实例按组合展开和服务绑定创建，默认 agent 的 config.agents 可以为空。
普通节点按 app 默认资源生成的配置运行；启用 bazaar-contrib-server 的 server 入口及其依赖后提供 API，
再启用 bazaar-contrib-web 的 web 入口后，在 server 组合启用 GUI 与业务页面。同一个进程可以同时运行 agent。

```toml
# bazaar-contrib-server 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
server-migrations = "bazaar_contrib_server:migrations"
server = "bazaar_contrib_server:server"
server-api = "bazaar_contrib_server:api"
```

```toml
# bazaar-contrib-starlink 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
starlink = "bazaar_contrib_starlink:starlink"
```

```toml
# bazaar-contrib-web 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
web = "bazaar_contrib_web:web"
```

server 实例用本地绑定的子组合装配独立配置载体与 BCS 配置加载器，以及存储和业务条目。
server-api 在 server 组合接受 HTTP 并提供 API，管理与节点 starlink 通信的 HTTP 入口；业务模块通过 API 登记自己的方法，访问模块提供授权服务。
web、GUI 与页面在该子组合中按服务绑定依赖 API、UI 和访问服务；业务页面与渠道编辑器通过通用 GUI 契约登记。
starlink 是独立发行包和普通节点插件，持有主动连接、getUpdate 请求循环、send 响应发送、身份、请求关联及重连资源。
新建节点默认不启用 starlink；用户配置目标 server 后启用其入口。第 11 阶段完成 starlink 后，再按原 audit／control 选择补充 V4 的事件上报和远端请求迁移。
节点通过 getUpdate 主动收取远端查询或操作请求，通过 send 独立发送对应结果与错误；请求响应的推进由 starlink 自身生命周期管理。
请求格式公开描述请求 ID、方法与参数，响应包含同一请求 ID 和结果或错误；BCS 与其他遵循协议的实现都可作为对端。
starlink 在 accepts 中声明所需服务，通过 Context.accept 获取其他插件 offer 的对象并调用方法；通知操作可以在节点内 announce。
业务通过 announce 独立发布公共审计事件，starlink 的事件 listen 处理器按配置入队上报，server 收到后交给投影模块。
agent／harness 的行为事件、原始载荷、来源顺序及时间关联按[完整行为事件与 NodeAudit](roadmap.md#完整行为事件与-nodeaudit)发布和上报，
agent 依赖 NodeAudit 始终发布，记录方按组合选择订阅；BCS 活动页、历史和网关分析各自决定消费范围及统计口径。
审计事件处理和请求响应分别管理；send 直接发送调用结果，审计记录由业务独立发布。
HTTP 输入模型、身份校验、传输和请求关联由 starlink 与 server-api 管理，服务接口及业务规则仍归提供 offer 的功能包。
包的 pyproject.toml 声明代码依赖并导出入口，节点配置保存所选入口及参数；运行时按 accepts 建图。
这两种依赖分别管理，server-api 先提供 API 注册服务，功能模块再登记接口，避免组合与业务之间形成运行循环。
API／UI 的公共类型由 HTTP／GUI 包导出，功能包导入公共契约，server／web 依赖功能实现包。
完整包清单、运行依赖、用户安装流程及各阶段验收见 [统一组合计划](roadmap.md#统一宿主与-serverweb-组合)。

### 并行运行与资源清理

lifespan 完成初始化并确认就绪后到达 yield，宿主继续装配其他模块。
持续服务循环由模块管理的后台任务或子进程执行，应用入口统一等待退出信号。
各模块根据自身实现选择异步任务、线程或子进程，确保 HTTP 服务、连接等待与 agent 执行可以各自推进。
任务故障接入既定通知契约，退出时停止新工作、完成在途操作、结束并等待所属任务或进程，最后释放资源。

server-api 的生命周期在 yield 前启动监听并提供 API，在退出时关闭监听；参数由独立 BCS 配置解析：

```python
from contextlib import asynccontextmanager

from bazaar_core import Context


@asynccontextmanager
async def server_api_lifespan(ctx: Context, config: ServerConfig):
    listener = await start_listener(ctx.accept(HTTP), config)
    try:
        ctx.offer(API, listener.api)
        yield
    finally:
        await listener.close()
```

HTTP 包提供共用路由和监听能力，server-api 管理本次 API 监听与两端私有协议入口。
start_listener 在监听就绪后返回，持续服务由其后台任务执行；close 停止监听并等待所属请求与任务结束。
API 的登记归属由同一 ContribHub 的 server 实例管理，消费者退出后才退出 server-api 的 lifespan 并撤销服务。

分开部署时使用独立配置分别执行 bcn run，各进程拥有自己的数据库、socket、端口和运行目录。
混合组合与分进程组合都通过实际 HTTP 请求、agent turn、长连接和退出验证：一个服务等待期间另一个仍有进展，
所属资源随模块生命周期结束。bcn run 依据声明装配和管理模块，具体执行方式属于模块实现。

## 启动接入与首个日志模块

`bcn run --config <path>` 在 app 中收集启动输入，读取随包默认资源，装配版本包的 VERSIONS 并绑定配置加载器，
直接创建 ContribHub，进入 core 的统一 run 异步上下文管理器；node-management 的 run 命令激活配置并等待退出信号。
core 发现入口、装配所选载体及发行版本组合，启用提供 `COMPOSITION` 的配置加载器后校验并装配业务依赖；
组合管理 agent 实例的创建、更新和停止。启动完成后，健康状态显示实例标识、创建者与运行状态。
外部控制入口开放前，需要的模块服务已经就绪；退出时先结束请求与 agent，再清理模块和共享资源。

日志模块在 lifespan 中按自身配置创建 QueueHandler／QueueListener 和输出 handler，接收标准库 logging 记录。
记录保留发生时间、级别、Python 调用位置、CURRENT_CONTEXT 中的 contrib 与实例 id，以及异常堆栈。
日志定义在顶层启用一次，退出时排空队列并释放自己的线程、文件和 handler；多个宿主分别持有自己的资源。
实例启动与运行异常由 core 直接通过标准 logging 报告；当前状态通过 ctx.snapshots 查询，变化由所需功能订阅 CONTRIB_CHANGED。

日志包通过公共模块入口接入，公共框架和包发现代码均不写日志专用分支。
测试从独立包的 wheel 元数据发现入口，启动真实节点、产生日志及资源异常、观察日志，再结束进程验证清理。

## 模块管理与自身 uv

`bcn contrib` 提供索引列表与增删、索引条目列表、安装、卸载、启用、停用、已安装列表和详情。
`bcn contrib add <name>` 接受短名或完整发行包名，短名通过已配置索引中的 `bazaar-contrib-<name>` 条目确定包名与来源。
例如 `bcn contrib add codex --enable codex` 安装 bazaar-contrib-codex，并在安装确认后启用其 codex 入口。
包安装将完整依赖规格保存到顶层 contribs；enable 保存所选入口及参数，disable 移除入口并保留包声明，同时校验剩余依赖。
安装状态与启用状态独立记录；不传 `--enable` 只安装，`--enable ENTRY` 启用指定入口，单独 `--enable` 启用该包导出的全部入口。
入口清单直接读取发行元数据，管理入口展示各入口与配置模型，所选条目经配置校验后保存；结果分别显示实际安装版本与配置保存状态。
模块通用参数由各入口的 Pydantic 模型校验并保存到对应 contribs[].enabled[].config；编辑更新所选已有条目，保留未修改的参数与其他组合选择。
agent 的连接／执行参数和凭据引用由 provider 实体模型校验，保存到 agent 条目 config.agents 中相应实体的 channel／harness 配置。
命令定义、帮助、提示与输出由登记命令的功能包持有；功能包选择自己的文案及渲染方式。同步 Click 命令树构造在工作线程完成。
命令名沿用常见的操作词，命令与选项帮助采用 uv／pip 的“动作＋对象”英语短语，直接说明用户操作；功能统一称为 contrib，安装包称为 package。
管理 CLI 参照 uv 的实际文本输出：包列表和索引使用对齐列，详情逐行展示字段及入口选项，操作分别显示包变更、配置保存、运行应用和需要重启的结果。
manager 自持 Jinja 依赖与随包发行的 resources/*.tpl 文本模板，沿用主分支的资源命名，以 PackageLoader 加载并启用 StrictUndefined；Python 整理展示数据、计算表格列宽，模板持有文案及排版，操作查询复用结果模板。模板加载与渲染在工作线程执行。
查询结果写入 stdout，操作提示和错误写入 stderr；失败时保留已完成步骤、操作 ID 与恢复提示，并返回失败退出码。
卸载失败说明具体包名与实际阻塞条件：配置中没有该包时指向 `contrib configured`；仍有该包的 contrib 时列出名称，说明用 `contrib configured` 查看编号、`contrib disable INDEX` 停用后重试卸载。
输出依据本机 uv 0.12.2 的实际安装、列表、详情、重复安装和卸载行为，以及 [uv 环境检查文档](https://docs.astral.sh/uv/pip/inspection/) 的列格式约定。
索引读取与安装管理提供独立的 typed 服务契约，CLI 与后续 contrib 市场页面消费同一实现。
管理请求明确目标宿主、包规格和模块入口；已有配置与实例由管理列表提供选择目标及操作句柄。
实例句柄由宿主返回，管理界面和调用入口使用它完成操作。服务提供自动生成的操作 ID、进度与终态查询，
结果包含安装版本、配置状态和下次启动生效状态。web 页面及远端调用入口在后续阶段接入。

本地 `contrib add`、模块配置编辑和后续 `agent add` 在节点未运行时使用短生命周期管理组合。
app 调用 core 装配所选配置载体及版本服务，并按操作进入配置、命令登记、模块管理及所选 agent 管理服务；首次管理操作经当前载体提交 app 默认配置，默认 agent 的 config.agents 为空。
配置编辑复用 CONFIGURATION 的 prepare／commit，以完整候选的模型及依赖校验和修订条件提交保存结果。
管理命令退出时释放本次资源；channel 连接、harness 进程与 agent 执行实例由后续 `bcn run` 按已保存实体配置创建。
首次使用流程见 [总计划](README.md#全新安装与默认运行组合)，本分支用真实示例包完成默认基础组合、安装及 agent 配置管理，完整 Codex／Telegram 流程随具体 provider 阶段交付。

公共命令服务由 `bazaar-contrib-invoke` 的 `commands` 入口提供：`COMMANDS` 的值是本次组合拥有的 Click 命令组 `Commands`。
功能包声明 `accepts=[COMMANDS, 所需公共服务]`，在 lifespan 中通过 `Commands.register()` 登记自己的 Click 命令；
登记边界报告同名冲突，退出时只撤销本次登记的对象。manager 持有 `contrib` 的完整命令树和管理服务绑定。

invoke 自持 `bazaar_compute_node.commands` 扩展分组，发行包按顶层命令名称导出 `CommandEntry(contrib=...)`。
该描述只指向负责登记的 contrib；命令参数、回调和服务仍由该 contrib 在 lifespan 内登记。
命令发现先按已启用入口所属发行包筛选，再核对命令归属；仅展示和执行已启用 contrib 的命令。
所有命令插件按保存的入口及参数加载；安装包后仍须 enable 才能使用其命令。
node-management 以同一扩展协议声明 `run`，app 消费通用命令发现与分发接口。入口发现与选择归 invoke，core 只接受通用插件根。
`run` 保留原有运行节点、为全部已配置 Agent 提供服务直到停止的完整英语说明；入口列表与实际命令复用同一文案，保留完整 short_help。

启动依次完成 boot、load、选择入口、启动所选依赖、执行回调、资源清理。外层由原生 Click 解析配置载体参数，
配置参数写在命令前，例如 `bcn --config /tmp/node.toml contrib list`。invoke 用 Click 的原生命令解析定位扩展名；
配置和定义已读取，未进入所有业务 lifespan。配置服务根据该次选择产生根插件组合。
core 的 Composition.roots 按插件名称指定根，复用现有参数展开、服务绑定及依赖顺序，只启用根及其所需提供者。
局部绑定按具体实例计算，已有自举实例继续复用；无关分支不获取资源。所选根缺失、服务冲突或循环保留正常诊断。
core 不读取命令元数据，不导入 Click，也不解释 CLI 或 GUI。其他功能插件可以拥有自己的扩展协议。
app 的 defaults.toml 提供新节点的初始配置，并显式声明 manager 包内官方索引的 resource:// 地址。

随后所选 contrib 及其依赖进入 lifespan 并登记实际命令，Commands.dispatch 在工作线程内完成原生 Click 的参数、帮助和回调分发，
将回调返回的 awaitable 交回事件循环。回调结束或取消后退出组合；只有需要常驻的回调自行等待。
根帮助从入口描述列出可选命令；单条命令帮助启动它所必需的服务。未选中的命令插件及业务分支保持未启动。

管理根从已启用的 contrib-manager 及保存参数构造。首次配置创建、迁移和后续保存复用 CONFIGURATION。
`run` 在同一 Hub 内消费 CONFIGURATION.activate()，保留命令根并通过已有 Context.update 启用完整已保存组合，
随后等待退出信号；配置载体、版本服务及未变共享实例继续复用。管理命令完成即退出，保留原来的待重启语义。

依据 `src/bazaar_compute_node/app/upgrade.py` 的 uv 定位和执行行为，在 `bazaar-contrib-manager` 实现新组合共用的宿主升级及包管理能力。
uv 由宿主进程的 PATH 解析为具体执行文件；目标环境明确对应运行该节点的 Python。
实际执行使用参数数组，安装结果、发行清单和启用配置之间有明确提交顺序。

运行配置是依赖声明的唯一来源。`contribs` 每项保存一个包的 `package`、可选 `source` 和 `enabled` 数组；每个启用项保存 `entry` 与自己的 `config`。
`package` 使用与 `pyproject.toml` 的 `project.dependencies` 相同的完整 PEP 508 字符串，保留 extras、版本范围、URL 和 marker；
`enabled = []` 表示只安装包。同一发行包的多个入口归入同一个数组，包规格和来源只声明一次；编辑参数保留依赖规格，停用最后一个入口保留只安装声明。
TOML 可使用内联数组，也可用 `[[contribs.enabled]]` 展开启用项，再以 `[contribs.enabled.config]` 填写各入口的参数。
`add` 保存实际安装版本或显式版本范围及所选来源；`--enable` 将声明带到该包提供的入口。迁移只需复制配置。
默认来源为空时使用 manager 的 `host_source`；显式来源随配置迁移。入口只在其依赖 marker 对当前平台成立时启用。

调研依据：[Python dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/)
定义包名、extras、版本或直接 URL 以及 marker；[uv dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/)
使用标准依赖字符串并用 `tool.uv.sources` 选择索引；[uv compile/sync](https://docs.astral.sh/uv/pip/compile/)
支持从 pyproject 解析依赖并用同一 pylock 同步完整环境。配置边界用 packaging/Pydantic 校验字符串；安装仍交给 uv。

每次启动先通过配置服务读取和保存文档，按普通配置声明发现入口。app 先按配置启用监听 CONFIG_PREPARE 的插件及其服务依赖，
在它们就绪后发布并等待这个公开配置准备事件，再切换所选业务组合。配置决定入口名称和参数，自写实现监听同一事件即可替换。
CONFIG_PREPARE 是 EventKey[None]；包管理插件自己的事件处理器复用 sync(only_missing=True)，并将失败转换为启动错误。
默认实现检查目标解释器中的声明、extras 及传递依赖，缺失或版本不符时执行完整候选解析、安装和入口验证；
满足时直接继续。随后配置服务在同一 Hub 中切换到调用者选择的命令根或完整节点组合，再进入业务 lifespan。
显式 `bcn contrib sync` 复用该流程，按配置重新解析并安装；安装失败保留诊断，启动中止。
app 保留直接使用的核心、配置、版本、命令公共契约包及首次启动所需的默认准备插件；status、logging、sidecar 及平台实现由默认配置声明。
默认 TOML 直接声明两种原生 sidecar 入口及标准 PEP 508 `sys_platform` 条件，通用配置读取只选择条件成立的条目。默认包规格及仓库内正式依赖采用各自所需的三段式兼容范围，首版为 ~=0.3.0；固定宿主、包依赖及用户保存的版本要求共同约束解析结果，兼容旧依赖可以保留。
发行工具更新 project.version 时保留仍适用的依赖下限；使用更新接口时才提高对应最低版本。

安装管理、同步和整体升级从配置生成候选，保留实例参数和显式来源／版本；操作日志和 pylock 是执行及恢复产物。
卸载要求先停用入口，并验证其他包和当前自举载体仍能正常工作。配置文档通过 CONFIGURATION 条件提交。

完整组合的版本与来源解析按以下步骤执行：

1. 在目标宿主的操作临时目录生成 `pyproject.toml`，`project.dependencies` 包含固定宿主版本及全部额外包规格。
2. 将每个额外包的 `source` 转为具名 `tool.uv.index`，由 `tool.uv.sources` 将包绑定到所选 index。
   index 名由管理服务内部生成；用户填写的仍是包名、源 URL 和可选版本范围。
   同源条目共用 index，依赖解析按保存的来源顺序及目标宿主默认包源使用 uv 的 `first-index` 策略；
   直接声明的额外包通过 sources 固定来源，其传递依赖由 uv 从候选包源解析。
3. 由自身 uv 执行 `uv pip compile <candidate>/pyproject.toml --python <target-python> --format pylock.toml --output-file <candidate>/pylock.toml`。
   此步骤解析整个候选依赖图，产物保存选定版本、下载地址与校验信息。来源或版本冲突在改变安装环境前报告。
4. 安装阶段消费同一份解析产物，完成后用目标解释器检查实际包、入口和 API 兼容信息。
5. 成功后通过配置服务保存包声明与解析结果；启用请求另外校验并保存入口配置，结果显示下次启动生效。
   操作失败保留原声明，并记录实际环境变更及恢复原组合所需步骤。

生成的来源声明例如：

```toml
# 管理服务生成的候选 pyproject.toml，不是用户的 config.toml。
[project]
name = "bazaar-contrib-environment"
version = "0"
requires-python = ">=3.15"
dependencies = ["bazaar-compute-node==0.3.0", "bazaar-contrib-pg"]

[[tool.uv.index]]
name = "selected"
url = "https://packages.example.com/simple/"

[tool.uv.sources]
bazaar-contrib-pg = { index = "selected" }
```

- **uv tool 部署**：从同一 pylock 选择当前解释器兼容的 wheel、锁定的 sdist 或标准 archive 直接引用，将下载地址与 SHA256 写成 requirements。
  使用 `uv tool install '<锁定的宿主包直接引用>' --with-requirements <candidate>/requirements.txt`，
  主包及全部附加依赖均使用已解析的产物；默认功能包与其他配置依赖一同解析。
  现有监督进程负责下一次启动；Windows 沿用当前升级保留运行解释器的安装方式。
- **自管 venv 部署**：同一 uv 使用 `uv pip sync --python <target-python> <candidate>/pylock.toml`，
  目标是由宿主管理完整依赖集合的专用 venv，安装和卸载均按候选完整组合同步。
- 安装、卸载和节点升级共用宿主环境变更锁，避免并发包操作覆盖依赖组合。
- 保存用户给定的包版本、来源规格和模块实例配置；uv 负责发行依赖解析，运行时 `accepts` 负责服务装配。
- 整体升级保留后端的额外包依赖、`database.config.backend`、连接参数、其他启用实例及停用选择。

tool 部署使用普通 `uv tool install`，保留 Windows 正在运行的解释器。
宿主安装与重启遵循 [服务监督计划](../2026-10-07-service-supervisor/plan.md) 的生命周期要求。

后续节点管理命令保留 `bcn start`、`bcn stop` 作为服务管理命令的别名，帮助分别写为
`Legacy alias for bcn service start`、`Legacy alias for bcn service stop`，沿用同样的说明方式。

## 本特性分支的串行 Tasks

每个 Task 完成后停下 review，通过后继续下一个 Task。后续在 `feature/node-contribs` 先交付独立定时器，再交付数据库，两项各用一个 commit；其后完成 agent 相关能力和整体交付。当前三条特性分支按依赖 review 并合入 `0.3`。
新能力通过已有公共插件契约装配；服务提供者随消费者的声明依赖选入，V4→V5 同步补齐原功能所需的条目和参数。

### Task 1：核心 contrib 框架

- [x] 建立 Python 3.15 uv workspace 与独立 core 发行包，使用任务组管理真实资源。
- [x] 定义 ContribHub、typed 服务和事件、可选配置模型、ContribEntry、Composition、Candidate 与版本契约；无配置 lifespan 只接收 Context。
- [x] 每个条目创建一个具体实例，Context 提供 id、name、snapshots 以及 offer／accept／announce／enable／validate。
- [x] 静态及配置计算的 accepts 统一解析，完整图在资源启动前检查缺失、重复和循环依赖。
- [x] 默认服务绑定共享，ctx.enable 的 local 支持 ServiceKey 与 EventKey；继承其他绑定，私有消费者只接受相同绑定的提供者。
- [x] 子组合由创建它的实例拥有，按服务和创建关系的依赖图启动、传播故障及逆序退出。
- [x] 处理父实例到 yield 前启动子组合、已 offer 的祖先服务注入、重复关闭、取消和在途回调排空。
- [x] 将 lifespan 的组合启用等待纳入图，真实 SQLite 验证跨层级环、多层嵌套环后的继续读写，以及后台启用、退出和持久化。
- [x] run 用普通加载器提供 COMPOSITION 组织自举与业务阶段，退出先结束业务再释放自举资源。
- [x] 真实 SQLite、文件、TCP 与任务故障验证独立组合、共享服务、私有事件、错误隔离和完整退出；完成 Ruff、根 LSP 与独立发行验收。
- [x] 按发行包声明的支持平台生成 CI 任务，以单个 Checks 任务顺序检查受影响包；验证矩阵、工作流及 Ruff、根 LSP。

### Task 2：独立状态查询模块

- [x] 独立 bazaar-contrib-status 提供协议、typed key、入口与类型标记，只依赖兼容范围内的 core。
- [x] 查询读取 ctx.snapshots，记录包含具体实例 id、创建者 parent 和不可变生命周期状态。
- [x] 真实文件和任务故障验证历史快照、传递阻塞、独立 Hub 与嵌套共享查询；完成独立安装及检查。

### Task 3：独立应用基础与节点输入

- [x] 建立独立 app 发行包、Click 命令入口和默认依赖，普通 run 进入 core 的分阶段运行接口。
- [x] 应用 run 接收普通 bootstrap 和 loader，明确启用具体条目，业务输入通过 typed 服务声明。
- [x] bcn --version 与 run 处理启动输入、Ctrl+C／SIGTERM，完成真实文件与隔离进程的退出验证。
- [x] 独立 wheel／sdist 验证应用公共调用、CLI、许可证、类型标记；完成 Ruff 与根 LSP。

### Task 4：发现、配置与节点启动

- [x] 统一 bazaar_compute_node.contribs 元数据发现，入口导入、API、配置与服务错误进入状态诊断，有效条目继续运行。
- [x] 配置契约、filesystem、版本服务与状态成员独立发行，app 持有启动 flags 和 defaults.toml；官方 catalog 构建资源随本轮迁入 manager。
- [x] --configer 选择载体，--configer-options／--config 可重复并保留顺序；载体自己解析原始参数并提供 CONFIG_STORE。
- [x] CONFIG_V1…CONFIG_V5 的依赖链提供转换服务，载体按修订条件提交候选，保留历史转换和数据目录规则。
- [x] FileConfigStore 使用原始字节修订、独立文件锁与原子替换，线程执行 IO 并等待在途操作。
- [x] 已知条目通过 typed 服务与显式绑定表达依赖，保存前预检已声明的组合；agent 模型与实例展开由 Task 11 的业务插件接管，app build 统一加载 contrib 声明。
- [x] ConfigDocument 提供通用文档与载体契约，agent 实体模型与展开在 Task 11 由 agent 插件持有。
- [x] 独立第三方 wheel 用真实 SQLite／TCP 配置中心验证读取、编辑、冲突、未知提交响应、重建和启动取消。
- [x] 自定义构建后端从 uv.lock 生成内置 catalog，独立 sdist 可重建；验证默认配置、组合导入、路径转义与隔离节点。
- [x] 完成 Ruff、根 LSP、真实安装配置流程与独立发行验收。

### Task 5：首个日志模块

- [x] CURRENT_CONTEXT 在实例 lifespan 与监听回调 set／reset；子任务和工作线程继承上下文，消费者绑定自身身份。
- [x] logging 在 lifespan 中通过 QueueHandler／QueueListener 接收标准库日志并在线程中写入。
- [x] text／json 格式保留时间、实际来源、级别和异常；实例外日志用 Python logger 标识来源，source.id 为 None。
- [x] 真实文件验证按日及体积轮转产生文件并保留日志，以及 level 路径、日期保留、线程输出、handler 所有权和退出排空。
- [x] 完成并发私有消息回调、真实普通日志 wheel 和隔离节点的安装验收及检查。

### Task 6：模块安装管理与升级

- [x] Context.update 和 Enablement.update 串行应用完整组合；首次启用、候选校验和更新复用 target／Diff／apply，保留未变资源并重建受影响依赖。
- [x] 更新保留已有 local 绑定，父实例替换时清理其后代；真实 SQLite／文件验证跨组合重绑、缺失依赖恢复、并发、故障恢复和请求者生命周期。
- [x] 消融删除 Enablement 重复的活动／失败索引、未使用的 ready 属性和 Diff 重复候选字段；真实资源回归验证由 Hub 单一记录提供查询。
- [x] 独立 bazaar-contrib-manager 提供索引、包、进度和结果契约，复用目标宿主 uv 及环境操作锁。
- [x] 真实本地／HTTP 索引、缓存、私有源和多入口 wheel 验证安装、发现、导入、启停、编辑及卸载。
- [x] add／enable／edit 使用可重复 --option KEY=VALUE，支持点分字段、列表位置与重复值，保留未编辑参数。
- [x] uv tool／专用 venv 消费绑定来源的完整 pylock 候选，升级保留配置载体、额外依赖、来源与业务选择。
- [x] 目标解释器先转换并检查完整配置，条件提交后分别记录安装、保存与运行结果；修改请求者自身生命周期时报告待重启。
- [x] 普通 server lifespan 用 ctx.enable(local=(DB, RECORDS)) 管理真实 SQLite／TCP 子组合，验证混合运行、停用后节点继续写入和纯服务端启动。
- [x] 完成真实安装环境、Blockbuster 回归、独立 wheel／sdist、Ruff 与根 LSP；修改 amend 回各功能的引入提交并签名推送。
- [x] 配置携带完整依赖规格与来源，add／enable／edit／disable／remove／upgrade 统一读写配置。
- [x] contrib sync 与每次启动复用配置所选 manager 的检查及安装，准备阶段按公开事件监听声明及服务依赖装配，再切换所选业务组合；独立载体验收使用替代入口与独立的 uv 环境检查实现。
- [x] 真实 uv tool 与 venv 验证仅复制配置即可恢复包、入口及参数，完成发行检查、Ruff、根 LSP，并单独签名提交。

存储等后续包交付时，继续验证历史配置自动纳入依赖及完整 V4→V5 运行迁移。

### Task 7：插件扩展入口与按根启用的命令执行

- [x] 交付 invoke 的 COMMANDS、Commands.register／dispatch 与插件自持的 CommandEntry 扩展发现，保持原生 Click 和 lifespan 登记。
- [x] core 在通用 Composition.roots 中按根及绑定选择依赖，保留既有完整建图、更新和生命周期流程。
- [x] manager 与 app run 按相同元数据声明命令根，app 复用 boot／load 和一个 Hub，回调决定退出时间。
- [x] 命令列表与执行按所属 contrib 的启用配置筛选，app 显式提供启动入口，保存参数用于实际插件装配。
- [x] run 复用配置服务与 Context.update 启用完整组合，共享依赖保持实例；管理命令只启用必要分支。
- [x] 真实文件／SQLite 与独立示例 wheel 验证根选择、绑定、命令参数、帮助、取消、信号、资源退出及管理升级。
- [x] 完成真实 uv tool／venv、独立 wheel／sdist、Blockbuster、Ruff 与根目录 LSP，替换旧命令提交并签名推送供 review。

### Task 8：sidecar 调用与节点基础接入

本轮 review 节点为 sidecar 接口包、两个传输实现包及真实跨进程调用验收。`bazaar-contrib-sidecar` 定义
`SERVER`、`CLIENT` 两个公共服务 key，并提供只接受 `SERVER` 的 `sidecar` 入口；它随完整配置组合进入 lifespan，
通过连接所属的调用上下文转发框架操作。消费插件只依赖接口包并接受 `CLIENT`，按已有 offers／accepts 装配。

`bazaar-contrib-sidecar-unix`、`bazaar-contrib-sidecar-windows` 分别提供同名入口，各自 offer 两个服务。
`SERVER` 接受会话 handler 工厂并持有监听，`CLIENT` 传递类型化请求与结果；公共服务契约与具体 I/O 无关，
后续 HTTP 实现使用同一契约。Unix 使用 asyncio Unix streams，Windows 使用 CPython ProactorEventLoop 的
`start_serving_pipe`／`create_pipe_connection`；默认 TOML 声明两个平台实现，用依赖字符串中的标准 `sys_platform` marker 选择。
app 原样读取并交给配置服务；配置的通用 marker 判断在入口发现前生效。删除 sidecar 及传输声明即可选择其他实现或停用，启动不补回。
Windows 使用 [CPython 3.15 的 IOCP 与 pipe transport](https://github.com/python/cpython/blob/3.15/Lib/asyncio/windows_events.py)，
创建句柄时以 FIRST_PIPE_INSTANCE 排他，并沿用 0.2 的 PIPE_REJECT_REMOTE_CLIENTS；按
[CreateNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createnamedpipea)
的句柄归属管理后续连接，接受任务由 lifespan 持有，退出时取消并关闭未连接句柄。
Unix 沿用 0.2 的存活探测：已有 socket 能连接则拒绝启动，连接被拒绝或普通残留文件则清理后重建；
其他 I/O 错误直接上抛。先自行 bind，再把 socket 交给 asyncio，保留内核的地址排他，避免 asyncio 自动删除已有端点。
Unix 监听与按文件身份清理由 [asyncio streams](https://docs.python.org/3.15/library/asyncio-stream.html) 持有。
两端收发沿用 0.2 的分块读行：每次读取 64 KiB、查找换行并保留下一帧余量，避免 StreamReader.readline 的单行上限；
共享实现放在 sidecar 包内，实际请求与响应均验证超过 1 MiB 的完整内容。退出先取消连接任务，再等待监听关闭。
配置内可明确指定本机 endpoint，默认地址在插件内按平台生成，沿用 BCN_HOME 的数据目录。
每个节点默认启用 sidecar 与本机实现，承接 0.2 的 bcn.sock 本机通信；V4→V5 无条件生成这两个入口，
并将旧 Windows 路径按原有管道身份转换为原生地址。node.control 与本机通信的启用无关；
已有 V5 的启用选择与参数继续保留。

共享 handler 从服务端 Context 出发，提供公开属性读取、方法调用及异步上下文的进入、退出和引用释放。
JSON 标量、序列和字典按值传输，服务、候选组合等对象以连接内引用返回；公共 key、定义和模型可用已安装的
`module:symbol` 引用及构造参数传入，避免复制服务实例或丢失 key 身份。请求与结果由 Pydantic 校验。
连接关闭时释放其对象引用及远端进入的子组合；服务端退出时取消并等待所属连接任务。

消费方使用客户端取得远端 ctx，再通过 accept 请求服务并组合调用。
每次远端 accept 使用已有 Context.enable，为调用方启用只声明该服务依赖的消费入口；
消费入口继承原绑定范围，框架负责解析、就绪和退出顺序，连接持有并在关闭时释放这些消费组合。
其余公开属性和方法继续转发原 Context；sidecar 只声明 `accepts=[SERVER]`，不导入配置、消息或其他业务契约。
目标符号未安装或服务不可用时，沿用 Failure／RemoteError 将调用错误返回客户端，不安装或启用业务提供者。
默认启用及 V4→V5 转换只声明 sidecar 入口。
接口依赖直接写入插件的 `Contrib.accepts`；配置不提供 `services` 或同义字段，也不保留将这类字段解析为服务 key 的逻辑。
配置刷新由调用者通过远端 CONFIGURATION.read／commit／apply 编排，继续使用配置加载器原有 ctx 的更新归属；
sidecar 的 ctx 保持自己的服务可见范围与组合归属，调用方的服务依赖归连接内的消费入口持有。
各传输共用这些 handler，业务编排由消费插件持有。

本轮继续完成 manager 的实际消费。manager 的启动依赖仍为 CONFIGURATION、COMMANDS，
所选组合未声明 CLIENT 提供者且没有加载诊断时，直接使用本地管理；已配置但无法加载或装配的客户端报告错误。
首次依赖安装完成后，变更操作从所选配置构建以 CLIENT 消费者为根的临时子组合，复用 core 的依赖选择与资源退出。
该子组合只创建一个 CLIENT 消费入口；启用后有客户端时直接取首项，没有客户端时继续本地管理。
根选择仅展开本次组合内的声明，外部现存实例及其依赖继续复用，避免为父实例重复选择同名服务。
取得环境锁后、产生安装或保存副作用前，通过 CLIENT 的远端 ctx.accept(CONFIGURATION) 建立配置会话，核对配置自举声明与当前修订。
只有首次连接明确返回端点不存在或拒绝连接时使用离线组合；已连通后的远端异常、超时和断开直接返回失败，
不重放操作。本机安装、目标解释器预检与操作记录仍归 manager，在线保存由远端配置服务条件提交，随后通过它的 apply 请求热更新。
add --enable、enable、edit、disable 和索引变更复用此流程；单纯新增包不因新增发行元数据要求重启，
已安装包的版本或来源替换、卸载及应用升级继续报告需要重启。配置服务在每次运行应用前刷新发行元数据发现缓存。

disable --force 明确许可剩余消费者因缺少服务而阻塞，保留其配置声明。core 的 Blocked 状态携带缺失服务名，
并沿依赖传播；配置服务只对这种诊断放宽提交校验，配置模型、入口导入、重复服务及循环错误仍然拒绝。
选项补救提示参照 [dnf --allowerasing](https://dnf.readthedocs.io/en/latest/command_ref.html#options)；--force 的帮助直接说明会停用目标并停止依赖方。
配置服务以独立的 ConfigDependencyError 报告仅由新增缺失依赖造成的校验失败；manager 在普通 disable 的本地或远端提交收到这种错误时，保留具体 contrib 和缺失服务原因，再提示追加 --force，并说明依赖方会停止，所需服务恢复前无法运行。
普通后续提交与包预检按已保存组合的阻塞诊断计数，保留既有缺失依赖而拒绝新增阻塞；重启允许这些声明继续等待依赖恢复。
core 继续按已有依赖图先停止消费者、再停止提供者；重新启用提供者后，由同一更新流程恢复消费者。
管理入口、自身传输或配置服务被此次变更影响时，保留已保存结果并如实报告未应用与重启要求。

- [x] 交付 bazaar-contrib-sidecar 的 SERVER／CLIENT 契约、接受 SERVER 的 sidecar 入口、共享 ctx／对象 handler 与会话资源管理。
- [x] 交付独立 Unix socket 和 Windows named pipe 实现包，各自 offer SERVER／CLIENT；app 声明平台默认依赖与启用配置，V4→V5 的本地调用配置映射到 sidecar。
- [x] 真实独立 wheel、节点进程和 CLI 消费者验证迁移地址、ctx 调用、配置热更新、远端子组合、断开清理与重启；TCP 示例实现复用相同 handler，完成 Ruff、根目录 LSP 和发行安装检查。
- [x] 对照 0.2 补齐原生边界处理，交付真实 socket／pipe 和隔离进程的安装验收：残留端点、活动地址排他、异常退出恢复、坏连接隔离、退出清理及 3 MB 双向分块读行。
- [x] 三系统原生 CI 验证各平台 socket／pipe 与相同客户端流程，提交本轮 review。
- [x] manager 按配置选择 CLIENT 消费分支，在线通过远端 CONFIGURATION 条件提交及应用，首次明确离线时保留本地流程，失败不重放。
- [x] 交付 disable --force 和配置层缺失依赖许可，复用 core 的停止顺序、阻塞声明和恢复能力。
- [x] 真实独立安装环境、节点进程与 CLI 验证在线新增启用、编辑、停用、强制停用和依赖恢复，离线保存、并发修订与超时失败；完成 Ruff、根 LSP 和发行检查，独立签名提交供 review。三系统 CI 的验收结果记录于 PR。

本轮继续迁出已经实现的功能，保留 app 直接使用的公共接口、自举依赖和 VERSIONS 清单。
命令按所属 contrib 的根及服务依赖启用；准备组合只选配置准备处理器及所需服务。
node-management 包只接收已经实现的 run 命令、配置激活和退出信号等待，通过 COMMANDS 登记并由默认配置选择；
app 使用普通命令发现，不再插入 node-run 启动定义。后续 agent 与节点平台能力继续按各自 Task 交付。
manager 自持 catalog 构建后端与锁文件快照，app 使用普通 uv_build；默认配置只声明资源地址。
构建后端文件为 manager/build.py，pyproject 的 build-backend="build"、缓存键、源码包含与检查范围使用同一模块路径。
依据 [importlib.resources](https://docs.python.org/3/library/importlib.resources.html) 的结论，使用 files(package).joinpath(path).read_bytes()
读取包内资源，适用于 wheel 和其他资源加载器；资源模块缺失、文件或 JSON 读取失败沿用每个索引的错误和缓存结果。
依据 [uv build backend](https://docs.astral.sh/uv/concepts/build-backend/) 的文件包含规则，manager sdist 包含自定义后端和 catalog.uv.lock，
从独立 sdist 重建 wheel 仍能生成完整索引；app 的 wheel／sdist 保留自己的默认配置资源。

- [x] 迁出 run 实现，真实配置选择验证命令、信号退出及资源释放。
- [x] manager 持有官方索引构建和资源读取，真实索引增删、离线读取和独立 sdist→wheel 验证。
- [x] app 的公共 run 通过真实安装与配置加载流程验收；manager 的依赖规格验收覆盖保存、传递与恢复。完成隔离 app、manager venv／uv tool、sidecar 回归、Ruff、根目录 LSP 和发行检查；在同一框架分支独立签名提交供 review。

本轮 review 删除当前审计发行包、node-audit、app 的审计发布和依赖，以及按 CONTRIB_CHANGED 监听声明追加命令根的策略。
logging 保留标准日志、格式、级别路径、轮转和资源释放，直接从标准 LogRecord 与 CURRENT_CONTEXT 捕获来源。
实际资源异常验证 core 的报错及堆栈仍写入日志，命令错误继续由 CLI 展示；独立安装验收同步删除审计构建和配置。

- [x] 删除审计实现、默认声明及发行依赖，简化命令根选择并保留配置准备流程。
- [x] 日志改为标准 logging，真实文件、异常、线程、轮转、配置与隔离 CLI 验证通过。
- [x] 默认索引列表使用多行 TOML，manager 的 build.py 通过独立源码包重建验证。
- [x] 完成 Ruff、根目录 LSP、独立 wheel／sdist 及受影响安装流程，amend 到各自引入提交并更新同一 PR。

### Task 9：独立定时器接口与时间轮

该 Task 用一个 commit 交付节点共享定时能力；下一 Task 用另一个 commit 交付数据库。
沿用现有 `src/bazaar_compute_node/core/timerwheel.py` 的分层时间轮、代次与等待者语义，
公共协议和错误移至 `bazaar-contrib-timer`，具体轮、驱动与条目归 `bazaar-contrib-timer-wheel`。

公共包只依赖 core，导出 `TIMER`、`Timer`、`TimerHandle`、`TimerCancelledError` 和 `TimerClosedError`。
`Timer` 提供 `create(delay_ms)`、`tick_ms`、`maximum_delay_ms` 和单调时间 `now_ms`；
句柄提供 id、active、deadline、generation、expired_generation、reset、cancel 和异步 wait。
每个句柄由一个消费者等待；reset 保留等待者并更新代次，取消句柄或等待任务会释放定时资源，
待处理句柄在 provider 关闭时以公共关闭错误结束。消费者在自身生命周期结束时取消所属句柄。

时间轮实现保留 10 ms tick、256 个近端槽及四层各 64 个槽。create／reset 的毫秒参数在公共调用边界
以 Pydantic 校验范围；按单调时钟的绝对截止时间向上取整，0 ms 等待下一个 tick。
空轮通过 Event 等待新条目，实际驱动属于插件 lifespan 的 TaskGroup；正常退出先关闭句柄再取消驱动，
驱动异常及意外取消使 provider 失败，由 core 既有依赖图处理消费者退出，并保留原故障。

时间轮以普通条目进入消费方选择的组合候选；公共包随实现或消费者依赖安装。
任何节点插件均可声明 `accepts=[TIMER]`，由已有 `Composition.roots` 按所选根的依赖闭包启用提供者，
共享绑定与资源归属由普通组合声明表达。第三方插件只依赖公共包并提供相同 TIMER，组合可选择该实现。

- [x] 交付两个独立发行包、公共类型和 TIMER key、时间轮入口及完整资源生命周期。
- [x] 两包 LICENSE 通过 `../../LICENSE` 相对符号链接引用仓库根文件，验证 wheel／sdist 收录完整许可证并可独立安装。
- [x] 通过真实消费者验证 TIMER 依赖选入所选实现、多个消费者共享及子组合继承，并观察计时与退出后句柄关闭。
- [x] V4→V5 将旧节点的全局时间轮映射为 timer-wheel 提供者条目，保留版本检查、健康等原有消费者所需的配置；已有 V5 的实现选择和参数按原流程保留。
- [x] 真实计时验证到期、重置、跨层定时、取消、等待任务取消、共享绑定、消费者退出和 provider 关闭；真实驱动任务取消验证故障及依赖传播。
- [x] 从 workspace 外分别安装独立 wheel／sdist，并用仅依赖公共包的真实 asyncio 定时插件验证替换，证明消费者无须依赖时间轮实现。
- [x] 完成包内测试、Blockbuster、Ruff、根目录 LSP 与独立安装检查，签名提交一个 commit，停下 review。

### Task 10：公共存储、SQLite 与 schema

公共包 `bazaar-contrib-storage` 提供 `Database`／`Session` 协议、`BackendRegistry`、
`STORAGE`、`DB` 和按名称缓存的 `backend_key(name)`。`storage` 入口提供共享注册表；
具体后端在自身 lifespan 的注册上下文内提供对应 backend key；`database` 的必填 backend 配置
通过 `accepts=lambda config: [STORAGE, backend_key(config.backend)]` 声明所选依赖，并将该对象登记为 DB。
消费组合以实际使用者为根选择依赖，配置明确 `backend="sqlite"`；各包使用相对链接共享根 LICENSE。

查询输入是冻结的 Pydantic 数据模型，标识符和参数分别校验，值始终作为绑定参数交给后端。
公共 `Select`／`Insert`／`Update`／`Delete` 描述显式读写；表达式包含列引用、值、算术、聚合与
coalesce／lower／length，过滤包含比较、IN、空值、逻辑组合与文本子串搜索。
Select 明确投影、inner／left join、排序、分组、limit／offset；批量写入、冲突处理与 returning 明确声明。
结果为普通记录和写入结果；关联由批量查询或明确 join 取得。
`Database.transaction(operation)` 把绑定 Session 传给异步操作，整体提交或回滚，绑定 Session 在操作返回后失效；Session 提供相同数据操作与
声明式建表、加列、索引操作。后端把连接、约束与查询错误转为公共错误类型。

`bazaar-contrib-sqlite` 翻译公共操作为参数化 SQLite 查询，提供配置模型、连接及 sqlite 入口。
SQL 结构保存在后端包的 `resources/*.sql.tpl`：查询、表达式与过滤、DDL、FTS 表及各同步触发器分别用
Jinja 模板表达，Python 负责绑定参数、标识符／DDL 字面量转义及选择待执行语句。
依据 [Jinja API](https://jinja.palletsprojects.com/en/stable/api/) 的 PackageLoader、StrictUndefined 和缓存机制，
后端自持 Jinja 依赖，初始化时在工作线程预加载全部资源，关闭 auto_reload；每次渲染持有独立参数列表，
模板中的 bind 调用按 SQL 出现顺序收集参数。递归表达式与过滤复用
[Jinja macros](https://jinja.palletsprojects.com/en/stable/templates/#macros)，完整 SQL 及条件分支在模板内可直接阅读。
`bazaar-contrib-postgres` 在 `open-bazaar/contrib-postgres` 独立仓库作为可选后端维护，采用相同的资源组织，
提供 `postgres` 入口，保留 PG 自己的占位符、类型及搜索语义；wheel／sdist 验证资源安装后实际执行。
复用原 `contrib/sqlite/executor.py` 的单 writer 队列与 reader 池机制，并将长期驱动纳入 lifespan 任务组；
实际操作的取消、回滚、在途工作排空与连接关闭保留原语义，每次 SQL 调用在取消后先 interrupt 并等待该调用结束，再归还连接；后台故障通过公共 failure source 交给 core。
路径参数默认沿用 BCN 数据目录；独立验收总是显式使用隔离路径。

`bazaar-contrib-schema` 提供 `SCHEMA` 和模块 schema 的异步登记上下文，模块声明拥有的表、
迁移与前置模块版本，消费方通过服务依赖保证前置模块就绪。统一 `Migration` 以 version／name 和公共操作表达变更，
数据转换通过接收公共 Session 的异步 apply 完成，允许同一版本在建表、复制和改名之间执行数据处理。
迁移和 ledger 写入处于同一个 DB 事务；登记检查表和模块身份归属，进度按 package／version 记录名称、应用时间和耗时。
`contrib_schema_tables` 持久记录表的 package 归属，运行期退出保留数据和版本进度。
ModuleSchema 只声明结构与版本；登记身份从当前 Context 的发行来源自动取得，包名按发行包规则统一。
Context 公开已有的 ContribEntry.source；SCHEMA 使用框架设置的 CURRENT_CONTEXT，不接受调用方提供当前 package。

Node 的 agent、messages-node、collaboration、reminder 持有各自的当前基线与后续版本，直接接受 SCHEMA 并自行登记。
`bazaar-contrib-legacy-node-migrations` 持有完整已发布历史 1～32，接受 SCHEMA 接续旧库，完成后提供 LEGACY_NODE_MIGRATIONS。
`bazaar-contrib-server` 持有 Server 迁移定义，接受 SCHEMA 并在登记完成后提供 SERVER_MIGRATIONS。
Node 迁移包随 BCN 共同发行；Server 插件在 `open-bazaar/contrib-server` 独立维护，
同一个 `bazaar-contrib-server` 发行包提供 `server-migrations` 入口，并按阶段加入 `server`／`server-api` 逻辑入口。
Server 以 `>=0.3.0,<0.4.0` 声明 core、schema、storage 的公共兼容范围。服务端配置载体、存储组合、业务及页面包同属 open-bazaar 的可选仓库。
Node 各业务服务接受 SCHEMA 并分别登记，依赖 LEGACY_NODE_MIGRATIONS 保证旧库升级完成；Server Database 对应接受 SERVER_MIGRATIONS。
Agent 持有自己的运行数据与 schema，由公共服务依赖带起旧库接续及所选数据库；新安装默认安装并启用 Agent。
旧配置转换按实际 Agent／Server 使用情况分别生成对应插件及依赖声明，Server 由自己的业务插件带起迁移。
组合配置通过现有 database 入口为同一个 DB 选择 SQLite／PG。迁移插件依赖 schema／storage 公共能力，
数据库 provider 负责通用操作。当前尚无业务消费者时仍通过明确根和服务依赖选择插件；V4→V5 在旧配置存在 Agent 时声明 legacy-node-migrations，供 Node 消费组合依赖。

ModuleSchema 可声明 baseline（当前结构及版本）和 progress（已有账本的表、版本／名称字段及所属链首版本身份）。
空库执行基线并记录该版本；匹配的已有账本把已执行版本进度接入统一 ledger，随后使用同一个 Migration 执行循环推进。
已有版本只按账本判断完成状态，去掉 checksum 字段、计算、比较与后端 migrate_legacy 接口。
Node 的原 1…32、Server 的原 1…7 以同一迁移类型重写，保留版本和实际数据／结构变更；
原已发布 src 作为只读验收来源。SQLite 的 legacy/node、legacy/server SQL 资源及自动识别执行路径移除。
两种业务类型由被依赖的迁移插件显式选择，通用 schema／数据库 provider 均不识别 Node／Server 名称。

公共 storage 操作补齐删表、改表名、改列名、删列、结构化唯一／CHECK／引用约束、表达式索引、搜索索引记录键、
通用触发器操作、表存在查询、INSERT…SELECT 批量复制和 ROW_NUMBER 排序表达式。
依据 [SQLite ALTER TABLE](https://www.sqlite.org/lang_altertable.html) 的约束变化与建新表／复制／替换步骤，
旧消息结构变化使用公共建表、Copy、删表、改名表达，保留记录及约束。
依据 [PostgreSQL 触发器](https://www.postgresql.org/docs/18/sql-createtrigger.html)、
[触发器函数](https://www.postgresql.org/docs/18/plpgsql-trigger.html)及
[窗口函数](https://www.postgresql.org/docs/18/functions-window.html)，PG 后端实现等价操作；SQLite／PG 均使用后端自己的 Jinja 资源生成 SQL。
迁移中的消息顺序换算通过公共批量复制及排序表达式处理；数据合并、提醒消息生成和 JSON 回填由迁移插件使用公共查询／写入完成。

CreateTable／CreateIndex 可显式声明 if_not_exists，模块接管已有资源时保留数据；SQLite 已有 FTS 索引整体复用。

文本搜索契约以每个词在所选列中出现的子串语义定义，SQLite 使用 FTS5 trigram 外部内容索引与同步触发器，
短词执行直接子串条件；额外 PG 实现使用 pg_trgm GIN 与绑定的 ILIKE 条件。
依据 [SQLite FTS5](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)，trigram 支持子串搜索，
少于三个 Unicode 字符的词需要直接条件；依据 [PostgreSQL pg_trgm](https://www.postgresql.org/docs/18/pgtrgm.html)，
GIN 支持 LIKE／ILIKE。两端以相同普通数据工作流验证搜索、批量关联、分页和事务。
PG 后端是独立仓库的发行包，以 `>=0.3.0,<0.4.0` 声明公共 storage 与 core 的兼容范围，
依赖 Pydantic、Psycopg 与 Jinja；按
[Psycopg 异步连接](https://www.psycopg.org/psycopg3/docs/advanced/async.html)及
[事务管理](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)使用真实异步连接和显式事务，
PG 依据 [Raw query cursors](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#raw-query-cursors)
使用 AsyncRawCursor 与 $n 参数，保留 SQL 算术及文本中的字面 %。
在自有 rootless 容器及临时数据库运行，固定两个后端并存、选择切换、退出和独立 wheel 安装验收。

注册、选择、替换与迁移调度的机制测试使用固定的小型真实场景。
完整 PostgreSQL 实现归外部插件仓库，主仓库机制测试复用真实 SQLite；外部 PG 插件持有自己的关键集成验证。
插件 CI 后续补齐：从包源安装已发布 BCN 和插件，使用隔离配置启用 postgres 入口并验证实际消费流程。
schema 的小型两版本模块验证执行顺序、事务回滚与已执行版本跳过；业务迁移保留关键数据转换回归。
历史发行桥接固定为 Node 17→32 和 Server 3→7，以只读原实现产生数据和升级对照，目标链固定在原发行版本。
Server 迁移回归随同一 Server 插件维护；测试夹具保存原实现生成的版本 3 数据和版本 7 升级结果及固定来源，独立安装后即可运行。
Node／Server 消费者切换验收归 Server 插件的集成测试，主仓库测试只依赖自身包。
新增业务 migration 复用同一执行机制，回归随实际数据转换风险选择场景。

- [x] 交付 bazaar-contrib-storage 的通用数据、显式查询、事务及错误契约，以及 storage 注册表和 database 选择入口；依赖按所选 backend 展开。
- [x] 交付 bazaar-contrib-sqlite，提供 sqlite 后端及真实连接、公共操作翻译；使用 SQLite 的消费组合明确 database.config.backend="sqlite"。
- [x] 交付 bazaar-contrib-schema 的模型／索引登记、迁移顺序和 ledger；按 [数据与查询](roadmap.md#数据与查询) 统一抽象迁移、基线及版本账本，业务迁移插件显式注册。
- [x] 消费组合声明 storage、sqlite、database、schema，并通过 DB 依赖选入；同步 V4→V5 所需配置，用隔离数据库验证实际查询、事务、迁移及后端选择。
- [x] 完成各包独立 wheel／sdist 安装、真实组合及检查，提交供 review。

验收覆盖 SQLite 公共入口选入后的真实查询与连接关闭、并发写入、事务取消与查询中断、驱动故障；schema 登记、依赖、归属及重启升级；
真实 PostgreSQL 独立实现和两个后端并存／切换；初次迁移验收使用原始历史源码生成 Node 1／4／12／17／18／24／26／29／31／32 和 Server 1／3／6／7 数据库，
对比原迁移完成后的业务记录、关联、游标和搜索数据，并核对旧账本保留及重复登记；
V4→V5 声明直接连接原临时文件并选择 Node 迁移插件。各正式包均完成独立 wheel／sdist 安装，
原已发布迁移源只读用于生成对照数据库；核对独立发行资源、共享 LICENSE 和 py.typed，通过 Ruff 与仓库根 Pyright LSP 检查。
- [x] 完成统一迁移修订：Node／Server 所属包提供业务迁移入口、基线／已有版本接续、公共操作补齐及 provider 实现；验证真实不同旧版本的数据变换、空库 SQLite／PG、就绪依赖及独立安装。
- [x] 将现有 PG 实现迁出主仓库，交付 open-bazaar 的独立可选插件；收敛主仓库测试到固定机制与关键业务场景，验证独立安装并修订原提交供 review。
- [x] 将 Server 迁移入口及对应回归迁至 open-bazaar/contrib-server，由同一个 bazaar-contrib-server 包持有迁移与 Server 逻辑入口；移除主仓库成员、来源与测试依赖，验证独立产物并修订同一提交。

Node／Server 业务迁移入口及 SQLite／PG 在独立 wheel／sdist 中验证当前基线；历史升级分别通过两种安装产物执行，
Node／Server 消费者切换验证仅登记被依赖的迁移插件。公共操作验证批量复制、窗口排序、约束、改表／列及通用触发器。
SQL 模板在 SQLite 与 PG 的独立 wheel／sdist 安装后完成实际查询、参数顺序、带引号及二进制默认值、
索引移除重建和双后端切换验收；Blockbuster 检查初始化预加载及查询过程。

### Task 11：agent 配置、实例与局部生命周期

先实现真实 agent 插件，再在后续每个 commit 为该 agent 接入一项能力及所需接口。
公共 channel／harness 契约随实际消费代码一起收敛，每次接入均由 agent 的局部组合验收。

`bazaar-contrib-agent` 拥有实体模型与 `agent` 组合入口；入口配置为 `config.agents`。
本次迁入身份、名称、mode、idle_timeout 及已有 provider 选择参数，V4 的 runtime 参数在 V5 中命名为 harness，
保留原顺序与每项设置。转换只声明已经交付的入口，provider 选择保留在 agent 配置，所需入口随实际接入 commit 补齐。
V4 使用自身的历史输入模型，V1…V4 原转换保持不变。
通用 ConfigDocument 只保存 contribs 与来源，应用 build 只加载声明；agent 的实体解释、校验与装配归插件。
app 的 Python 代码通过通用配置和入口发现使用所选插件；agent 的发行依赖归插件消费者，默认配置负责选择，包管理服务在启动前恢复配置中的包。
app 的最小 wheel／sdist 验收使用其自举和通用组合，配置选择 agent 的流程由现有独立插件安装验收覆盖。

插件公开 `AGENT` 服务和纯 Python `compose(AgentConfig)`，构造单实体组合；agent 入口逐个用
`ctx.enable(..., local=(AGENT,))` 管理实例。实体 lifespan 创建所属工作区并提供身份与路径，
默认沿用 BCN_HOME／home 下 `.bcn/workspaces/<id>`，显式 workspace 可选择实际项目路径。
工作区通过工作线程创建并沿用原权限；退出后保留持久文件，消费者先于所属实体退出。
全新配置启用 agent，`config.agents=[]` 时保持实体数为零。持久数据服务与执行服务在消费它们的后续 commit 接入。

- [x] 交付独立 agent 包、实体／入口模型、AGENT 与所属 workspace，真实局部组合管理两个实例及退出。
- [x] 将 app／config 的 agent 业务模型和展开移入插件，默认依赖、配置及 sidecar 独立安装夹具同步；V4→V5 保存 agent 参数到入口 config.agents。
- [x] 用只依赖公共 agent／core 的真实文件消费插件验证组合、共享依赖、实体隔离、空配置与资源释放；工作区按真实目录身份验收，覆盖经符号链接访问的目录，不增加测试注入入口。
- [x] 完成普通安装、独立 wheel／sdist、受影响测试、Ruff 与根目录 LSP，新签名 commit 交付后停下 review。

### Task 12：为 agent 接入 channel／harness 与所需公共接口

本次同时交付 `bazaar-contrib-audit` 的发布契约和 NodeAudit 服务，agent 通过 AUDIT 依赖无条件发布当前执行链路的输入、会话／turn、完整输出、审批及关闭事件。
NodeAudit 使用 ctx.announce(AUDIT_EVENT, event)；公共事件包含 AuditContext、运行标识与发布序号、发生／接收／单调时间及完整 JSON payload。
发布调用沿用 main `AuditRecorder.append` 对消费异常的日志处理。
HarnessOutput 的 kind 接受 provider 的事件类别，保留 provider 事件名、会话／turn 关联、发生时间、原始 JSON 与现有公共字段。
默认声明与 V4→V5 启用 audit；独立 agent 组合显式提供该服务，事件消费者沿普通 listen 订阅。实现按[完整行为事件与 NodeAudit](roadmap.md#完整行为事件与-nodeaudit)的来源和分工交付。

Codex、Claude Code 及 Telegram／Lark／WeCom 等具体 channel 实现统一在 open-bazaar 独立插件仓库维护。agent 声明其实际导入的 audit／channel／harness 发行依赖，初始配置选择 agent 及对应服务入口。V4→V5 按旧 runtime 的 kind 加入所选 Codex／Claude Code 插件及 builder 声明；普通安装由用户通过 bcn contrib add codex／claudecode 选择实现。
公共 channel／harness 包的节点安装示例使用 `bcn contrib add channel`／`bcn contrib add harness`。

沿用 main 的 `core/channel.py`、`core/runtime.py` 与 `core/orchestration/turn.py` 的实际调用边界：harness 包拥有会话／turn 输入、运行指令与工具环境、输出流、审批请求／决定／结果及模型／skills 描述；channel 包拥有输入消息、附件、身份、发送与交付回执，并复用 harness 的审批载荷。两包使用 Pydantic 公共模型，provider 的连接／进程由其 Contrib lifespan 持有。执行流的时限、任务取消、终态与输出流 aclose 以 main 的对应路径为依据；main 已有的配置预算及完整会话／交付／恢复语义随所属执行编排任务迁入。

builder 配置以 ClassVar 声明 kind、entity_model、运行 Contrib 和能力；共享注册入口按 kind 提供 builder key。core 的普通配置校验通过 Pydantic ValidationInfo.context 提供当前候选及现有外部声明组成的 Composition，agent 的配置模型从其中读取 builder 元信息，以实体模型预检选定参数。该上下文只含声明，沿用现有建图与生命周期流程。

公开 compose 接受 agent 配置和所选 builder 声明，构造实体。实体先提供 AGENT 并 yield，再由所属 TaskGroup 启动各 provider 子组合；每个子组合通过 local=(CHANNEL_INPUT, CHANNEL) 或 local=(HARNESS_INPUT, HARNESS) 建立私有范围，运行输入只包含业务上下文。agent 持有所选 provider 的 Enablement 句柄；执行实例通过句柄的 ref(CHANNEL)／ref(HARNESS) 声明依赖，在子组合就绪后消费输入、调用所选 harness、转发事件和渠道审批，并由 core 既有依赖图先停止执行消费者。按 main 的 _consume_events／_forward 转发输出并在终态结束当前流，文本事件保留为事件，失败事件保留为 turn 终态；消息发送及完整故障状态随所属业务任务迁入。退出先结束执行流与会话，再退出 provider 子组合，最后释放实体。身份、工作区和现有配置保留在 agent 包。

`loop.py` 持有持续接收消息、驱动 turn、转发输出并回到下一条输入的 agent 运行循环；内部使用 LoopConfig、run 和 loop 声明，生命周期任务及实例命名为 agent-loop。provider 子组合与循环实例的装配归 agent 入口模块。审批请求的 session／turn 与当前执行不符，或 channel 返回的 request_id 与原请求不符时，agent 路由记录 warning 日志，并返回使用原 request_id、带原因说明的 rejected 决定。日志明确包含期望值、实际值、审批 request_id、session／turn、agent、channel／harness 及目标会话，便于关联调用排查；reason 仅简短说明本次操作被拒绝的原因。harness 沿正常审批返回流程处理拒绝；approval.requested 保留原请求，approval.decided 发布实际返回的决定。

真实验收由 TestChannel 注入自然用户消息、观察输出及处理审批，独立安装的小型 provider wheel／sdist 使用本地连接与隔离进程执行实际文件任务。每次验证使用自有临时配置与工作区，覆盖普通调用、审批往返、取消与资源退出，以及原有 agent 文件消费者和受影响安装流程。

行为用例依据 main 的 `tests/contrib/test_orchestration.py`：`test_stream_events` 的事件转发、`test_approval_is_routed_to_the_current_channel_session` 的会话／原发送者／当前消息审批路由、`test_multiple_sessions_keep_workspace_and_correlation_isolated` 的会话隔离，以及 `test_graceful_stop_cancels_turn_and_closes_runtime_stream` 的取消与释放。现有 TestChannel／文档子进程验收扩展为同一会话连续请求、不同会话各自的目录与关联、审批关联及关闭中的实际进程退出。按 main `run_turn`／`_consume_turn_stream` 将子进程故障转为当前 turn 的失败事件，结束该流后继续处理后续输入；正常 EOF 按终态观察结果完成当前 turn。事件按 kind／thread_id 转发到所属会话，审批单独保留原发送者 id 和当前入站消息关联；普通发送沿用 main 的显式 target 与可选 reply-to 语义，随消息命令迁入。消息持久化、完整 actor／inbox／恢复与配置管理测试按原任务依赖顺序接入。

- [x] 按 [channel 与 harness 公共接口](roadmap.md#channel-与-harness-公共接口) 交付两个独立公共接口包、Pydantic 输入／输出／审批模型及 CHANNEL_BUILDERS／HARNESS_BUILDERS；具体 provider 依赖接口包，默认 agent 通过同一接口调用。
- [x] 提供 channel-builders、harness-builders 两个共享注册入口，builder 登记 provider 实体模型、运行入口和能力；将运行输入及 provider 的 runtime 命名统一改为 harness。
- [x] app 默认组合启用两类 builder 注册服务，功能包继续拥有命令文案。
- [x] 将所选 channel／harness 接入真实 agent 的局部组合；以独立 provider wheel 和真实控制面验证消费、调用、审批与资源退出，公共接口随同消费代码交付并 review。
- [x] 交付 audit 公共包与必需发布服务，接入 agent 当前执行链路及 provider 原始事件；使用现有真实 provider 验收记录完整事件、来源、文件内容与时序，完成独立安装、受影响检查并修订原提交供 review。

### Task 12.1：为 agent 注入 developer instruction 与 bcc

按 main `app/agent.py`、`app/wrapper.py`、`core/instruction.py`、`resources/developer_instructions.md`，
以及 Codex `start_session`／Claude `_open_connection` 的实际时机，将默认 agent 的初始化上下文提前至消息能力之前交付。
实体准备 workspace 后，在 `<workspace>/.bcn/bin` 安装自身解释器启动的 bcc wrapper；退出和启动失败沿所属生命周期清理 wrapper。
POSIX、Windows CMD 与 PowerShell 脚本文本分别放在 agent 的 `resources/bcc.sh`、`bcc.cmd`、`bcc.ps1`，
使用现有 Jinja2 加载并渲染 agent／mode 与自身解释器路径，保持原生成内容和各平台引号规则。
POSIX 与 Windows 沿原 wrapper、stdin 和 UTF-8 行为；其他 workspace 文件保持持久保存。

agent 在每次建立 harness session 前渲染现有 developer instruction 资源，注入 agent 身份、实际 channel 名称、
harness、workspace、mode 与本次 session id；同一次 SessionInput 提供 instructions 和 environment。
环境将 wrapper 目录前置 PATH，携带 agent／mode、当前 actor 和 harness session 关联；provider 使用已收到的指令和环境创建实际进程或 SDK 会话。
指令模板保留 main 的已有行为约束及 mode 分支，按[统一验收标准](#迁移实施与验收标准)删除 Agent ID 显示行、将 runtime session 显示为 Session，随 agent 的 wheel／sdist 发行；加载与渲染通过工作线程完成。

bcc 复用 invoke 的 CommandCatalog／Commands 和 Contrib 生命周期登记机制，以独立 `bazaar_compute_node.bcc_commands`
入口组发现每个功能包的 CommandEntry。入口组选择所需 CLI contrib，由该 contrib 注册自己的 native Click 二级命令；
命令帮助与新增实现说明统一使用 `contrib`／`contribs`。
agent 负责 wrapper、命令启动和运行上下文。消息、thread、Reminder 的实际命令在所属后续任务消费公共服务及本次入口。

使用现有独立安装的 TestChannel／文档 provider：自然用户输入建立真实 session，子进程收到实际指令与环境，
通过注入 PATH 查找并运行 bcc，调用两个真实 contrib 命令完成 workspace 文件操作，覆盖两个 agent、会话重用／隔离与退出。
验证 wheel／sdist 内模板、wrapper 和公开入口均可用，保留原审批、失败后继续及关闭验收。

- [x] 迁入默认指令资源与渲染、各平台 wrapper 生命周期，并在 harness session 创建前注入。
- [x] 复用公共 invoke 机制提供可由其他 contrib 贡献二级命令的 bcc 入口；将实际 agent／actor／session 上下文交给命令。
- [x] 扩展现有真实 provider 独立安装验收，完成受影响检查、Ruff 和根目录 LSP，以新签名 commit 交付并停下 review。

### Task 13：为 agent 接入消息与会话

以 main `core/orchestration/commands/messages.py` 的 MessageCommands、`core/storage.py` 的快照与持久化操作，
以及 `contrib/sqlite/repository/{messages,sessions,search,facade}.py` 和 codec 为来源，交付独立 messages contrib。
保留入站去重、会话 target／显示名与 handle 解析、跨 bot 同名目标发送、check 推进游标、read 读取快照，
以及发送前两次新鲜度检查、按目标保存与恢复草稿、附件校验、可选回复关联、交付回执和 thread unfollow 的原规则。
搜索保留前五个字面片段、时间／sender／target 过滤、原相关性公式、分页与 snippet，以及原查询取消语义；按本轮确认，执行预算由调用方可选传入，bcc 默认不设查询超时。

消息记录、会话与 cursor 模型及命令结果迁入 messages；`core/actor.py` 的 Actor／Actors 作为消息公共契约迁入，
内部服务参数、草稿归属、快照与锁的键统一使用 Actor，数据库记录及进程请求的 id 只在边界转换。
messages-node 接受公共 DB、Node 迁移就绪服务和 AUDIT，持有自己的数据操作、按 Agent 分离的服务与生命周期；
通过已有 Select／Insert／Update／Delete／transaction 表达原查询，沿用 Node 已发布迁移及当前基线。
原搜索表达式需要的通用字符串运算随实际查询补入 storage 公共表达式，查询取消使用现有 backend 的中断机制。
查询控制按 main `contrib/sqlite/storage.py` 的原边界恢复：普通读取借用 reader，目录、历史、未读摘要和搜索
持有同一读取快照，check／freshness／落库仍在原写事务中；普通写入保留原 writer 操作边界。
公共 Database 提供 read(snapshot, query_budget) 和 write(transactional)；SQLite 沿用已有 reader、transaction
和 query_budget；MessageStore、MessageService 及 RPC 搜索请求透传可选 query_budget，默认 None。
需要限制搜索的调用方显式指定秒数，公共消息层不再固定五秒；bcc 沿用默认值。
使用当前节点数据库的只读一致性副本统计规模并测量实际搜索，正式数据库只读打开，测量在副本完成。
独立 PostgreSQL 已有相同作用域实现。
目录按 main 的 latest_message_ranked／latest_message／pending_message／target_catalog 集合查询表达，
通用 Select 增加实际所需的 CTE、分区窗口及标量子查询，由 backend 的模板渲染。
入站消息的附件按本次结果集批量读取；目标解析合并渠道与 handle 唯一性读取。
搜索相关性所需的文档数、平均正文长度及全部关键词的文档频次恢复为 main 的单次聚合查询；
使用现有 SUM／Case／Contains 表达，不逐个关键词重新扫描同一消息集合。
未读调度同样恢复 main 的 unread_ranked 窗口 CTE，在数据库中只返回每个会话最新的未读消息，
再统一补齐附件，避免读出全部未读消息后在 Python 中筛选。
按本轮查询 review，独立的目标投影与引用读取以协程并发处理，保持输出顺序、快照及 cursor 原子性。
逐项核对完整 MessageStore 查询与原 SQLite storage/repository：在真实节点数据库的一致性副本上，
对目录分页、review、历史与引用、未读、目标解析、附件、搜索结果及游标／新鲜度写事务作对照；
仅允许已批准的 Actor 表达、调用方可选预算与逐段消息持久化差异。完整运行受影响 contrib 验收。

从 `app/command.py`、`core/serialize.py` 及 `cmd/bcc/{message,inbox,thread,_client,_format}.py` 迁入对应请求校验、
结果序列化和 native Click 命令，通过 invoke 的 bcc 入口及公共 sidecar CLIENT 消费服务。
Click 的正常命令退出在 Hub 生命周期释放后交回原顶层，避免草稿拦截多出 ExceptionGroup 错误文案。
现有 bcc 只加载 commands 与命令所属入口，无法满足 CLIENT 的实际依赖；CommandEntry 因此携带显式
ContribSpec 依赖，由 bcc 与命令一起交给既有 load。messages 的命令入口声明本机传输，并将原 BCN_ENDPOINT
交给其 endpoint 配置；传输仍由公共 provider lifespan 创建和关闭。
消息服务的进程边界沿用 CommandDispatcher 的 Actor 解析和 session binding validator，绑定由调用它的 agent 提供。
sidecar 只负责转发框架调用，消息 CLI 通过连接所属的通用消费入口请求 `MESSAGES`；
消息依赖由该调用方声明并由框架绑定，sidecar 的定义及发行依赖均不包含消息或配置业务。
V4→V5 保留 `if old.agent`，只在旧配置存在 agent 时加入 messages-node 和 legacy-node-migrations；
只有 BCS 等其他 contrib 的节点继续使用通用通信入口。
删除 `services` 配置模型、服务 key 字符串解析及全部默认／转换／测试用法。
原 `resources/command/`、消息相关 `resources/bcc/` 与 TextTemplate 保留格式化逻辑和模板字节，随独立包发行。
channel 公共契约按原发送链路补齐 DM 地址、目标显示及交付信息，消息服务消费实际 channel，不持有 provider 生命周期。
`app/attachments.py` 的 AttachmentMaterializer 原样迁入 messages，保留暂存、原子落盘、文件上限及按数据库引用清理；真实入站附件走此流程后随消息保存，重启恢复仍使用原引用。
默认配置选择 messages-node 及其数据依赖，由 manager 恢复；Python 发行依赖由实际导入接口的消费者声明。
messages-node 的 CLI 依赖公共 sidecar 契约；所选原生传输实现由配置声明并由 manager 恢复，消息包的发行依赖只声明实际导入的接口包。
独立安装验收在准备工作区及附件路径前解析临时根目录，沿用实际 agent 的工作区路径语义，覆盖 macOS 的 `/tmp` 符号链接。

按消息接口拆分 review，`bazaar-contrib-messages` 保留 Actor、消息／会话／附件、状态及请求和结果类型，
定义 `Messages`、`MessageService`、`MessageStorage`、写入作用域 `MessageSession` 和 `ThreadConcurrency` 公共 Protocol，
并导出同一 `MESSAGES` key。契约包只声明直接使用的 core 和 channel 公共依赖。
`bazaar-contrib-messages-node` 承接现有 MessageStore／repository、消息业务、发送与草稿、附件管理、
会话锁实现、命令分派、bcc 命令、模板及绑定生命周期，通过 `messages-node` 入口提供 `MESSAGES`；
`messages-cli` 与 message／inbox／thread 命令入口随实现发行。公共模型和模板保持原内容。
消费者通过 `accepts` 声明服务依赖，装配方通过 `ctx.accept(MESSAGES)` 获取服务并绑定某个 Agent，
将产生的消息服务显式传给 collaboration；内部调用直接使用接口对象。
`MessageStorage.write` 把通用会话读取和保存交给同一次数据库 writer 回调，
公开 `get_thread`、`get_channel_session`、`save_channel_session`，沿用原审核写入的 writer 边界及数据约束。
查询、入站登记和交付持久化保留已有读取快照及事务作用域。
共享回执转换移到消息契约的纯值转换，交付执行器消费相同转换；collaboration 不导入消息实现。
独立安装验收分别安装契约及实现 wheel／sdist，通过现有真实 TCP Channel、sidecar、持久化和重启工作流验证服务绑定；
同时核对只安装消息契约及其声明依赖时可以导入全部公共契约，默认配置、V4 转换及 manager 索引选择实现发行包。

- [x] 交付 bazaar-contrib-messages 公共契约及 bazaar-contrib-messages-node 消息、历史、收发、附件和命令实现。
- [x] 模块通过 DB 自持模型、schema 与显式查询，使用 schema 和调用登记入口，共享服务供 CLI／RPC 消费。
- [x] 默认配置选择 messages-node，由 manager 恢复所选发行包；通过真实 TestChannel、隔离数据库和文件验证消息、历史、搜索、草稿及附件流程和资源退出。
- [x] 完成独立包安装与检查，提交供 review。

### Task 13.1：统一 channel 分段交付、错误与退避重试

按本轮讨论，重试策略归 channel 公共层；具体渠道只负责拆分发送操作、调用上游及映射错误。
将 `Channel.send` 的整包发送边界改为 `prepare_send`，返回逐个文本分段、上传或附件发送的单次操作；
公共 `ChannelSender` 串行执行这些操作。每个操作独立计算尝试次数与指数退避，成功部分不重复发送，
每次重试沿用同一个 operation id；仅在渠道明确保证幂等时，才重试可能已送达的操作。
按本轮 review，`ChannelRetryPolicy.timeout` 表示每次发送尝试的完整时限：准备阶段和每个上传／消息操作的
每次尝试分别从零计时，各分段及重试沿用相同的时限；移除重复的 `attempt_timeout` 字段。
公共策略通过有限尝试次数、指数退避与 jitter 控制重试，并尊重上游 retry-after；
退避等待消费已存在的公共 TIMER，等待完成后才开始下一次尝试的计时。

公共 `ChannelError` 保留上游原始错误信息及可选错误码，并提供连接、超时、限流和拒绝等常见子类。
错误类型与发送阶段、确定未发送／可能已发送及可重试性分开表达；单次操作持续记录发送阶段，
准备、连接或上传阶段的超时不会误标为已发送未知，发送／等待确认阶段的不确定结果保留 unknown。
公共层保留每段结果及先前确认回执，后段确定失败返回 partial，不确定结果返回 unknown；
messages 直接消费统一执行结果，移除把所有异常一律归类 unknown 的旧边界。

每个实际可见的消息操作必须公开其正文、附件和目标；上传操作只保留操作回执。
公共执行器在每段返回回执后、执行下一段之前通知 messages，由 messages 立即保存该段的正文、
附件、上游消息 ID、状态和独立本地消息 ID。持久化在上游重试边界之外，保存失败不能重发已接受的段。
同一次发送的各条记录通过原始发送 ID 关联，第一条实际消息沿用命令返回的消息 ID；
聚合交付状态与完整操作回执保存在该条记录的元数据中，不覆盖其实际正文、上游 ID 或分段状态。
新建私聊在首条有交付证据的消息回执到达时建立会话并写入该段。后续分段仍在发送时，
按前段上游 ID 收到的引用即可关联已有消息，重启后各段仍分别可查询。

在已安装 wheel／sdist 中，通过真实 TCP TestChannel 控制面验证多段与附件发送、后段限流恢复、
独立退避及稳定 operation id、原始错误与阶段信息、已确认结果保留和资源退出。
在真实 TCP 往返中让后段经历多次可重试拒绝、退避及延迟确认，累计耗时超过单次时限后仍可交付，
验证每次尝试与后续分段都获得完整时限；保留丢失确认时的单次超时与未知状态验收。
逐段核对实际消息内容、附件与上游 ID 的持久化，并在后段待确认时从 TestChannel 注入引用前段的入站消息，验证引用立即关联以及重启后的记录。
验收中的只读 SQLite 查询在返回消息记录时显式关闭连接，确保 Windows 也能在流程结束后删除临时数据库。
现有消息、草稿、附件及 CLI 行为验收继续运行；按实际公共接口导入补齐依赖与 TIMER 声明。

- [x] 交付公共错误、单次操作契约及统一分段退避执行器，并接入 messages。
- [x] 扩展真实 TestChannel 与独立安装验收，完成受影响检查、Ruff 和根目录 LSP。
- [x] 在原 channel 功能提交中签名 amend，更新 PR 验证信息后停下 review。

### Task 14：为 agent 接入身份与协作

原节点的 `core/review.py`、`core/orchestration/commands/review.py` 以及
`core/orchestration/orchestrator.py` 的 `_record_inbound`／`_answer_unreviewed` 是本项迁移依据。
`messages` 已拥有 sender、channel session、thread、Actor、已知发件人查询及引用读取，
collaboration 消费这些公共契约，保持同一会话登记、审核状态和消息数据。
原 `bazaar_compute_server/refs.py` 的短数字映射与 `v02_refs` 属于服务端页面和数据源，
随 history／contacts 阶段迁移；节点本项的引用关联使用现有消息 ID 和上游消息 ID。

共享 `COLLABORATION` 依赖 `MESSAGES`、DB、AUDIT 和共享 TIMER，通过所属 agent 的绑定生命周期提供协作服务；
内部 agent／thread 参数和归属键保留 `Agent`／`Thread`，与 messages 共用会话锁。
准入以数据库中的会话审核状态为准，新会话默认待审；保留原可信入站审核标记及已保存的审核结果。
调用方经 sidecar 获取 collaboration 公共服务后查询、通过或拒绝，状态持久化和审计由服务持有。
原 `ReviewPolicy` 的 `listed`、`reviewed` 及配置白名单装配从本项公共 API 和验收入口移除。
入站登记沿用原安静回显去重、已交付出站优先的引用解析、可信入站审核标记、
原子会话登记、已拒绝会话再次入站转待审及原入站审计字段；返回原登记结果供 agent 编排消费。
channel 公共入站载荷补齐原 provider 入站消息已有的 chat ID、通知／提及标记及 metadata，
使实际渠道能够携带安静历史、引用 ID 和可信审核标记；默认值保持现有消费者行为。
未审核自动答复读取 `review.reply`，通过公共 channel 发送契约及共享 TIMER 交付，
保留答复及失败审计。审核沿用原会话归属校验、并发锁、状态持久化和 `channel.session.reviewed` 事件。
collaboration 保持独立发行，只消费 messages 公共契约。审核决策由 collaboration 持有：
在共享会话锁及同一次 `MessageStorage.write` 回调中读取 thread 和 channel session，
修改审核值后通过通用 `save_channel_session` 保存，再发布审核审计；消息接口以通用会话操作表达，
MessageStore 和 repository 的专用 `set_review` 由这一调用链替代。设置等自身业务数据仍通过 DB 管理。
设置读取及 upsert 通过公共 DB 操作现有 Node 迁移的 agent 级 `settings` 表，
`setting.changed` 只记录 key；不重复创建已属于 Node 历史的结构或版本。

本轮确认一并恢复 9 月 20 日曾实现后撤回的重复入站修复 `e0ebfbd`：
messages 的登记先用到达载荷中完整的渠道实例身份查找会话，随后复用已入库的消息并补回该身份。
原历史消息行没有保存 `channel_identity`，直接用重载消息查会话会误报身份变化；
同一上游 ID 的引用补历史或再次投递应复用原消息和会话。使用真实 TCP 入站在重启后再次投递已入库消息，
验证原审核状态、会话身份、消息记录及后续新消息均保留。

app 默认配置选择 collaboration，manager 官方索引收录其独立发行包；
V4 配置存在 agent 时添加 collaboration。各发行包按实际公共导入声明依赖。
真实外部 consumer 通过 accepts／offer／绑定调用服务，sidecar 沿用公共框架转发 typed 对象及调用。
在独立安装 wheel／sdist 的隔离节点和 TCP TestChannel 上验证两个 agent 的身份及会话隔离、
新会话默认待审、sidecar 控制的待审／通过／拒绝及再次入站、原消息可见范围、
自动答复和审计、重复入站、安静出站回显、入站及出站引用、设置和审核重启恢复。

- [x] 交付 collaboration 公共服务及持久化准入、审核、原入站关联和设置实现。
- [x] 接入默认选择、版本转换和独立发行，通过真实隔离安装验收及受影响检查、Ruff、根目录 LSP。
- [x] 独立签名提交，更新 PR 验证信息后停下 review。

本项及受影响的 channel、agent、messages、配置转换、app、manager 和根目录脚本共 15 项本地 pytest 通过。
collaboration 的独立 wheel／sdist 消费者通过真实 sidecar 操作审核和设置，
在 TCP TestChannel 上完成两 agent 隔离、引用、安静回显、自动答复及失败、重启与重复入站验收。
既有消息及 agent 的独立安装工作流和四种 manager 安装／升级工作流通过；
Ruff、仓库根目录 Pyright LSP 和原 12 份消息模板一致性核对通过。

### Task 15：为 agent 接入 Reminder

以 main `53d925f` 的 `core/models/reminder.py`、`reminder_owner.py`、`core/reminder.py`、
`reminder_schedule.py`、`core/orchestration/reminder.py` 和 `commands/reminders.py` 为业务来源，
迁入 `bazaar-contrib-reminder`；模型状态、请求与结果、周期计算、单全局 frontier 定时器、
60 秒时钟复核、失败重试、到期批处理、版本并发保护、不可用提醒取消及启动未读恢复保持原行为。
`cmd/bcc/reminder.py`、`app/resource_dispatch.py` 的 Reminder 分支及 `_format.py` 的 Reminder 格式化迁入该包，
三个 Reminder bcc 模板和错误模板逐字保留；命令经独立 `reminder-cli` 消费入口访问 REMINDER 公共服务。
命令完成事件由 `_append_audit` 统一写入审计服务。
Reminder 消费公共 `Timer` 契约时，参数和成员统一命名为 `timer`。

Reminder 自有定义和状态由 DB 公共查询操作读写，沿用 Node 迁移中已发布的 reminders 表和版本账本，
通过 SCHEMA／LEGACY_NODE_MIGRATIONS 依赖等待其就绪。消息接口补齐已有的会话列表、消息定位、
系统消息保存及全局未读恢复操作；消息服务提供可加入调用者 Session 的消息作用域，
Reminder 在 DB 的一个事务内更新提醒状态并通过该作用域保存系统消息，保留原子提交／回滚。
消息实现持有消息归属、路由校验和序号分配，不增加 Reminder 专用消息方法。
共享 Reminder 服务提供按 Actor 的命令绑定、全局 scheduler 的 start／stop／wait_failure 和唤醒回调；
执行组合在完成 agent 绑定后启动 scheduler，完整 agent 执行接线沿 Task 16 交付。

默认配置、V4→V5 的既有 agent 转换与官方索引选择 reminder；app 发行元数据仍只声明自身直接使用的包。
从实际 wheel 和 sdist 在独立环境运行真实 timer／SQLite／消息实现和 sidecar 消费者，
验证登记、修改、延后、取消、周期、到期系统消息、未读与锚点、重启恢复、两个 mode 的 Actor 范围及资源退出。

- [x] 交付 bazaar-contrib-reminder 的持久化、到期、snooze、更新、取消及唤醒规则，依赖 DB、schema、timer 和消息。
- [x] app 默认安装并启用 reminder，复用节点共享 TIMER；持久化与唤醒业务归 Reminder 插件。
- [x] 以真实定时器和隔离数据库验证到期、更新、取消、持久化及清理，完成独立安装与检查，提交供 review。

验收：本项和受影响的 messages、collaboration、配置转换、app、manager、根目录脚本共 14 项 pytest 通过。
Reminder 的 wheel／sdist 经独立节点、真实 TCP TestChannel、sidecar 和原 bcc 命令完成
登记、修改、延后、取消、周期、到期系统消息、锚点／未读、两种 mode 的会话范围及离线后恢复。
单独按声明依赖安装的 wheel／sdist 均成功导入 Reminder 全部模块，依赖闭包不含消息实现包。
Reminder 模型、请求、周期计算三个源文件和四份 bcc 模板逐字一致；
命令、scheduler 及辅助函数按 Actor／公共 timer 适配核对，dispatch／格式化方法和 CLI 文案保持原结构与内容。
四种真实 manager venv／tool 安装升级流程、workspace Ruff／format、锁一致性和 exact 根目录 Pyright LSP 均通过。

### Task 15.1：业务迁移独立登记与 Legacy Node Migration 接续

agent、messages-node、collaboration、reminder 各自持有当前表结构基线与从 1 开始的后续版本链，
直接在 Contrib.accepts 声明 SCHEMA，在自己的 lifespan 中登记 ModuleSchema，完成登记后再提供服务。
迁移按 package／version 独立记录，各包内部按版本推进。ModuleSchema 删除手填的 package；
SCHEMA.register 在当前 contrib 的生命周期上下文读取 Context.source.package，以发行包规范名登记。
Context.source 直接公开入口发现已保存的发行来源；没有发行来源的登记在写入前报错，不用实例名代替包身份。
运行期 modules 按推断出的包名索引声明，调用方只维护自有表、迁移和历史来源。
可选 Server 同步删除重复身份声明，安装验收经真实入口发现取得 Context，并使用业务自有基线验证 Node／Server 根切换。

`bazaar-contrib-legacy-node-migrations` 的 `legacy-node-migrations` 入口持有已发布的完整 Node 1～32，
历史表定义、公共操作及数据转换以 main 和 Task 15 的实现为来源，保留跨表步骤和原版本顺序。
Progress 可声明旧统一账本的 package，作为历史进度来源；已有旧表、统一账本记录及其身份均保留。
Legacy 从 `bazaar-contrib-node-migrations` 或旧 schema_migrations 接续，新增进度记入自动取得的真实发行包身份。
该 contrib 调用 SCHEMA.register(..., upgrade_only=True)，只接续已有历史进度；
空库及已完成的历史不执行建表或重新夺取归属，完成后提供 LEGACY_NODE_MIGRATIONS 就绪服务。
Node 业务 contrib 显式依赖该就绪服务，历史完成后各自登记，不扫描发行入口或集中登记业务版本。

SCHEMA 的 ModuleSchema 增加 predecessor 声明，以公共 Dependency 表达前身包及交接版本。
业务模块声明 Legacy Node 32 为前身；引擎读取前身账本及其已登记历史来源，已完成交接点时将原表归属转给该业务模块，
在同一事务登记其基线进度而不重放建表，再执行该模块新增版本。
空库没有前身进度，直接执行各包基线。独立注册、重启及后启用的业务模块均按各自账本继续。
Server 的登记与独立历史接续继续使用同一通用引擎；SQLite／PostgreSQL 只负责公共存储操作。

验收从原只读迁移生成 1～32 数据库，与原升级结果比较业务数据、关联、序号、搜索和旧账本；
从拆分前真实 0.3 实现生成已登记数据库，验证旧包身份、归属交接和已执行步骤不重放。
验证真实 contrib 启动次序、各包独立登记、当前基线、新增业务版本、重复启动及保留业务数据。
执行独立 wheel／sdist、SQLite／PostgreSQL、Server 及受影响消费者、Ruff、锁和根 Pyright LSP 检查。
从 PyPI 最早可下载的 0.1.3 和仓库最早 v0.1.0 标签分别安装真实旧实现，使用其公共存储接口生成配置与业务记录；
仅本次最早版本验收使用一次性容器，在隔离安装根直接替换为当前发行产物；其他版本继续使用已有隔离能力。
检查配置转换、原账本、业务数据、迁移和重复启动，分别记录安装、配置和完整运行的实际结果。
Sidecar 的独立安装验收仅构建应用、通用配置／管理和通信消费者实际使用的发行包，
清理未使用的 Agent／audit／channel／timer／harness 测试构建项，并验证原生传输、TCP、热更新和退出恢复。
将通用自有表／版本／lifespan 登记规范写入开发约定，在 schema README 给出简短可用示例；历史背景仅在 Legacy 包 README 简述。

- [x] 收拢已发布历史为 Legacy Node Migration，业务包仅持有自己的基线和后续版本。
- [x] 业务 contrib 自行登记，SCHEMA 按前身进度完成归属与基线交接，保留旧账本。
- [x] 从实际 contrib 发行来源自动取得登记身份，删除重复声明并验证历史接续、独立发行和重启。
- [x] 完成真实最早安装版直接升级当前产物的配置转换、数据及业务服务启动验收，记录完整 CLI 启动的当前阶段限制。
- [x] 完成历史／当前库／空库／新版本及独立安装验收，修正同一签名功能提交并停下供 review。

验收：本轮 91 项主仓库 pytest 通过，包含 core 43 项、schema 3 项、Legacy 历史／独立安装／真实业务 lifespan 38 项、
四个业务独立安装及 scripts 3 项。此前的配置转换、应用配置、manager 和 Sidecar 12 项验收已通过。
全部 32 个历史起点与原升级结果一致，已有 0.3、新增业务版本和重启保留数据与旧账本。
新增未修改的旧引擎生成的统一账本 17 版夹具，验证自动身份下接续到 32、归属交接及重启；所有业务记录与原 32 版结果一致。
SQLite wheel／sdist 验证逐包登记、基线和接续；真实 PostgreSQL 两种产物验证旧进度 17／32、空库、
实际业务启动、独立新增版本及两次重启。Server 的原升级／基线和适配登记后的消费根切换通过。
五个业务／Legacy 包的声明依赖导入及发行源码／资源／LICENSE 对照通过；README 示例独立打包并以不同入口名加载，
wheel／sdist 均从 Example_Notes 元数据取得 example-notes 账本与归属，重启保留数据。Server 三项验收在两种产物下通过。
32 项迁移与数据变换、21 项基线操作保持一致，保留声明均有引用；workspace Ruff／format、锁和根 Pyright LSP 通过。

最早安装升级验收：PyPI 0.1.3 原 wheel 与 v0.1.0 原标签构建 wheel 分别在一次性容器内独立安装，
使用对应旧版公共 API 写入真实配置、会话、收发消息、附件、游标及运行记录，随后直接换装当前真实发行产物。
V1→V5 配置转换保留 Agent 身份、provider、模型、环境引用、endpoint 与数据库位置；
原 3／8 版账本记录保持不变并接续到 32，所有现存业务表与未修改原实现的升级结果一致。
四个业务 contrib 的真实 lifespan 启动、公共消息／线程／附件／搜索及两次独立进程重启通过，附件文件哈希保持不变。
CLI 版本与直接安装通过；原配置的 CLI 依赖恢复仍需索引中尚未发布的 0.3 产物，本轮以真实本地包完成配置与业务服务验收。
完整 Agent 执行及外部 provider 运行仍由后续对应任务验收。容器及所属临时运行环境在验收后移除。

### Task 16：补齐 agent 执行、恢复与替换契约

- [ ] Channel 与 Harness 分别持有纯内存运行状态，随实例创建、销毁和重建，按 Running → Stopping → Stopped 管理退出。Stopping 停止接收新业务工作，保留既有请求所需的事件、审批、回执及回复通路；使用公共 TIMER 定期复核队列、在途请求／回复和相关后台工作，排空后再关闭资源。参考主线 `core/orchestration/orchestrator.py` 的 `_handle_runtime_expiry`／`_stop_expired_runtime_if_idle`：繁忙时继续等待，后台工作未结束则重新安排定时器，复核绑定实例及 timer generation，退出时取消所属定时器。

- [ ] 保持 Agent 身份、Actor 会话、worker 与调度状态的生命周期稳定。为已有 Agent 新增或移除 Channel 时，仅更新相应 Channel 子组合与接线，保留其他 Channel、harness 和正在运行的 Agent；移除时先停止接收新工作，保留已接收消息、排队／执行任务及其回复所需的连接和绑定，排空后再关闭 Channel 并释放子组合。验收运行期间追加和移除 Channel，检查已受理工作完成、既有会话与其他通道继续运行。

- [ ] Agent 自持运行数据，接受 DB、SCHEMA 和 LEGACY_NODE_MIGRATIONS，在自己的 lifespan 登记后提供业务服务。run 通过配置服务激活完整的已启用组合，不限定 roots；管理命令通过插件登记选择启动根。全新安装默认启用 Agent；V4→V5 仅在旧配置存在 Agent 时启用 Agent，存在 Server 使用配置时启用 Server 及对应依赖。

- [ ] 在已交付的 agent 及各能力接入基础上补齐 turn、会话绑定、inbox、调度、输出交付、审批、任务故障和恢复，按实际消费依赖消息、协作、Reminder、timer 与 builder。
- [ ] 按[Actor 验收标准](#迁移实施与验收标准)迁入 main 的 `Actor`／`Actors`：内部身份参数、业务调用与归属键全程使用 Actor，仅在边界与 `session_id`／`agent_id` 转换；保留两种 mode 的身份解析、按 Actor 归属的运行会话／状态／队列／worker／timer、业务可见范围和活跃命令绑定，分别验收各会话隔离及同 Agent 会话共享的原行为。
- [ ] 沿用 Task12.1 已交付的 developer instruction、bcc 工具入口与初始运行环境，补齐命令能力的活跃身份／会话绑定、上下文及通讯编排；按 [默认 agent 行为与替换](roadmap.md#默认-agent-行为与替换) 由 agent 选择提示词、工具及所需公共服务。
- [ ] agent 消费 channel／harness 公共包的输入、请求、输出／审批和生命周期契约，harness provider 执行 agent 提供的指令和工具环境；具体 provider 自身持有 SDK 和资源。
- [ ] 按[完整行为事件与 NodeAudit](roadmap.md#完整行为事件与-nodeaudit)接入 agent 构造输入及 harness 原始事件的完整发布，保留关联、顺序、时间、工具／技能／文件、原始 usage 和模型步骤来源；审计记录、两端上报及 BCS 消费随所属阶段接入。
- [ ] agent 组合用普通 Python 函数从已校验配置构造具体条目及稳定绑定；配置候选预检显式调用同一构造逻辑，已知组合完整校验后由 lifespan 的 ctx.enable 管理。
- [ ] 公开 agent 入口拥有 config.agents 和 channel／harness 参数模型，明确创建执行与 provider 实例；私有服务和消息事件用 local 绑定，共享服务沿用外部绑定，依赖就绪后进入执行。
- [ ] 用真实自定义 agent 组合替换默认入口，验证其配置模型、实体装配、指令、工具环境、通讯行为、所选依赖与资源退出。
- [ ] 使用 TestChannel 控制面及真实隔离资源验证消息注入、调度、输出、审批、故障和退出；具体外部 provider 的 e2e 由其交付阶段使用相同控制面验证。
- [ ] config.agents 为空时保持 AGENT／CHANNEL／HARNESS 运行实例数为零，真实实体组合验证服务可见范围和所属资源回收，完成独立安装与检查，提交供 review。

### Task 17：agent 配置管理与会话准入审核

agent 插件在 `bcn agent review` 下登记三个基础命令：

```bash
bcn agent review list <agent-id>
bcn agent review approve <agent-id> <thread-id>
bcn agent review deny <agent-id> <thread-id>
```

`list` 查询所选 agent 的待审会话，返回会话 ID、渠道、名称和审核状态；
`approve`／`deny` 对该 agent 所属的指定会话作出通过或拒绝的决定。
命令通过本机 sidecar 连接运行中的节点；命令所属消费入口显式声明 collaboration 公共服务依赖，
通过框架 accept 获取服务后调用。collaboration 持有准入规则、审核状态持久化及审计，sidecar 沿用通用转发。
本机命令和 BCS 审核入口使用同一公共服务与同一份会话审核状态。
后续 TUI／GUI 界面由独立 contrib 消费同一公共协作服务。

本任务交付本机审核入口，消费 Task 14 已提供的 collaboration 会话准入服务：新会话默认待审，
调用方经 sidecar 作出通过或拒绝决定，与具体控制端及上报配置解耦。保留已持久化的审核结果，
待审会话在通过后按原执行链路参与交互，拒绝后再次入站的状态变化沿用原协作语义。
审核操作以 `core/orchestration/commands/review.py` 和 `contrib/server/control.py` 的原调用为依据，
沿用会话归属、并发保护与审计规则。

审核由待审变为通过后，为该会话触发一次正常入站通知，沿 inbox notice 唤醒智能体，
使原请求继续得到响应，用户无需再发送一条消息。改动接在 collaboration 公共 `review_contact`
保存通过决定及审核审计之后，按 Thread 从数据库读取所属会话和已保存的入站消息，
接入 Task 16 迁入的正常通知发布入口；原 `publish_inbox_wake`、`_RuntimeNotification`、
`_notice_for`／`inbox_notice` 的调用链是通知构造与分发依据。
前半段沿用现有入站、落库和审核拦截：被拦截的原处理已结束，
审批触发的通知只凭持久化会话和消息构造，节点在入站后、审批前重启也能完成。
本机命令、BCS 和其他公共服务调用者通过同一个 review_contact 获得该行为。

- [ ] 在 bazaar-contrib-agent 的公开入口提供自身配置管理服务并登记 `bcn agent add`、列表、编辑、删除命令，按当前载体读写配置。
- [ ] 命令读取已启用 builder 的 provider 选择和实体模型，填写 agent 信息、channel／harness 参数与凭据引用，更新自身 contrib 条目的 config.agents；模型边界校验及组合展开后条件提交完整候选。
- [ ] 节点未运行时通过短生命周期管理组合完成首次默认配置创建和 agent 管理，模块选择与业务参数统一保存在相应 contribs[].enabled[].config，默认 agent 的实体参数由其 config.agents 持有。
- [ ] 管理模式启用配置、命令及所需共享登记服务，完整业务候选只执行资源启动前校验；后续 bcn run 才创建 AGENT／CHANNEL／HARNESS 实例。
- [ ] 登记 `bcn agent review list <agent-id>`、`approve <agent-id> <thread-id>` 和 `deny <agent-id> <thread-id>`，经 sidecar 消费 collaboration 服务；状态更新持久化并发布原审核审计事件。
- [ ] 审核通过并持久化后，从数据库读取该会话及入站消息，触发一次正常入站通知，经 inbox notice 唤醒响应；本机命令、BCS 和公共服务调用共用实现。
- [ ] 各子命令按实际操作选择依赖和生命周期：配置管理使用原短生命周期组合，会话审核连接运行中的节点及其协作服务。
- [ ] 自定义 agent 可登记自己的模型与管理命令；通用 app 命令分发消费登记结果，配置保存复用 CONFIGURATION。node-management 持有节点与平台管理职责，按默认节点清单接入其基础入口并在后续平台阶段补齐。
- [ ] 在未配置 BCS 的真实隔离节点上，通过 TestChannel 注入新会话，使用三个基础命令验证待审列表、本机通过后交互、拒绝后的状态变化和重启后审核结果；通过公共服务读回同一结果，并验证不同 agent 的会话归属。
- [ ] 用 TestChannel 发送自然用户请求，待审拦截后重启隔离节点，再通过本机命令或公共服务批准；观察正常 inbox notice 和实际响应，全程只发送原请求。以相同控制面验证正常审批及两种 mode 的会话归属。
- [ ] 在隔离 tool／venv 和真实示例包中完成安装、add --enable、agent add、读取保存配置、bcn run 及退出流程，完成独立安装与检查，提交供 review。

### Task 18：首阶段完整验收与合入

- [ ] 从源码 workspace 完成未发布模块的联调、入口／依赖变更后的 lock／sync 与实际宿主重启，再从逐包构建的独立 wheel 在 workspace 外完成同一流程。
- [ ] 从干净环境完成“安装 BCN → 安装日志模块 → 启用 → 启动 → 事件输出 → 退出 → 升级再启动”。
- [ ] 验证全部默认基础包的正式依赖与初始启用组合，空 agent 时的节点管理及真实示例 provider 的 add --enable／agent add／run 流程；具体 Codex／Telegram 的真实首次使用流程随其交付阶段及最终发行复核。
- [ ] 在 core 完成插件开发指南，明确入口分组与社区插件登记约定，补齐服务契约、配置、安装和资源生命周期说明，给出可实际执行的最小模块示例。
- [ ] 运行各成员的新测试与 Ruff、根目录 `uv run --quiet scripts/pyright_lsp_check.py .` 的逐包检查及包构建。
- [ ] Linux、macOS、Windows 独立 CI 完成安装与生命周期验收；native service 验收在隔离 CI runner 执行。
- [ ] 登记本仓库 app、core 与首阶段 contrib 的发行清单、共同版本和正式依赖；验证入口组、contribs 配置、contrib_indexes 与 bcn contrib 命令采用统一命名。
- [ ] 在当前特性分支提交，review 通过后按依赖合入 `0.3`，以该集成点开始路线图下一分支。

## 后续安全待办

2026-10-18 将 contrib 存储权限与 Sidecar 授权边界记为后续待办：记录风险和设计方向，暂缓实施，后续单独设计及拆分任务。
保留 Task 15.1 的 Legacy Node Migration 与业务各自登记方案，继续按现有串行 Tasks 推进和 review。
迁移下放解决表结构、业务数据和版本链的维护归属；当前进程内 contrib 的运行权限是另一层边界。

### 已确认的现状与风险

- contrib 在同一 Python 进程、同一系统权限下执行。恶意代码即使绕过 Schema，也可以自行尝试连接和修改数据库；
  把迁移重新集中管理不能消除这个既有风险。公共接口的表权限约束与不可信代码的执行隔离需要分别评估。
- Schema 已持久化表归属并检查登记冲突，MigrationSession 限制迁移写入的目标表；
  运行期 DB 仍共享后端对象，尚未按消费 contrib 的登记结果限制访问，迁移查询也没有完整的跨表权限检查。
  登记包身份由框架提供的发行来源自动取得；predecessor 和 Progress 的历史来源仍由调用方声明，不能直接当作授权凭据。
- 公共 DB 使用结构化请求，查询和增删改的 table 字段是表名，通常来自 TABLE.name；Table 对象用于结构定义和建表。
  公共 Database／Session 契约没有裸 SQL 入口，因此可在通用 storage 层分析请求并统一执行表权限约束。
- 当前 Sidecar 能为连接创建接受指定可见服务的消费实例。隔离的真实 Hub、SQLite 与 Unix socket 验证了远端可取得
  DB、STORAGE、SCHEMA，并读取另一 contrib 登记的表；该观察证明当前服务可达和共享 DB 访问，未来的表授权机制尚未实现。
  讨论重点还包括：Sidecar 是否能调用内部管理能力，给调用者追加可访问表。仅给未登记表的连接分配空权限，仍需核对授权管理入口。
- Sidecar 对远程引用对象使用公开成员读取和调用；正常方法名禁止以下划线开头，但 Symbol／Construct 路径没有相同限制。
  Symbol 按路径导入 Python 对象，Construct 导入后直接用字段参数调用目标，当前没有限制目标必须是请求数据模型。
  需核对通过服务属性、动态导入和构造取得管理能力的路径；构造一个普通对象本身不能成为真实 DB 的授权依据。

### 讨论过的方案与约束

- 最初考虑为每个 contrib 分配命名空间，随后倾向沿用现有表名和归属，按成功登记的表集合分配访问范围，
  减少表名映射和额外授权清单。未登记表的调用者取得空权限，跨 contrib 的业务操作通过数据拥有者的公开服务完成。
- 权限控制由通用 storage contrib 内部持有和执行，业务 contrib 只提交表及迁移声明、使用受限 DB。
  授权管理对象、修改归属、追加访问表和切换调用身份的能力需要与普通 DB 操作分离，覆盖 Sidecar 的自我授权路径。
  Schema 负责业务迁移编排，依赖方向保持 Schema → storage；Sidecar 继续采用通用框架转发与访问规则。
- 表声明需要在候选组合激活、执行迁移和发放权限之前检查同名冲突及已有归属。
  同一候选中的冲突应拒绝该次激活，避免先启动者抢占；归属与授权应绑定可信的 contrib 身份。
  当前在 lifespan 内逐个 register 的流程不足以完成整体预检，声明收集阶段和身份确认方式需要后续确定。
- 统一校验需覆盖直接操作及 read／write／transaction 回调中的会话，并递归分析联表、CTE、子查询、复制来源、搜索及结构变更。
  CTE 别名需要与物理表区分；删除索引、触发器等还需核对对象的实际归属，不能只相信请求填写的 table。
  已发布历史迁移中的跨表步骤和 Legacy 归属交接也需要纳入设计，保留原数据与版本账本。
- 讨论的候选接口是由 Schema 注册成功后返回 storage 创建的受限 DB，例如 register(MODULE) as db；
  当前 accept(DB) 返回共享服务对象。授权发放接口、内部管理能力的装配方式和生命周期撤销规则仍待设计。
- 原生数据库授权会引入后端差异：[PostgreSQL 使用角色管理对象权限](https://www.postgresql.org/docs/current/user-manag.html)，
  [SQLite 没有 GRANT／REVOKE](https://www.sqlite.org/omitted.html)。倾向先评估结构化存储接口上的统一约束；
  对抗同权限任意 Python 代码则需进一步评估执行、系统文件权限或数据库凭据的隔离边界。

- [ ] 后续分别定义可信 contrib 的存储接口约束、Sidecar 调用者授权与不可信代码执行的信任边界，形成可 review 的独立设计。
- [ ] 在独立设计中明确声明预检、可信身份、权限授予与撤销、远程管理能力及历史归属交接，再拆分实现与隔离验收任务。

讨论来源：本开发线程 2026-10-18，50526714-b36b-5da8-8cf6-76eb2f7d0ba2（登记决定表权限）、
c12f11c9-f8f1-5ce3-90d7-789f16439c17（同名冲突提前检查）、0385a1ee-cb4b-5f03-a0ba-7fdc177674b5（控制归 storage）、
1426c7b2-67ad-5cbf-9a93-f5d9b4088b0f（Sidecar 自我授权）、ec415eb9-9fca-5c2e-a964-83435f2fb8e0（既有执行风险）、
c90205ac-16c8-524c-99b9-7b94d9968923 与 f7caa6e3-9eae-51e3-b994-e36a506db015（暂缓并保存讨论）。

## 首阶段验收结果要求

新写的独立模块包可以声明新服务、消费现有服务、订阅业务事件，并通过配置与统一入口启用。
用户统一在顶层 contribs 启用入口并填写参数，组合明确创建具体实例，服务与事件的 local 绑定决定私有范围。提供者和实际创建者决定依赖顺序，provider 由业务 builder 按输入选择。
模块以独立函数声明 lifespan 与固定事件处理器，运行时操作通过显式 Context 参数完成。
lifespan 到达 yield 后服务与监听器就绪，回调结束后退出生命周期并撤销服务。
乱序配置仍按依赖装配；不同 agent 的服务和事件范围明确；子资源随创建实例结束。
按配置计算的依赖在启动前展开，所选后端就绪后才启动 DB 选择层，缺失及冲突诊断定位到具体配置。
同一模块多次启用时，各实例的配置与资源独立，宿主返回的句柄可以分别释放所属实例。
用户只选择模块入口与参数，组合按业务输入声明实例，宿主创建上下文和内部运行标识；配置列表与派生实例分别展示。
`bazaar-contrib-logging` 从独立 wheel 加载，实际节点输出正确；包安装、启用和节点升级共用 `bazaar-contrib-manager` 的 uv 与依赖组合。
官方与额外索引条目经包名和来源交给 uv，已安装发行元数据将包关联到入口，CLI 与页面保存相同的运行配置。
首次配置生成、保存后加载、更新已有选择条目和完整组合升级均保留用户参数与后端选择。
ConfigStore 来自启动输入选中的已安装载体入口，默认 filesystem，配置中心组合可在无本地 config.toml 的环境读写配置并在部署重建后恢复。
新建配置使用 V5；历史版本经发行自举的五版本模块链产生候选，所需包安装及配置依赖校验后经载体保留原内容、条件提交；修订冲突直接报错，由调用者重新读取和校验。
各能力的参数随对应阶段映射到所属模块；最终组合从真实历史配置验证 provider、凭据引用和用户选择保留。
app 的普通 Python 入口提供启动输入和默认资源，core 的运行接口完成发现、自举装配、就绪与完整退出。
配置加载使用配置包公共逻辑与 core 的通用候选校验接口，具体读写和转换由载体／版本模块提供。
公共宿主机制保持业务无关，新功能契约由功能包定义。

所有测试使用测试专用配置、临时数据和隔离进程。真实外部 provider 使用现有 TestChannel 控制面；
三平台 native service 测试使用独立 CI runner。测试观察有效业务流程与资源行为，使用真实实现和构建产物。
