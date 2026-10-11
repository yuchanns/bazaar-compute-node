# BCN 0.3 contrib 框架与首个模块

## 目标

在 `core/` 的 `bazaar-core` 包中提供统一的 Python 进程内 contrib 框架。Contrib 定义服务声明、生命周期函数和事件处理器，
函数通过公共 Context 提供和使用服务、发送事件。core 是框架运行内核，负责自举阶段、入口发现、配置与依赖校验、作用域以及完整启动和停止。
app 以普通 Python 调用提供启动输入与默认资源；具体功能由 contrib 接入。
节点启动流程实际使用这套框架，配置载体与日志以独立发行包接入，完成安装、发现、启用和使用。

新增业务服务的接口、查询、结果、调用入口和配置属于功能模块。宿主依据注册信息完成装配，
新增模块无需向 Context 增加业务字段，也无需向中心分发器追加业务分支。

本分支完整交付服务注册、依赖装配、作用域、事件和资源生命周期、独立状态查询模块、包发现、配置载体契约与默认 filesystem 模块、V5 配置及版本转换机制、
节点启动接入、模块及 agent 管理、默认语言／调用／存储／消息／协作／定时／Reminder 和可替换 agent 组合。后续能力依照
[0.3 路线图](roadmap.md) 逐个交付新包。工程的分支与发行规则见 [总计划](README.md)。

## 首阶段的发行结构

首个日志发行包固定为 `bazaar-contrib-logging`，Python 模块为 `bazaar_contrib_logging`，模块入口为 `logging`。
它依赖 `bazaar-contrib-audit` 的公共审计事件契约，并纳入节点默认组合及可选 server 组合。
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
全部组合使用同一个 `ContribHost` 与 `bcn run`。`bazaar-contrib-server`、`bazaar-contrib-web` 与其他模块一样按配置启用，
服务端能力由 ROOT 上的模块提供；agent 可为空，也可与服务端模块同时运行。

统一模块入口承担服务注册和依赖装配。各阶段在新成员中实现业务与调用者，按新契约组成独立的 app／core／contrib 组合。
旧 src 与旧测试保留原样，全部新能力齐备、整体验收通过后统一切换并一次清理。各功能包独立持有实现与依赖。

## 生命周期与任务管理

core 要求 Python 3.15。ContribHost 是异步上下文管理器，拥有管理插件实例的 `asyncio.TaskGroup`；
`async with ContribHost() as host` 进入后才可启用插件，`run(bootstrap, load)` 自动进入该宿主上下文。
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
core 的 SQLite、文件、TCP、嵌套宿主、故障和取消测试使用 pytest-asyncio；SQLite 资源使用 aiosqlite，
文件资源使用 aiofiles，TCP 资源使用标准库 streams，包级 pytest 配置选择自身测试目录。
细节依据 [任务组、取消和 shield](https://docs.python.org/3.15/library/asyncio-task.html)、
[aiosqlite](https://aiosqlite.omnilib.dev/en/stable/) 与 [aiofiles](https://github.com/Tinche/aiofiles)。

## 框架契约

### 模块与公共类型

Bazaar Compute Node 的 contrib 框架实现在 `core/src/bazaar_core/bazaar.py`，公共 API 由 `bazaar_core` 导出：
`ROOT`、`ScopeInfo`、`ServiceKey`、`EventKey`、`Contrib`、`Context`、`ContribHost`、`Composition`、`Candidate`、`COMPOSITION`。
整个实现、文档和模块导入使用同一个模块路径。

`bazaar-core` 是框架运行内核，管理通用模块声明、发行元数据发现、自举装配、服务图、作用域与完整生命周期；
公共类型和运行接口通过普通 Python import 直接使用，依赖仅包含框架自身所需的通用库。
`app/` 的 bazaar-compute-node 持有命令入口、启动参数、默认依赖及配置／自举资源，直接调用 core 的运行接口并等待退出信号，接入模块登记的命令；
模块索引、uv 执行和发行依赖管理归 `bazaar-contrib-manager`；
数据库、消息、渠道、RPC 和 UI 的服务契约由对应功能包持有。

```python
from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import Protocol

from bazaar_core import ROOT


# 节点组合与相关插件共享的业务枚举，由应用侧公共契约持有。
class ScopeKind(Enum):
    AGENT = "agent"
    CHANNEL = "channel"
    HARNESS = "harness"


# 应用将自己的作用域关系和 provider 限制交给通用宿主。
host = ContribHost(
    parents={
        ScopeKind.AGENT: {ROOT},
        ScopeKind.CHANNEL: {ScopeKind.AGENT},
        ScopeKind.HARNESS: {ScopeKind.AGENT},
    },
    provider_kinds={ScopeKind.CHANNEL, ScopeKind.HARNESS},
)


# 框架的泛型接口；示意代码省略内部实现。
type EventHandler[T] = Callable[[Context, T], None | Awaitable[None]]


class Context(Protocol):
    @property
    def scope(self) -> ScopeInfo: ...
    @property
    def snapshots(self) -> tuple[ContribStatus, ...]: ...
    def validate(self, composition: Composition) -> Candidate: ...
    def offer[T](self, key: ServiceKey[T], service: T) -> None: ...
    def accept[T](self, key: ServiceKey[T]) -> T: ...
    async def announce[T](self, event: EventKey[T], value: T) -> None: ...


# bazaar-contrib-storage 定义公共数据接口；消费者和提供者导入同一个 key。
class Database(Protocol):
    async def insert[T](self, model: type[T], record: T) -> None: ...
    async def find[T](self, query: Query[T]) -> Sequence[T]: ...


DB = ServiceKey[Database]("db")
```

typed key 是明确的契约对象；提供者和消费者引用同一契约包中的对象。服务表内部集中处理泛型擦除，
消费代码保留静态类型。服务诊断使用 key 的可读名称，业务调用不依赖字符串转型。

Context 由框架为每个插件实例创建并绑定。进入 lifespan 前，框架按 `accepts` 的静态声明或配置计算结果解析依赖，
等待提供者就绪，将这些服务对象注入该实例的 Context。`accept()` 读取本实例的注入表和自身提供的服务；
`offer()` 登记自身声明的实际服务对象，`announce()` 从本实例的作用域发布 typed 事件。
`ctx.scope` 返回不可变的 `ScopeInfo`，包含 kind、name、path 和 label；`ctx.snapshots` 返回不可变状态记录。
`ctx.validate(Composition(...))` 执行候选装配校验，返回 `Candidate`，其公开属性仅含状态记录和作用域信息。

宿主在框架内部持有运行实例、作用域服务表、提供者关系和依赖图，负责解析、启动和退出；
公共 `Context` 协议描述插件可使用的实例能力。`ContribHost.snapshots`、`Scope.snapshots`、`Enablement.snapshots` 和
`Candidate.snapshots` 均为 `tuple[ContribStatus, ...]`，`Candidate.scopes` 为 `tuple[ScopeInfo, ...]`。
实现参考 [Cordis 的 Context 内建管理器](https://github.com/cordiverse/cordis/blob/f8ea3cd50f1a5724e8e715995bcde131c9c12b2c/packages/core/src/context.ts)
与 [Registry/Fiber 的生命周期归属](https://github.com/cordiverse/cordis/blob/f8ea3cd50f1a5724e8e715995bcde131c9c12b2c/packages/core/src/registry.ts)：
管理器由运行内核创建，实例的依赖、监听器和子资源由同一生命周期持有；BCN 的服务注入和公开状态使用上述契约。

上述接口是 `bazaar-contrib-storage` 公共契约的节选。该包统一模型／schema 描述、显式查询、批量读写、索引、事务与错误语义。
数据模型、schema、查询条件和业务规则属于功能包；数据库 provider 负责将公共操作翻译为自身后端操作。
业务读取返回普通数据记录，关联数据通过明确的批量查询取得。
共用可见范围规则由消息业务服务提供。集中式业务 repository 及旧存储业务方法在最终发行阶段统一清理。

### 模块定义与实例

`Contrib` 定义包含：

- 模块说明和公共模块 API 兼容范围；入口名从发行元数据取得，供配置、管理和诊断使用。
- `scope: Enum` 声明实例所属的上下文种类，默认通用标识 ROOT，由模块作者引用应用侧共享契约中的枚举成员。
- `offers` 中的 typed service keys，以及静态或按配置计算的 `accepts`。
- Pydantic 配置模型。
- 可选的 `compose(config) -> Composition` 同步声明式组合工厂。
- 可选的 `lifespan(ctx, config)` 异步上下文管理器工厂。
- `listen={EventKey: handler}` 固定事件处理器映射，默认为空。

bazaar-core 发布明确的 contrib API 版本，首版对应 `0.3` 契约。BCN 必要包使用同版精确发行依赖；
独立可选与第三方包声明 bazaar-core 的最低版本和上限，如 >=0.3.0,<0.4.0，并声明公共功能契约的兼容范围。
发行依赖负责包兼容，Contrib 声明的 API 范围负责启动时复核；0.3.x 保持已公开的 0.3 框架契约。
参数及配置校验在加载边界完成，业务模块拿到自己的配置模型实例。

`offers` 是静态服务 key 序列。`accepts` 接受服务 key 序列，或接收已校验配置、返回服务 key 序列的同步 callable。
宿主在 Pydantic 配置校验完成后、建立依赖图前，为每个新实例执行一次该 callable，保存本次实例的依赖集合。
解析函数依据配置计算依赖，实际服务获取和资源创建发生在 lifespan 中。
例如数据库选择入口声明 `accepts=lambda config: [STORAGE, backend_key(config.backend)]`，
所选后端在启动前就成为明确依赖。配置更改在下一次装配时重新计算，形成新的实例依赖图。

组合入口的 `compose` 接收该入口已校验的 Pydantic 配置，返回额外的 ContribEntry、Entity 和声明诊断；原入口本身继续参与装配。
core 在建作用域和依赖图前递归展开组合，在同一展开链按定义身份检测循环，展开结果沿用同一次候选校验与 Enablement 归属。
派生条目保留来源发行包与父配置路径，实体参数由对应模块模型验证，诊断定位到 `contribs[n].config` 中的具体位置。
声明工厂只构建内存声明；资源和服务登记由 lifespan 按依赖执行。默认 agent 的 ROOT 入口通过此接口展开内部 AGENT 定义和 AGENT／CHANNEL／HARNESS 实体，自定义组合使用相同接口。
该扩展随默认 agent Task 交付；届时公共 ConfigDocument 移除顶层 agent 模型，app 的 build 只加载通用 contrib 声明，实体解释交给组合入口。

业务模块统一在配置文档顶层的 contribs 中启用。配置载体由启动参数选择，先于完整配置装配，见 [配置载体与启动自举](#配置载体与启动自举)。
`scope` 接受应用定义的普通 `Enum` 成员；core 导出的 `ROOT` 表示当前宿主根节点，公共插件默认使用它。
`ContribHost(root_kind=ROOT, parents={}, provider_kinds=None, providers=())` 接收应用声明的规则和根节点的 provider 选择。
`parents` 的键声明可创建的子作用域类别，值声明允许的父类别；父类别必须已声明，根类别只由宿主创建。
类别规则允许同类嵌套，每个实际实体仍有一个父节点。应用及相关插件引用同一份枚举成员；宿主按这些标识匹配模块与实体。
`root_kind` 可以使用应用自己的枚举成员，默认 ROOT 的插件仍在该宿主的实际根节点实例化。
`provider_kinds=None` 允许任意已声明类别的 provider 条目；显式集合限定其类别，空集合禁止 provider 条目。
实体通过 `Entity.providers` 选择 provider，根节点通过宿主的 `providers` 参数选择；普通条目在所有匹配类别的作用域自动实例化。
节点应用声明 ROOT → AGENT → CHANNEL/HARNESS 及 provider 仅属于 CHANNEL/HARNESS 的规则；Server 可以只使用根节点。
根节点关闭和普通加载器的根作用域约束按宿主根身份处理。类型、规则与配置在 Pydantic 输入边界校验，装配校验给出条目位置及实体路径。
实现采用 Pydantic 对 [`Enum` 实例的校验](https://github.com/pydantic/pydantic/blob/main/docs/api/standard_library_types.md#enums)：
保留实际枚举对象，按宿主规则检查其类别和父子关系。宿主按声明和已有运行实体自动创建实例。

| 模块声明 | 自动实例化方式 | 生命周期 |
| --- | --- | --- |
| `ROOT` | 每条启用配置在当前宿主创建一个共享实例 | 随 bcn run 启动、停止，服务可供子作用域使用 |
| `ScopeKind.AGENT` | 每条启用配置在每个 agent 上下文自动创建实例 | 随所属 agent 创建、更新、停止 |
| `ScopeKind.CHANNEL` | 在 channel builder 确定的对应连接上下文创建实例 | 随所属 channel 创建、停止 |
| `ScopeKind.HARNESS` | 在 harness builder 确定的对应运行上下文创建实例 | 随所属 harness 创建、停止 |

例如 recorder 声明 `scope=ScopeKind.AGENT`，用户在顶层 contribs 启用一次；
存在 A、B 两个 agent 时自动得到两个独立 recorder 实例。日志声明 ROOT，默认只创建一个共享消费者。
每次实例化都有独立 Context、配置模型对象、资源和监听器；类型声明决定实例归属，服务查找与事件传播仍遵循作用域树。
同一条 AGENT 配置用于各 agent，运行状态和局部数据分别属于各自实例。

channel／harness builder 依据 agent 原有的 provider 选择和连接／执行参数创建实体，
登记对应 provider 入口与运行上下文的关系，再由宿主从已启用定义创建匹配实例。
例如选中 Telegram 的 channel 只创建 telegram provider，选中 Codex 的 harness 只创建 codex provider。
普通上下文扩展随其声明的运行实体创建；具体 provider 根据 builder 的登记关系匹配。
默认 agent 的实体列表和 provider 参数保存在 agent 条目的 `contribs.config.agents`，由 agent 与所选 provider 的模型校验；其他模块参数保存在自身 contribs.config。

Contrib 是模块定义，生命周期函数与事件处理函数分别声明，再通过构造参数登记。
`lifespan` 工厂接收本实例的 Context 与配置实例，返回 `AbstractAsyncContextManager[None]`。
模块使用 `@asynccontextmanager` 编写生命周期，进入到裸 `yield` 表示启动完成；退出执行其清理代码。
返回类型注解供静态检查使用，宿主依据登记的 callable 调用；模块作者可以省略生命周期函数的返回注解。
事件处理函数接收 `(ctx, value)`，可以同步返回 None，也可以返回 awaitable。
同一 Contrib 定义的函数在每次调用时拿到所属实例的 Context。只需固定事件消费的模块可以省略 lifespan。

宿主为每个适用的条目与作用域创建一个内部运行实例，持有模块定义、目标作用域、配置和所属资源。
宿主为运行实例自动分配内部标识，用于生命周期管理与诊断。
`ContribHost.enable(...)` 返回 `Enablement` 句柄，拥有本次启用的条目注册及其当前和未来实例；关闭句柄撤销这些注册并按依赖逆序清理实例。
用户在顶层 contribs 选择入口并填写参数，实例归属由模块 scope 声明及实体创建过程确定。
同一包可以声明多个模块，同一启用定义可以产生多个运行实例；各实例持有独立配置模型、Context 和资源。

宿主为每个运行实例创建独立的 Context，并保存该次进入的上下文管理器。
模块进入 lifespan 前，其 `accepts` 声明的服务已在目标作用域可用。
服务提供者在 lifespan 的 yield 前调用 `ctx.offer()`；宿主进入完成后核对 `offers` 中的服务均已就绪，
然后启用本实例的事件处理器，使依赖它的模块继续启动。

### 宿主运行接口

`ContribHost.run(bootstrap, load)` 是 core 提供的异步上下文管理器。bootstrap 是 app 或组合模块提供的自举入口与参数，包含由调用方按功能契约构造的期望 scope 与服务 key；
load 是普通 ROOT `ContribEntry`，定义声明本组合使用的服务依赖，并在 lifespan 中提供 `COMPOSITION`。
core 创建 ROOT、发现并校验入口、按依赖进入自举模块，再启用加载器；加载器的 Context 接收声明依赖的注入，
提供 `Composition` 或经 `ctx.validate()` 校验的 `Candidate` 后 yield。core 随后装配业务模块和实体，全部服务就绪后 yield 宿主。
退出时先结束业务和在途操作，再结束加载器，最后按依赖逆序释放自举模块与 ROOT；
发现、加载、校验或部分启动失败均由同一宿主回收已取得资源。

BCN 的配置加载器声明 `accepts=[CONFIG_STORE, CONFIG_V5]` 和 `offers=[COMPOSITION]`，
使用配置包的公共逻辑读取文档、调用发行目标版本服务，通过 `ctx.validate(Composition(...))` 检查模块参数、实例展开、依赖与冲突，
再经载体条件提交，最后 `ctx.offer(COMPOSITION, candidate)`。加载器配置绑定载体选择、目标版本与 defaults.toml 资源，
app 通过普通 Python 参数将自举条目和加载器传给 `ContribHost.run`。
配置契约的 key、文档布局与历史转换由配置包持有，core 按通用条目和运行实体执行装配。
CLI、管理和升级入口复用相同的配置加载与候选提交逻辑。
server 的独立宿主使用同一运行接口，其普通加载器保持原 BCS 文档结构与独立配置、数据资源。

### 生命周期与事件处理示例

存储组合包含后端注册表、具体后端和数据库选择三个入口，recorder 只声明业务使用的 DB 依赖。
`bazaar-contrib-storage` 导出 `Database`、`BackendRegistry`、`STORAGE`、`DB` 和 `backend_key(name)`，
并提供 `storage`、`database` 两个入口；SQLite 包持有连接实现和配置模型，消息包持有消息类型与事件 key。

`BackendRegistry.register(name, backend)` 返回同步上下文管理器，进入时登记该作用域的后端、退出时撤销。
`get(name)` 返回已登记的同一 `Database` 对象。连接的关闭由创建连接的后端模块负责。
`backend_key(name)` 在公共包中按名称缓存 typed key，同一个名称的调用返回同一契约对象，
诊断名称采用 `storage.backend.<name>`。不同后端名对应不同 key，同一注册表的后端名唯一。
每个存储组合的 `storage`、后端和 `database` 挂在同一 ROOT 作用域，子作用域消费者通过父层取得 DB。

```python
from contextlib import asynccontextmanager

from bazaar_contrib_storage import DB, STORAGE, BackendRegistry, backend_key
from bazaar_core import ROOT, Context, Contrib
from pydantic import BaseModel, Field


class SQLiteConfig(BaseModel):
    path: str


class DatabaseConfig(BaseModel):
    backend: str = Field(min_length=1)


SQLITE = backend_key("sqlite")


@asynccontextmanager
async def storage_lifespan(ctx: Context):
    ctx.offer(STORAGE, BackendRegistry())
    yield


@asynccontextmanager
async def sqlite_lifespan(ctx: Context, config: SQLiteConfig):
    db = await open_database(config.path)
    try:
        with ctx.accept(STORAGE).register("sqlite", db):
            ctx.offer(SQLITE, db)
            yield
    finally:
        await db.close()


@asynccontextmanager
async def database_lifespan(ctx: Context, config: DatabaseConfig):
    ctx.offer(DB, ctx.accept(STORAGE).get(config.backend))
    yield


async def record(ctx: Context, message: Message):
    db = ctx.accept(DB)
    await db.insert(Message, message)
    await ctx.announce(MESSAGE_RECORDED, message)


storage = Contrib(
    scope=ROOT,
    offers=[STORAGE],
    lifespan=storage_lifespan,
)

sqlite = Contrib(
    scope=ROOT,
    config=SQLiteConfig,
    accepts=[STORAGE],
    offers=[SQLITE],
    lifespan=sqlite_lifespan,
)

database = Contrib(
    scope=ROOT,
    config=DatabaseConfig,
    accepts=lambda config: [STORAGE, backend_key(config.backend)],
    offers=[DB],
    lifespan=database_lifespan,
)

recorder = Contrib(
    scope=ScopeKind.AGENT,
    accepts=[DB],
    listen={MESSAGE_RECEIVED: record},
)
```

`DatabaseConfig.backend` 是必填后端名，默认选择由默认组合的 TOML 明确写入。
宿主按 `storage → sqlite → database → recorder` 启动，配置中的条目顺序不影响该依赖关系。
SQLite 到达 yield 时，注册表中已有连接，`SQLITE` 服务也已就绪，database 才把所选连接登记为 DB。
这里存储组合在根作用域提供共享连接，recorder 在各 agent 作用域分别实例化。
每次调用 `record(ctx, message)` 时，`ctx.accept(DB)` 都返回当前作用域中存续的连接对象。
函数之间通过明确的服务登记与获取共享对象；上下文管理器自己的局部变量用于退出清理。
recorder 的消息记录持久保存在数据库中。
停止时先结束 recorder，再撤销 database 提供的 DB，随后 SQLite 撤销后端登记并关闭连接，最后撤销 STORAGE。
database 的 lifespan 管理 DB 的登记归属，所选连接的资源所有权仍属于 SQLite。
该示例展示 agent 范围的事件消费者；各实际能力在默认组合中的实例归属见 [路线图](roadmap.md)。

公共包及 SQLite 包分别将相应定义登记到发行元数据：

```toml
# bazaar-contrib-storage 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
storage = "bazaar_contrib_storage:storage"
database = "bazaar_contrib_storage:database"
```

```toml
# bazaar-contrib-sqlite 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
sqlite = "bazaar_contrib_sqlite:sqlite"
```

### 依赖与作用域

服务作用域形成树：ROOT → AGENT → CHANNEL / HARNESS 实例。每个 ContribHost 持有独立 ROOT。
组合模块可在自己的 lifespan 持有另一 ContribHost；服务查找、唯一 provider 校验与实例资源分别限定在各自服务图，
跨宿主的通信通过明确提供的接口。bazaar-contrib-server 以此管理独立 BCS 配置与 storage，退出由所属组合生命周期收回。
查找服务时先查当前作用域，再逐级查父作用域。子作用域可以提供自己的实现，兄弟作用域通过明确地址调用。

宿主读取顶层 contribs，加载定义并校验 Contrib.scope，按声明建立自动实例化关系。
ROOT 实例先按依赖装配；创建 agent 时进入其上下文并装配全部已启用 AGENT 定义，
channel／harness builder 为选中的 provider 创建对应子上下文，再装配匹配定义和上下文扩展。
已有 agent 停止或新增时，只回收或创建该实体所属的实例，统一模块配置继续保留。
AGENT 定义在 agent 列表为空时保持已启用、实例数为零；后续创建 agent 时自动实例化。

每个上下文进入 lifespan 前建立完整依赖关系：

1. 校验每个实例的配置，展开静态 `accepts` 或调用其配置解析函数，记录完整依赖集合。
2. 根据每个实例的目标作用域和 `offers` 建立服务索引，按已展开的依赖寻找当前或父作用域的提供者。
3. 同一作用域中的同名服务提供者具有唯一性；有多个候选时给出模块入口与具体配置位置。
4. 拓扑装配使消费者按依赖就绪顺序启动，配置中的书写顺序不决定依赖顺序。
5. 缺少提供者或形成环时，诊断列出模块入口、配置位置、所属作用域和服务 key。

存储后端可以同时启用：SQLite 提供 `backend_key("sqlite")`，PG 提供 `backend_key("pg")`，
database 只依赖配置选中的后端并提供 DB。其他已启用后端依赖 STORAGE，各自按其生命周期创建和关闭连接。
选择 PG 时，启动依赖为 `storage → pg → database → 业务`；SQLite 的启停由其配置条目决定。
选择 `backend="pg"` 而配置中没有启用对应提供者时，启动前报告缺少 `storage.backend.pg` 及 database 的配置位置。
同一作用域重复启用同名后端，或重复启用提供 DB 的选择入口，均在进入 lifespan 前报告服务冲突。

服务的所属作用域与注册它的模块生命周期分别记录。agent 停止时，先停止其 channel / harness 子作用域，
然后停止 agent 模块；节点停止时，先结束所有 agent，再按依赖逆序结束节点模块。

### 事件与资源生命周期

`EventKey[T]` 和事件载荷类型由发布事件的功能包定义。模块通过 `Contrib(listen={...})` 声明监听器，
宿主在实例启动就绪后登记监听器，并绑定该实例的 Context。
事件从发布作用域向父作用域传播：节点消费者可以观察各 agent 事件，agent 消费者只接收自己的子树事件。

`announce()` 调用 `handler(ctx, value)`，并等待同步或异步消费者完成，监听器顺序和错误传播有统一语义。
所有应收到事件的监听器都得到处理机会，消费者错误在完成本次分发后交给发布者处理。
需要持久队列、重试或丢弃策略的消费者，自己提供对应服务契约。

宿主使用 `AsyncExitStack` 管理进入的 lifespan，各模块按启动依赖逆序结束。
停止一个实例时，先停用其监听器并等待正在执行的回调结束，再退出 lifespan，最后撤销该实例登记的服务。
消费者先于其提供者结束，回调执行期间依赖的服务保持可用。
模块在 lifespan 中使用 `with` / `async with` / `try ... finally` 管理连接、输出 handler、
功能服务上的注册句柄和后台任务；对应资源的关闭、退订及等待由这些上下文完成。

进入 lifespan 时失败，由该上下文管理器的清理代码回收已创建的资源；宿主撤销本次登记。
进入完成但服务就绪检查失败时，宿主退出该 lifespan，并撤销本次登记。
随后按依赖逆序结束故障实例及其依赖者。退出失败时仍撤销该实例的服务登记，并持续完成其余清理；
故障清理记录错误，显式停止将汇总错误交给调用方。
正常取消保留调用方取消语义。应用边界给启动和停止过程整体时间预算；重复停止可安全完成。
新模块的长期任务使用 core 独立提供的 `ITaskFailureSource` / `TaskFailureSignal`，由宿主停止故障实例及其依赖者。
初始化失败保留来源诊断，同一组合中有效的独立实例继续就绪。
`ContribState[E]` 是 `Starting`、`Running`、`Stopping[E]`、`Stopped[E]`、`Failed[E]`、`Blocked[E]`
组成的联合类型，各状态均为不可变、带 slots 的数据类型。运行实例和 `ContribStatus.state` 使用同一套状态类型；
实例保存原始异常用于传播和日志，快照中 `E` 为异常类型和描述文本。`Failed.error` 保存自身故障，
`Blocked.error` 和 `Blocked.blocked_by` 保存依赖故障及来源实例标识，数据直接从状态对象读取。
退出过程的 `phase` 为 stopping、exiting、aborting 或 stopped；停止请求、生命周期退出、强制取消和退出完成更新此阶段，
故障状态保留其类型和原因。`exit_error` 保存独立的资源清理异常。故障资源清理后，宿主、启用句柄与作用域的
snapshots 仍保留启用条目的故障状态；定义停用或作用域结束时移除记录。
状态变化与实例移除由 `CONTRIB_CHANGED` announce，观察插件通过 listen 接收 `ContribChange`，
其中 `snapshots` 包含变化实例的状态快照，`removed` 包含移除实例的标识。
快照包含实例标识、声明名称、配置位置、作用域、运行状态、异常描述和故障来源标识。
查询与通知复用插件协议。状态观察回调报错只作为此次事件的错误记录，不中断宿主或其他观察者。
被加载插件无需编写状态登记代码。加载与生命周期状态由宿主记录；健康 probe 是插件按自身业务提供的检测能力，
运行状态不代表健康 probe 的结果。

### 状态查询模块

`contrib/status/` 的 `bazaar-contrib-status` 独立发行包导出 `IContribStatus`、`CONTRIB_STATUS` 和 `status` 定义，
只依赖同版 `bazaar-core`。`status` 在 `bazaar_compute_node.contribs` 登记入口，声明 ROOT、
`offers=[CONTRIB_STATUS]` 和空配置，通过普通 lifespan 提供查询服务。
`IContribStatus.snapshot()` 同步返回 `tuple[ContribStatus, ...]`，每次读取 `ctx.snapshots` 的状态记录；
已返回的快照保留查询时的状态，包含仍被宿主保留的 failed、blocked 记录。
core 持有状态记录、数据类型和变更事件；查询服务的协议、key 与插件实现由 status 包持有。
app 默认组合和 server 的独立 ROOT 组合分别启用 `status`，消费者声明 `accepts=[CONTRIB_STATUS]` 并通过 accept 查询，
各宿主提供自身实例的快照。状态通知仍订阅 core 的 `CONTRIB_CHANGED`，查询和通知都使用普通插件协议。

### 发现与配置

公共 entry point group 为 `bazaar_compute_node.contribs`，入口返回上述 `Contrib` 定义。
模块以功能名称导出 `Contrib` 定义，例如 `storage`、`database`、`recorder`；入口声明引用模块路径与对应的导出对象名。
`core` 的宿主通过 `importlib.metadata` 读取当前 Python 环境中的发行包及其入口，建立
`发行包 → 入口名 → 导入对象` 的对应关系。启动时以配置中的 `contrib` 值查找入口，再调用 `EntryPoint.load()` 取得定义。
管理服务可以在独立的目标解释器进程中加载定义，取得配置模型、scope 类型、API 兼容信息和服务声明。
实例资源在宿主进入 lifespan 时创建；已安装列表通过元数据展示包和入口。
包名、版本、入口名和导入对象来自发行元数据，安装源来自宿主管理的依赖清单。
同名入口冲突在加载边界报告具体发行包、版本和入口，模块定义使用该入口名作为管理及诊断名称。

```toml
# 独立模块发行包
[project.entry-points."bazaar_compute_node.contribs"]
logging = "bazaar_contrib_logging:logging"
```

`contrib` 选择 entry point 的名字，发行包名用于包管理。一个包可以提供多个入口，
这些入口统一在顶层配置；AGENT 定义启用一次，每个 agent 运行时自动创建独立实例。

```toml
# 节点 config.toml 的节选。
version = "5"

contrib_indexes = [
    "https://raw.githubusercontent.com/yuchanns/bazaar-compute-node/main/contrib/catalog.json",
]

[[contribs]]
contrib = "languages"

[[contribs]]
contrib = "language"
[contribs.config]
backend = "en"

[[contribs]]
contrib = "storage"

[[contribs]]
contrib = "sqlite"

[contribs.config]
path = "bcn.sqlite3"

[[contribs]]
contrib = "database"

[contribs.config]
backend = "sqlite"

[[contribs]]
contrib = "logging"

[contribs.config]
level = "info"

[[contribs]]
contrib = "recorder"

[[contribs]]
contrib = "agent"

[[contribs.config.agents]]
name = "A"

[[contribs.config.agents]]
name = "B"
```

示例为配置节选，完整 agent 字段及 channel／harness 选择和参数由 agent 插件与所选 provider 的模型定义，保存到该条目的 config.agents。
languages、language 来自 bazaar-contrib-language，提供默认英语及共享翻译服务；storage、database 来自 bazaar-contrib-storage，
SQLite、日志与 recorder 由各发行包声明入口。
storage、sqlite、database 和 logging 声明 ROOT，分别创建一个共享实例；recorder 声明 AGENT，
同一条启用配置自动用于 A、B，分别创建独立实例、配置模型、Context 和监听器。
A、B 的 recorder 各自消费所属子树事件，通过父层取得同一个 DB；根日志消费各 agent 上报的事件。
公开 agent 入口在顶层 contribs 启用一次，声明 ROOT；其 compose 从 config.agents 展开实体和内部 AGENT 执行定义，由各 agent 子作用域管理执行实例。
provider 入口与上下文扩展也统一启用，channel／harness 的具体实例由 builder 根据实体选择创建。

filesystem 用户可以直接编辑 config.toml；CLI／web 页面选择入口、填写参数后，调用同一配置管理服务，经当前 CONFIG_STORE 保存。
启用配置统一存于顶层 contribs；agent 插件的 config 拥有实体列表及 channel／harness 参数，app 与通用配置文档读取该模型的展开结果。
管理服务校验模型、模块 scope 声明及实际实体展开后的依赖图，更新已有条目；失败时保留原配置并给出诊断。
停用时移除顶层启用条目，包安装记录继续保留。配置变更在下一次宿主启动时生效。
配置列表展示一条统一声明，运行列表展示它生成的实例、所属实体与状态；通过实例查看配置时定位到同一来源条目，
编辑该条目影响其派生实例。agent 等实体的独立启停由相应实体管理入口完成。
运行标识由宿主自动生成，管理结果分别展示已保存配置、实际实例状态与待启动变更。

### 配置载体与启动自举

`bazaar-contrib-config` 是公共配置契约包，导出 `ConfigStore`、`ConfigSnapshot`、`CONFIG_STORE`，
`ConfigConverter`、`ConfigCandidate`、`CONFIG_V1` 至 `CONFIG_V5` 及
`ConfigConflict`、`ConfigUnavailable`、`ConfigFormatError`、`ConfigCommitUnknown`。载体负责文档读写、序列化与条件提交；
版本模块负责历史输入识别、逐版转换、字段映射和候选生成；配置包的普通加载器调用发行指定的目标版本服务，
经 core 的通用模型／服务依赖校验后组织条件提交。core 的运行接口执行自举与加载阶段，app 提供启动输入和发行资源。CLI、管理页面、组合导入和升级预检使用同一管理入口、版本服务与当前载体。

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
载体保留被替换内容的可恢复版本，成功返回实际提交后的快照；明确未提交的失败保持先前配置可读取。
写入后响应丢失等结果未知的情况抛出 ConfigCommitUnknown，管理入口重新读取并核对当前内容与修订，
显示已提交、冲突或仍无法确认的实际状态，后续操作依据该状态继续。
宿主记录载体入口、旧／新修订和变更摘要，载体提供备份位置或历史修订的诊断信息。

默认发行包为 `bazaar-contrib-config-filesystem`，模块为 `bazaar_contrib_config_filesystem`，
入口为 `config-filesystem`，声明 ROOT、offers=[CONFIG_STORE]，由 app 的发行依赖安装。
它使用自身的 FileConfig 模型校验 path，读取 TOML 并以原始字节摘要作为 revision；
经该接口提交的写入在同一配置锁内复核当前摘要，保存原始字节备份，
以同目录临时文件、fsync 和原子替换提交 TOML。
备份名采用 `config.toml.v<输入版本>.<timestamp>.bak`，权限及父目录处理复用现有平台规则，
POSIX 上配置和备份权限为 0600。来源没有配置时，宿主生成 V5 默认候选并以条件创建提交。

默认载体沿用统一模块生命周期，FileConfigStore 实现上述文件读写契约：

```python
from contextlib import asynccontextmanager
from pathlib import Path

from bazaar_contrib_config import CONFIG_STORE
from bazaar_core import ROOT, Context, Contrib
from pydantic import BaseModel, ConfigDict


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path


@asynccontextmanager
async def lifespan(ctx: Context, config: FileConfig):
    async with FileConfigStore(config.path) as store:
        ctx.offer(CONFIG_STORE, store)
        yield


filesystem = Contrib(
    scope=ROOT,
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
其配置模型描述连接地址、配置标识、凭据环境变量引用等作者需要的参数；客户端库属于自己的发行依赖，
连接由 lifespan 创建并在退出时关闭。配置中心载体按其后端实现条件写入和旧版本保留。
宿主通过公共接口读写，实际协议与客户端实现由载体拥有。

启动选择固定使用以下输入规则，配置命令与升级入口复用相同选择：

- `--config-provider <entry>` 选择已安装入口；缺省为 config-filesystem。
- `--config-options <JSON object>` 提供所选入口的模型参数；缺省为空对象。
  JSON 和 Pydantic 校验均在装配边界执行。
- `--config <path>` 是 filesystem 的路径简写，优先于 options.path；未设置时使用原默认配置路径。
  其他载体使用自己的参数模型，收到 filesystem 路径简写时给出参数诊断。

```sh
bcn run
bcn run --config /srv/bcn/config.toml
bcn run --config-provider acme-config --config-options '{"url":"https://config.example.com","key":"node-a","token_env":"ACME_CONFIG_TOKEN"}'
```

core 的 ContribHost 先从 Python 发行元数据发现入口，检查模块 API 兼容及自举条目传入的期望 scope 与服务 key；
配置包的启动参数绑定为载体条目指定 ROOT 和 CONFIG_STORE，框架按通用声明检查所选定义的 offers，
校验启动参数模型，再在 ROOT 进入其 lifespan，到 yield 后核对实际服务供给。
不存在的入口、重复入口元数据、非配置服务入口、参数错误、缺少自举依赖和声明未供给服务均定位到对应入口及字段。
载体的连接资源由自己的 lifespan 创建；accepts 在此时已有的 ROOT 中按通常规则校验。
宿主同时从 app 自举资源的固定版本组合装配本次发行指定的目标版本入口，按 accepts 解析完整版本依赖链。
载体与目标版本服务就绪后，框架启用声明 `CONFIG_STORE`、`CONFIG_V5` 依赖的普通配置加载器；
加载器读取文档、生成并校验当前版本候选，条件提交后提供 `COMPOSITION`，框架再在同一 ROOT 装配文档顶层 contribs 中的业务模块。
这些自举实例保持存续，业务列表保持业务启用声明；候选服务图把 CONFIG_STORE 和已就绪版本服务作为已有供给，按同一唯一 provider 规则校验新增条目。
读取失败报告所选来源与诊断。
退出先结束业务与配置操作，再按依赖退出版本组合与载体 lifespan，撤销其服务并关闭连接。

自举输入统一由进程启动 flags 提供。配置中心模块预装在候选环境或 Pod 镜像中，
Pod 的容器启动参数指定入口和连接参数后直接读取远端配置，配置的读写与持久化由远端载体完成。
监督进程、native service 定义与升级候选进程通过 flags 传递同一入口及参数／凭据引用；启动方式重建后继续使用同一配置标识。
所选第三方载体的包、版本范围和包源纳入已有的额外依赖清单，安装操作通过已选 package/source 执行，
启动入口查找使用已安装元数据。最小自举选择与完整业务文档分别输入，文件示例表示 filesystem 的默认表示形式。

### 配置版本模块

`bazaar-contrib-config-versions` 的模块为 `bazaar_contrib_config_versions`，由 app 安装并锁定同版依赖。
五个入口均为 ROOT，使用空的 Pydantic 配置模型，分别提供公共包中唯一的 typed key。
本次发行的自举组合选择 config-v5，按下表依赖先装配 V1，再逐个装配到 V5；
载体选择来自启动输入，版本组合来自发行定义，二者都先于完整业务文档装配。
app 的自举资源保存五个入口名和目标入口 config-v5；宿主按已安装元数据取得定义，
用目标入口及其 accepts 在该固定组合中建立依赖图。

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
新版入口和已有入口都保持实际服务供给与 ContribHost 的就绪校验。

V5 模块的完整登记方式如下；V2 至 V4 按相同方式取得上一版服务，V1 提供最初解析服务。
V5Converter 的 convert 按上面的规则调用上一版，并执行本节的 V4→V5 映射。

```python
from contextlib import asynccontextmanager

from bazaar_contrib_config import CONFIG_V4, CONFIG_V5
from bazaar_core import ROOT, Context, Contrib


@asynccontextmanager
async def v5_lifespan(ctx: Context):
    ctx.offer(CONFIG_V5, V5Converter(ctx.accept(CONFIG_V4)))
    yield


v5 = Contrib(
    scope=ROOT,
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
下一版追加 config-v6、CONFIG_V6 和 V5→V6 转换，并更新发行的目标入口，前五版继续提供其原有转换服务。
旧文档中的 version 标识输入格式，自举目标入口标识本次发行需要的输出格式。
版本模块与载体的连接资源、服务归属及退出使用同一个 ContribHost 和 ROOT；随本次宿主退出，在业务及在途配置操作结束后释放。

### V5 配置与版本转换

配置文档以顶层 `version="5"` 标识结构版本，与 BCN／模块的发行版本分别管理。
顶层保存 `version`、`contrib_indexes` 和统一 `contribs`；默认 agent 的实体配置归 `contrib="agent"` 条目的 `config.agents`。本地传输地址、语言、数据库、
审计和版本检查等能力参数保存在其所属模块的 config。
默认 agent 模型保留 id、name、mode、idle_timeout、channel 数组及 provider kind 和业务参数的实体含义；旧 runtime 数组映射到该模型的 harness 数组。
运行实例的 scope 由 Contrib 声明，内部标识由宿主生成。

版本服务沿 V1→V2→V3→V4→V5 推进，保留前三步的已发布转换，由 V5 服务追加 V4→V5 转换和目标版本候选。
版本缺省的既有 BCN 文档沿 V1 入口处理，载体返回 None 时直接生成 V5 默认组合。
V4→V5 在内存中生成候选配置和 required packages；
用原字段的有效默认值补全输入，再根据以下映射形成对应入口及参数。
官方旧 provider 的名称到新入口使用本节的明确映射表，转换在配置边界执行并接受相同 Pydantic 校验。
额外旧 provider 没有既定映射时，保留原快照，列出其原入口、参数位置及目标模块配置需求，由用户选择对应的新入口。

| V4 输入 | V5 结果 | 保留与默认规则 |
| --- | --- | --- |
| node.storage="sqlite"、node.database_name | storage、sqlite、database；database.config.backend="sqlite"，sqlite.config.path 为原数据库位置 | 缺省使用原 bcn.sqlite3；相对路径按原数据目录解析，保持同一数据库文件 |
| 其他 node.storage 与该后端参数 | 对应后端入口及 database.config.backend | 按已确定的后端映射保留参数；缺少映射时保留原配置并列出原字段与新入口选择需求 |
| node.audit="logging"、node.logging | logging 与对应 config | 保留日志参数；默认 audit 仍映射 logging |
| node.audit="server"、node.server | server-client，启用事件上报及所需 storage／schema／timer 入口 | client 配置保存原 url、token_env，通过 listen 消费审计事件；所需 bazaar-contrib-server 纳入额外依赖 |
| node.control="server"、node.server | 同一 server-client，启用远端请求 | 与事件上报共用 client 条目及 HTTP 会话，保留原 url、token_env 和请求设置，通过已绑定的 offer 服务执行 |
| node.control="none" 或缺省 | 本地调用入口，client 的远端请求能力保持停用 | 本地 commands／local-rpc 按原功能建立，client 的两种能力分别按旧选择生成 |
| node.endpoint | local-rpc.config.endpoint | 保留 socket／pipe 地址；缺省采用原平台的默认地址 |
| node.version_check | node-management.config.version_check | 显式 false 保持 false，缺省为 true |
| node.lang="en" | languages、language，language.config.backend="en" | 英语实现随 bazaar-contrib-language 安装 |
| node.lang="zh-CN" | languages、language-zh-cn、language，backend="zh-CN" | required packages 加入 bazaar-contrib-language-zh-cn，记录包来源与版本要求 |
| node.lang 缺省 | languages、language；写入原生效语言 en 或 zh-CN | 迁移在所属节点按原系统语言规则解析一次，写入确定值；新建 V5 默认 en |
| 其他非空 node.lang | 保留原语言标识作为 language.config.backend，关联明确声明该标识的语言后端 | 从已安装定义的 offers 匹配 language_backend_key(lang)，保持语言标识；缺少对应后端时显示安装／选择需求 |
| 原 agent、channel／runtime 配置 | agent.config.agents 及其 channel／harness 数组；对应公共登记与 provider 启用条目 | id、name、顺序、mode、超时、模型、effort、sandbox、env、凭据引用及 provider 参数逐项保留 |

执行、消息、协作、Reminder 等原有固定能力写成顶层模块条目；channel／harness 注册按原有 provider 选择生成。
转换以原生效能力为依据，明确的审计／控制选择覆盖相应默认组合内容；已有 V5 读取保留原启用列表。
默认 agent 的配置整体写入 agent 启用条目的 config；原 runtime 字段转为 harness，按 provider 模型保留数组顺序和参数。
Telegram、Lark、WeCom 分别映射到同名 builder 和运行入口；Codex 映射 codex-builder／codex，
Claude Code 的原 kind="claudecode" 映射 claudecode-builder／claudecode。
不同 agent 使用同一 provider 时，全局定义只启用一次，各实体保留自己的参数并自动创建独立实例。
node.server 被审计和控制共用时只生成一个 server-client 配置，原参数表中的每个字段明确归入该入口的连接、事件上报或请求设置。
事件上报与远端请求独立选择；原 audit="logging" 的节点仍保留 logging，原 control="none" 的节点保持远端请求停用。
已设置的自定义 provider 使用已确定的映射和目标入口，缺少对应关系时提示用户补齐该部分配置。

每个被转换字段都记录目标配置位置；没有映射的非空字段或歧义返回具体位置及处理建议，原快照保持可恢复。
凭据引用、环境映射、数据目录、工作区、数据库文件和 agent 身份继续指向原资源。
转换产生新配置与包需求，数据迁移由相应 schema 模块按原 ledger 执行。
版本读取、状态推进、V4→V5 映射和候选生成归配置版本模块；配置包的普通加载器经 `ctx.validate()` 校验候选后通过 CONFIG_STORE 条件提交，各能力阶段同步其字段映射及新配置模型。
首阶段交付 V5 模型和版本链、候选及提交机制；语言、数据库、provider、远程和管理阶段分别补齐映射和产物，
通过真实已交付组合验证其转换。最终发行阶段使用全部正式模块完成完整旧配置升级。

升级与首次加载使用以下顺序：

1. 根据自举选择装配 CONFIG_STORE 和发行的 CONFIG_V5 依赖链，读取原快照与修订，调用目标版本服务在内存中推进到 V5，生成候选配置、字段变更摘要及所需包清单。
2. 升级使用含目标宿主、所选载体包和固定版本组合的隔离预检环境，转交同一自举参数，由目标解释器执行迁移预检，
   将所需包和保存的默认／可选／第三方依赖一起交给自身 uv，
   保持已有来源与版本固定；所需包在最终安装前加入同一个候选 pyproject 和 pylock。
   中文包使用官方索引的明确 package/source 关联；第三方语言和 provider 使用其已有来源或用户选定来源。
3. 安装完成后在目标解释器加载入口定义，以 Pydantic 校验配置，展开实例与 accepts 并检查完整依赖图。
   直接 bcn run 加载旧文档时使用已安装环境完成同样检查；缺少包时报告所需包和安装操作，待安装后重试。
4. 使用读取时的 revision 调用 CONFIG_STORE.write，原内容备份／历史保留与条件提交由载体完成。
   ConfigConflict 时重新读取，重新执行转换及全部候选校验后再提交；更新后的所需包一并重新解析。
5. 记录转换结果、载体、旧／新修订、备份或历史位置、所需包与配置摘要；V5 后续读取使用已保存文档，
   启用条目、停用选择和后端参数继续保留。

配置并发保护由载体的条件提交完成，依赖解析和安装使用同一环境操作锁。
版本推进和候选校验期间保留原快照，包安装失败保留原配置及声明并报告实际环境状态。
未知未来版本在读取边界诊断；重复读取成功迁移后的 V5 得到相同启用配置。
V5 手写文件、CLI、页面和 defaults.toml 导入使用同一模型与保存入口。
默认组合用于新建，组合导入按已有条目保留参数、停用及 DB／语言选择。

以下展示 V4 中文选择与对应的 V5 节选，其余能力按映射表同步生成：

```toml
# 升级前的 V4 节选。
version = "4"
[node]
storage = "sqlite"
database_name = "bcn.sqlite3"
audit = "logging"
lang = "zh-CN"
version_check = false
```

```toml
# 升级后的 V5 节选；bazaar-contrib-language-zh-cn 已纳入额外包清单并安装。
version = "5"

[[contribs]]
contrib = "languages"

[[contribs]]
contrib = "language-zh-cn"

[[contribs]]
contrib = "language"
[contribs.config]
backend = "zh-CN"

[[contribs]]
contrib = "storage"

[[contribs]]
contrib = "sqlite"
[contribs.config]
path = "bcn.sqlite3"

[[contribs]]
contrib = "database"
[contribs.config]
backend = "sqlite"

[[contribs]]
contrib = "logging"

[[contribs]]
contrib = "node-management"
[contribs.config]
version_check = false
```

### 0.2 BCS 配置与数据源连续升级

拆包后的 server／web 组合独立管理 BCS 配置与数据源。BCN 主配置启用 server／web 模块，
server 的 lifespan 创建独立的 ContribHost 和 ROOT；其配置载体、存储、schema、API 与业务模块均在该 ROOT 装配。
节点 ROOT 与服务端 ROOT 分别持有 CONFIG_STORE、STORAGE、DB 和业务实例，跨两者的通信使用公开服务接口。
复用的 bazaar-contrib-config／bazaar-contrib-storage 提供公共契约与实现代码，资源和服务实例由各自组合管理。

server 启动时通过自己的配置载体直接读取 BCS 文档。默认文件仍位于
`~/.bcs/config.toml`，SQLite 数据库为 `~/.bcs/bcs.sqlite3`，登录签名密钥为 `~/.bcs/session.key`。
旧 BCS 的配置结构与有效默认值继续由 server 的 Pydantic 模型直接解析；这些设置的读写归服务端配置载体，
BCN 的 V1→V5 版本链只处理节点主配置。server 的运行组合由自己的发行资源与 BCS 配置确定。

`bazaar-contrib-server-config-filesystem` 提供 server-config-filesystem，在服务端 ROOT 提供 CONFIG_STORE，
默认读写 ~/.bcs/config.toml，独立持有文件锁、快照、条件提交与备份。
服务端设置的请求边界使用原 BCS 配置模型校验，经服务端 CONFIG_STORE 条件提交到同一来源；读取已有文件保持原结构，来源不存在时生成原 BCS 默认配置。
server 模块的启动参数模型包含 config_provider 与 config_options，缺省分别为 server-config-filesystem 与空对象；
用户只在选择额外配置中心或原 BCS 自定义配置位置时填写它们。第三方服务端配置中心在该 ROOT 提供同一契约。
原 BCS 自定义配置路径由 server 的 config_options.path 保留；bcn run 的 --config 选择节点主配置。
旧 BCS 自定义配置位置只改变配置文件，数据目录始终为原运行用户的 ~/.bcs，启动与升级沿用该规则。

`bazaar-contrib-server-sqlite` 提供 server-sqlite，消费服务端自己的 CONFIG_STORE 与 STORAGE，
按原 BCS 的 storage 及后端参数创建连接，在服务端注册表登记 sqlite 后端；database 在该 ROOT 提供 DB。
默认 SQLite 仍打开原 ~/.bcs/bcs.sqlite3，连接和退出由服务端存储模块管理；
额外存储实现使用同一服务端注册表与 DB 契约，选择与参数保存在独立 BCS 配置中。
同进程运行 agent 和 server 时，各自的配置载体、数据库连接与迁移记录均绑定自己的 ROOT。

server 在其 ROOT 启用 server-api，后者接受 HTTP、管理两端私有协议入口并提供 API。
顶层 server 入口向节点 ROOT 提供 SERVER_HOST，类型为 ServiceKey[ContribHost]，定义在 bazaar-contrib-server；
顶层 web 入口接受 SERVER_HOST，通过同一 ContribHost.enable 异步上下文管理器装配服务端 GUI 和页面模块。
enable 接收已发现定义及其参数、校验包含已有服务的依赖图并启动新增实例，退出时撤销本次启用；
web 与 server 的依赖顺序保证页面先退出，随后业务、API、存储与配置载体退出。

| 0.2 BCS 配置或原资源 | 服务端直接读取与保留规则 |
| --- | --- |
| listen，缺省 127.0.0.1:8765 | server-api 使用原监听值，保持 host:port、IPv6 及原解析语义 |
| storage，缺省 sqlite；同名后端参数表 | 服务端存储组合使用原后端选择及参数，由所属后端实现解析 |
| 默认 SQLite 数据源 | server-sqlite 直接打开原 ~/.bcs/bcs.sqlite3，保持旧实现的数据库定位规则 |
| session_minutes，缺省 10 | access 从 BCS 配置读取，保持本地登录有效时长 |
| retention_days，缺省 30 | history 从 BCS 配置读取，保持原事件保留策略 |
| ~/.bcs/session.key | access 直接读取原密钥，保持签名和 cookie 语义，既有未过期登录继续校验 |
| accounts、roles、account_roles、auth_settings、oidc_providers、oidc_identities、relations | access 使用原账户、密码哈希、角色、权限、分享和 OIDC 设置；账户 language／theme 继续由页面读取 |
| computers、events、refs 等既有业务数据 | fleet／history／contacts 查询原表和标识，保持 computer 身份、secret_hash、事件顺序与关联 |

包拆分完成后，业务查询直接使用上述历史数据布局。后续 schema 变更由新增迁移执行，
既有主键、凭据、事件关联和权限在事务内保留。节点 SQLite 与 server-sqlite 分别持有原迁移资源，
schema 服务从各自原 schema_migrations 的历史身份识别
BCS 的 initial_server_schema 链，保留已发布 1…7 的文件内容、名称、SQL、checksum 算法与原 ledger 记录，
校验后只执行缺少的后续步骤；BCN 的 initial_node_schema 链独立识别。
新增模块迁移使用独立的 contrib_schema_migrations ledger，以发行包／迁移版本标识迁移并保持 checksum 校验；
启用混合能力时按所属模块注册缺少的 schema，历史链选择由已有 ledger 识别，新库按启用组合建立 schema。
所有 schema 登记检查表名及迁移身份冲突，沿新增迁移形成统一组合的数据布局。

升级验收从实际 0.2 发行环境生成隔离的 BCS 配置、数据库和密钥，再以 0.3 候选宿主与实际模块 wheel 启用 server／web，启动时直接读取这些原资源。
覆盖默认路径、原 BCS 自定义配置位置、有效缺省值及既有迁移版本，核对原数据库路径、历史 ledger 与业务身份，
验证旧账户登录、未过期 cookie、角色／分享权限、OIDC 配置、历史与 refs 查询，
并让原 0.2 节点使用原 token、地址和 `/node/reportEvents`、`/node/getUpdates` 协议继续上报与接收控制。
server 保留这些既有协议入口及请求／响应含义；0.3 两端的服务调用与结果使用该包私有 HTTP 协议。
原 0.2 节点的 control.result 按既有协议接收；0.3 client 直接通过私有协议回传调用结果。
升级后新事件仍写入原数据库，并能通过同一 web 页面查询；重复启动保持独立 BCS 配置、数据与迁移结果稳定。
同进程使用另一份隔离节点配置与数据库，分别修改节点和 BCS 设置并写入各自业务数据，核对两边配置文件、数据库路径与实际查询结果。
完整验收使用隔离环境的真实 V1／V2／V3／V4 文件，覆盖中文、英语、未设置 lang、已有第三方语言、
多 agent／provider、远端连接、凭据引用和关闭版本检查，并验证重复加载、备份与迁移后真实运行结果。

### 默认配置生成与后端切换

`bazaar-compute-node` 的 `bazaar_compute_node/defaults.toml` 提供初始节点组合。
app 构建从 BCN uv.lock 的 contrib/ 必要成员自动生成 package/source 条目，合并随 app 源码维护的 catalog-external.json 独立可选条目，按包名排序并复核唯一来源后形成官方索引；官方源使用 PyPI，未安装的包同样随索引发行。
初次创建配置时，经 CONFIG_STORE 将 V5 组合提交到所选来源，filesystem 默认写入 `config.toml`，包括 `languages`、`language` 和 `backend="en"`，
以及 `storage`、`sqlite`、`database` 三条启用配置，
并明确写入 `database.config.backend="sqlite"`、默认 agent 的 `config.agents=[]`。必要包通过宿主发行依赖安装，启动按保存的配置发现入口和建立服务依赖。
可选 server／web 包分别提供 `bazaar_contrib_server/defaults.toml`、`bazaar_contrib_web/defaults.toml` 节点启用条目，
用户选择后经节点配置管理服务校验并保存；节点已有存储／语言选择、参数及停用选择继续保留。
服务端自己的配置与业务组合由 server 的独立载体和发行资源管理，监听、登录及 storage 等 BCS 设置直接从原配置读取。
安装操作提交包依赖清单并展示入口和组合资源；用户选择 enable 时导入所选组合并保存启用配置，`add --enable` 顺序执行这两项操作。
后续启动读取用户保存的配置，升级通过配置版本迁移保留停用条目、参数和后端选择。
已有配置缺少 DB 选择入口或所选后端时，装配诊断列出缺失服务及消费方。

切换 PG 的完整流程为：

1. 从模块索引选中 PG 的发行包和包源，将其加入候选依赖组合并交给目标宿主 uv 安装。
2. 安装成功后，从发行元数据取得 PG 入口；用户在目标宿主顶层 contribs 启用，并填写连接参数。
3. 更新同一作用域已有 `database` 条目的 `config.backend` 为 PG 注册的后端名，保存配置。
4. 下次启动按 `storage → PG → database → 业务` 装配；业务仍使用 `ctx.accept(DB)`。
5. 用户选择保留 SQLite 启用条目时，两后端各持有自己的连接；移除 SQLite 条目后，其连接随旧实例退出关闭。

例如假设 `bazaar-contrib-pg` 的入口名和注册后端名均为 `pg`，保存后的存储配置如下：

```toml
# config.toml 的存储组合；SQLite 与 PG 同时启用，业务选择 PG。
[[contribs]]
contrib = "storage"

[[contribs]]
contrib = "sqlite"
[contribs.config]
path = "bcn.sqlite3"

[[contribs]]
contrib = "pg"
[contribs.config]
dsn = "postgresql://localhost/bcn"

[[contribs]]
contrib = "database"
[contribs.config]
backend = "pg"
```

`contrib="pg"` 对应发行包声明的模块入口，`backend="pg"` 对应该入口登记的存储后端名。
入口名和后端名分别由模块作者声明，package 名称、入口名、后端名之间的对应关系由元数据和服务声明建立。
database 的配置模型编辑 `backend` 字段；用户修改已有选择条目，管理服务更新原条目。
包安装操作提交发行依赖清单，启用和切换操作提交运行配置，管理页面分别提供这些操作。
所选连接上的模型、schema 和业务查询继续由对应功能模块管理。

### 模块索引与安装入口的关联

`contrib_indexes` 是宿主配置中的模块索引 URL 列表，首次配置带入官方索引，用户可以追加私有或其他维护者的索引。
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

`source` 是 uv 使用的 Python 包源 URL，与读取这份模块索引的 URL 分别保存。
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
包名和所选包源写入目标宿主的依赖清单，升级沿用该记录。索引 URL 被移除或暂时不可用时，
已安装模块仍依据保存的依赖清单和运行配置管理。
索引发布、缓存、详情展示与 web 操作流程见 [contrib 市场计划](bazaar.md)。

## 统一宿主与可选 server／web

启动入口统一为 `bcn run --config <path>`，模块统一配置在顶层 contribs，具体实例按组合展开与 scope 声明创建，默认 agent 的 config.agents 可以为空。
普通节点按 app 默认资源生成的配置运行；启用 bazaar-contrib-server 的 server 入口及其依赖后提供 API，
再启用 bazaar-contrib-web 的 web 入口后，在服务端 ROOT 启用 GUI 与业务页面。同一个进程可以同时运行 agent。

```toml
# bazaar-contrib-server 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
server = "bazaar_contrib_server:server"
server-api = "bazaar_contrib_server:api"
server-client = "bazaar_contrib_server:client"
```

```toml
# bazaar-contrib-web 的 pyproject.toml
[project.entry-points."bazaar_compute_node.contribs"]
web = "bazaar_contrib_web:web"
```

server 在自己的生命周期创建服务端 ContribHost，以同一 run 接口提供独立载体的自举输入和 BCS 配置加载器；
core 执行服务端 ROOT 的载体、存储与业务装配，server 在就绪后向节点提供 SERVER_HOST。
server-api 在服务端 ROOT 接受 HTTP 并提供 API，管理与节点 client 通信的私有 HTTP 入口；业务模块通过 API 登记自己的方法，访问模块提供授权服务。
web 接受 SERVER_HOST，在服务端 ROOT 启用 GUI 与页面；业务页面与渠道编辑器通过通用 GUI 契约登记。
server-client 在普通节点 ROOT 运行，持有 HTTP 会话、身份、请求关联、重连及事件上报队列；server 与 client 是同一发行包的两种运行形态。
业务通过 announce 发布公共审计事件，client 的 listen 处理器接收后入队上报，server 收到后按公共事件契约交给投影模块。
client 在 accepts 中声明所需服务，通过 Context.accept 获取其他插件 offer 的对象并调用方法；通知操作可以在节点内 announce。
server 的查询或下发请求经私有 HTTP 交给目标 client，执行结果与错误通过同一私有协议直接返回；审计记录由业务独立发布。
HTTP 输入模型、身份校验、超时和传输结果由 server 包管理，服务接口及业务规则仍归提供 offer 的功能包。
包的 pyproject.toml 安装组合代码，defaults.toml 提供可导入的配置；运行时按 accepts 建图。
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
API 的登记归属由服务端 ContribHost 管理，消费者退出后才退出 server-api 的 lifespan 并撤销服务。

分开部署时使用独立配置分别执行 bcn run，各进程拥有自己的 ROOT、数据库、socket、端口和运行目录。
混合组合与分进程组合都通过实际 HTTP 请求、agent turn、长连接和退出验证：一个服务等待期间另一个仍有进展，
所属资源随模块生命周期结束。bcn run 依据声明装配和管理模块，具体执行方式属于模块实现。

## 启动接入与首个日志模块

`bcn run --config <path>` 在 app 中解析启动输入，读取随包默认／自举资源并绑定配置加载器，
直接创建 ContribHost，进入 core 的统一 run 异步上下文管理器，宿主就绪后等待退出信号。
core 建立根作用域、发现入口、装配所选载体及发行版本组合，启用提供 `COMPOSITION` 的配置加载器后校验并装配业务依赖；
agent 创建、更新和停止都由宿主管理对应子作用域。启动完成后，健康状态显示已启用实例、所属作用域与运行状态。
外部控制入口开放前，需要的模块服务已经就绪；退出时先结束请求与 agent，再清理模块和共享资源。

日志模块通过 `listen` 声明公共审计事件处理器，在 lifespan 中按自身配置创建并登记日志输出服务。
日志定义声明 `scope=ROOT`，顶层启用一次；
普通节点、纯 server 与混合组合都创建一个根消费者，接收各子作用域上报的审计事件。
处理函数通过 `ctx.accept()` 取得该输出对象，输出现有已脱敏审计载荷。
宿主停止事件消费并等待回调后，lifespan 释放自己创建的输出 handler。多个事件消费者可并列启用。

审计事件发布及持久化语义归审计业务契约；通用框架只认识 typed event。
节点审计记录器通过 announce 发布事件，logging 与 server-client 可并列 listen 同一事件。
日志管理输出资源，client 管理上报和远端请求资源；查询和操作直接调用功能模块 offer 的服务。

日志包通过公共模块入口接入，公共框架和包发现代码均不写日志专用分支。
测试从独立包的 wheel 元数据发现入口，启动真实节点、产生审计事件、观察日志，再结束进程验证清理。

## 模块管理与自身 uv

`bcn contrib` 提供索引列表与增删、索引条目列表、安装、卸载、启用、停用、已安装列表和详情。
`bcn contrib add <name>` 接受官方短名或完整发行包名，短名通过官方索引中的 `bazaar-contrib-<name>` 条目确定包名与来源。
例如 `bcn contrib add codex --enable` 安装 bazaar-contrib-codex，并在安装确认后启用其默认运行组合。
包安装提交完整依赖清单，enable 将选定的模块入口及参数写入顶层 contribs；disable 从该启用列表移除对应条目并校验剩余依赖。
安装状态与启用状态独立记录，`--enable` 将安装和配置提交按顺序完成，结果分别显示实际安装版本与配置保存状态。
提供多入口的官方 provider 包携带 defaults.toml，启用组合包括该 provider 的 builder 与 channel／harness 运行定义；页面入口由对应 web 组合配置。
未提供组合资源的多入口包由管理入口展示发行元数据中的入口与配置模型，用户选择后提交配置。
模块通用参数由各入口的 Pydantic 模型校验并保存到对应 contribs.config；编辑更新所选已有条目，保留未修改的参数与其他组合选择。
agent 的连接／执行参数和凭据引用由 provider 实体模型校验，保存到 agent 条目 config.agents 中相应实体的 channel／harness 配置。
命令定义、提示与输出沿用现有 CLI 和 i18n。
索引读取与安装管理提供独立的 typed 服务契约，CLI 与后续 contrib 市场页面消费同一实现。
管理请求明确目标宿主、包规格和模块入口；已有配置与实例由管理列表提供选择目标及操作句柄。
实例句柄由宿主返回，管理界面和调用入口使用它完成操作。服务提供自动生成的操作 ID、进度与终态查询，
结果包含安装版本、配置状态和下次启动生效状态。web 页面及远端调用入口在后续阶段接入。

本地 `contrib add`、模块配置编辑和后续 `agent add` 在节点未运行时使用短生命周期管理组合。
app 调用 core 装配所选配置载体及版本服务，并按操作进入配置、语言、模块管理、所选 agent 管理与登记服务；首次管理操作经当前载体提交 app 默认配置，默认 agent 的 config.agents 为空。
配置编辑复用 CONFIGURATION 的 prepare／commit，导入包组合复用 import_defaults，以完整候选的模型及依赖校验和修订条件提交保存结果。
管理命令退出时释放本次资源；channel 连接、harness 进程与 agent 执行实例由后续 `bcn run` 按已保存实体配置创建。
首次使用流程见 [总计划](README.md#全新安装与默认运行组合)，本分支用真实示例包完成默认基础组合、安装及 agent 配置管理，完整 Codex／Telegram 流程随具体 provider 阶段交付。

依据 `src/bazaar_compute_node/app/upgrade.py` 的 uv 定位和执行行为，在 `bazaar-contrib-manager` 实现新组合共用的宿主升级及包管理能力。
uv 由宿主进程的 PATH 解析为具体执行文件；目标环境明确对应运行该节点的 Python。
实际执行使用参数数组，安装结果、发行清单和启用配置之间有明确提交顺序。

宿主保存系统维护的额外模块依赖清单，每项记录发行包名、所选包源和用户指定的版本范围，
操作成功后记录实际安装版本及入口。默认官方包的组合依赖由宿主版本确定，运行配置通过当前 CONFIG_STORE 保存，filesystem 默认为 `config.toml`。
app 的共同发行清单标识 BCN 必要包；官方安装索引合并必要包与独立可选包的包名及来源。
添加模块保持应用版本，独立可选包按公共接口兼容范围及用户要求解析；整体升级使用目标 app 与保存的全部额外声明，保留实际来源和显式固定版本。
必要包随 BCN 整体发行，可选与第三方升级更新所属包的版本要求；
卸载从候选额外依赖集合删除该包，并校验依赖它的包及配置条目，以及自举选择是否仍引用该载体入口。
后续操作从保存的清单生成候选输入，安装源随包记录保留。

完整组合的版本与来源解析按以下步骤执行：

1. 在目标宿主的操作临时目录生成 `pyproject.toml`，`project.dependencies` 包含固定宿主版本及全部额外包规格。
2. 将每个额外包的 `source` 转为具名 `tool.uv.index`，由 `tool.uv.sources` 将包绑定到所选 index。
   index 名由管理服务内部生成；用户填写的仍是包名、源 URL 和可选版本范围。
   同源条目共用 index，依赖解析按保存的来源顺序及目标宿主默认包源使用 uv 的 `first-index` 策略；
   直接声明的额外包通过 sources 固定来源，其传递依赖由 uv 从候选包源解析。
3. 由自身 uv 执行 `uv pip compile <candidate>/pyproject.toml --python <target-python> --format pylock.toml --output-file <candidate>/pylock.toml`。
   此步骤解析整个候选依赖图，产物保存选定版本、下载地址与校验信息。来源或版本冲突在改变安装环境前报告。
4. 安装阶段消费同一份解析产物，完成后用目标解释器检查实际包、入口和 API 兼容信息。
5. 成功后提交新的额外依赖清单与解析结果；启用请求另外校验并保存运行配置，结果显示下次启动生效。
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

- **uv tool 部署**：将固定宿主版本与完整模块集合一起安装，使用
  `uv tool install bazaar-compute-node==<version> --with-requirements <candidate>/pylock.toml`。
  `--with-requirements` 承载完整的附加依赖与已解析产物；官方内置包由固定宿主版本绑定的组合依赖提供。
  现有监督进程负责下一次启动；Windows 沿用当前升级保留运行解释器的安装方式。
- **自管 venv 部署**：同一 uv 使用 `uv pip sync --python <target-python> <candidate>/pylock.toml`，
  目标是由宿主管理完整依赖集合的专用 venv，安装和卸载均按候选完整组合同步。
- 安装、卸载和节点升级共用宿主环境变更锁，避免并发包操作覆盖依赖组合。
- 保存用户给定的包版本、来源规格和模块实例配置；uv 负责发行依赖解析，运行时 `accepts` 负责服务装配。
- 整体升级保留后端的额外包依赖、`database.config.backend`、连接参数、其他启用实例及停用选择。

tool 部署使用普通 `uv tool install`，保留 Windows 正在运行的解释器。
宿主安装与重启遵循 [服务监督计划](../2026-10-07-service-supervisor.md) 的生命周期要求。

## 本特性分支的串行 Tasks

每个 Task 完成后停下 review，通过后继续下一个 Task。本分支在十四个 Task 全部完成后合入 `0.3`。

### Task 1：核心 contrib 框架

- [x] 将新包与开发、CI 解释器升级到 Python 3.15，使用任务组管理任务归属，以 pytest-asyncio 验证真实资源组合。
- [x] 建立仓库根 uv workspace 与 core/ 的 bazaar-core 独立发行结构，在 bazaar_core 导出通用公共 API。
- [x] 在 `core/src/bazaar_core/bazaar.py` 定义通用 ROOT 标识、接受应用 Enum 的作用域契约、typed keys、含 `scope / offers / accepts / lifespan / listen` 的模块定义、配置实例及版本契约。
- [x] 在加载边界校验 scope 枚举及应用声明的根类别、父子关系和 provider_kinds；默认 ROOT 的公共插件使用宿主实际根节点，按声明与运行实体建立自动实例化及归属关系。
- [x] ContribHost.enable 以异步上下文管理器校验并装配新增定义，复用当前宿主的已有服务并绑定本次实例归属；独立 ContribHost 分别持有 ROOT 与服务表，组合模块负责其内部宿主的退出。
- [x] 按条目与适用作用域创建独立运行实例，由宿主自动分配内部标识并返回生命周期句柄。
- [x] 实现服务作用域、依赖装配和实例绑定的 Context，注入已声明依赖，提供 `offer / accept / announce`、只读作用域与状态记录。
- [x] 支持静态 `accepts` 与同步配置解析函数，先校验配置、保存实例依赖集合，再建立完整服务图。
- [x] 实现异步上下文管理器的进入、服务就绪检查和按依赖逆序退出。
- [x] 实现 typed 事件与 `listen` 登记、启动后启用、停用及进行中回调等待。
- [x] 完成 lifespan 资源清理、启动中断回收及初始化和运行故障的实例隔离，以真实资源验证传递依赖、跨启用句柄、独立实例和并发故障的清理。
- [x] 由宿主记录实例状态与故障原因并 announce 状态变更，验证普通插件通过 listen 接收故障状态及原因；运行状态与插件自有健康 probe 分开。
- [x] 在 core 提供 ContribHost.run 异步上下文管理器，以自举条目和提供 `COMPOSITION` 的普通加载器组织分阶段启动、就绪、业务退出与自举资源回收；加载器声明依赖并通过 `ctx.validate()` 校验候选。
- [x] 使用真实模块组合和临时资源验证乱序配置、服务使用、子作用域、同一模块多次启用与分别释放。
- [x] 使用真实组合验证 ROOT 共享服务、独立 ContribHost 对同一 key 的各自供给与退出、AGENT 定义启用一次产生独立实例、实体新增和停止的自动实例化与清理。
- [x] 使用独立函数声明的注册表、后端、选择层与 recorder 模块验证依赖解析、服务实例共享及省略 lifespan 的事件消费者。
- [x] 以多个真实资源提供者和按配置选取的消费者验证依赖计算、多实现并存、停止顺序及缺失和重复 key 的装配诊断。
- [x] 验证并列消费者、后台服务就绪后 yield、一个服务等待时另一服务仍有进展、回调及后台任务结束后资源退出和部分启动失败回收。
- [x] 完成按 workspace 成员执行的 Ruff 和 Pyright/LSP 检查，提交供 review。

### Task 2：独立状态查询模块

- [x] 建立 `contrib/status/` 的 `bazaar-contrib-status` 独立 workspace 成员、公共协议、key 与类型标记，正式依赖为同版 core。
- [x] 以普通 ROOT contrib 的 lifespan offer `CONTRIB_STATUS`，`snapshot()` 读取 `ctx.snapshots` 的不可变状态记录，保留查询时的数据与故障来源。
- [x] 在统一 entry point group 登记 `status`，验证真实发行元数据发现、普通启用、消费者 accept 与 core 状态事件订阅。
- [x] 使用真实文件与任务组故障验证当前快照、故障和依赖阻塞记录、既有快照保持及资源清理；验证子作用域共享查询服务与独立宿主的各自状态。
- [x] 完成独立 wheel／sdist 安装与入口调用、包内测试、Ruff 和 Pyright/LSP 检查，单独提交供 review。

### Task 3：发现、配置与节点启动

- [ ] 建立 app/ 的 bazaar-compute-node 与 contrib/ 下的配置契约、filesystem、版本组合、审计、日志和管理成员，将已交付的 status 包加入 app 默认依赖与 defaults.toml；根目录转为共享开发配置与 uv workspace，旧 src 与旧测试保持原样，共享根 uv.lock；sources 绑定成员，editable 元数据用于入口发现，独立 wheel 用于交付安装验收。
- [ ] app 直接声明同版 core 与默认 contrib 依赖，持有普通 Python 命令入口、启动输入、退出信号、defaults.toml 及版本自举资源，直接进入 core 的统一运行接口；验证 core 通过普通 import 使用、具体功能模块由 entry point 加载、core 的正式依赖图保持通用。
- [ ] 在 core 接入统一发行元数据与 entry point 发现、Pydantic 配置加载，保存发行包、版本、入口名和导入对象的对应关系，入口名用于配置与诊断；逐条处理入口导入和配置错误，报告对应位置并阻断缺失服务的依赖者，使有效独立条目继续装配，加载诊断接入核心状态记录与变更事件，由 status 插件统一提供查询。
- [ ] 业务模块启用与参数统一读写顶层 contribs，scope 由模块定义声明；运行实体与 builder 决定具体实例，内部标识由宿主生成。
- [ ] 交付 bazaar-contrib-config 的 ConfigStore／ConfigSnapshot／CONFIG_STORE、ConfigConverter／ConfigCandidate／CONFIG_V1…CONFIG_V5、条件提交与错误契约，以及独立 bazaar-contrib-config-filesystem wheel、config-filesystem 入口与平台文件处理。
- [ ] 实现 --config-provider／--config-options 的入口及参数选择、缺省值与 --config 路径简写，完成已安装入口的能力／scope／API 校验、Pydantic 自举参数和实际服务就绪校验。
- [ ] 由 core 的 ContribHost.run 先装配启动输入选定的配置载体与发行固定的版本组合，再启用声明 `CONFIG_STORE`、`CONFIG_V5` 的配置加载器读取、转换、校验并提交文档，通过 `COMPOSITION` 装配业务；CLI、管理、组合导入、升级预检与监督进程使用同一载体和自举选择，业务及在途配置操作结束后退出版本组合与载体。
- [ ] 交付 bazaar-contrib-config-versions 的五个独立入口和按 CONFIG_V5→CONFIG_V4→CONFIG_V3→CONFIG_V2→CONFIG_V1 声明的依赖链，发行组合在业务配置读取前直接装配，使用真实 wheel 校验乱序发现、服务调用与逆序退出。
- [ ] 定义 version="5" 模型与保存格式，由版本服务保留原 V1→V2→V3→V4 转换并追加 V4→V5，交付候选配置、字段映射摘要与迁移所需包清单，验证本版直返与仅执行需要的历史步骤。
- [ ] 通过 CONFIG_STORE 条件提交候选，filesystem 实现原始字节复核、锁、备份和原子保存；修订冲突后重新生成和校验候选，各能力阶段同步所属字段及 provider 的明确映射。
- [ ] 以实际历史配置验证 agent／provider 参数、凭据引用、数据位置与缺省值保留，后续阶段加入 lang、DB、远端与管理能力的真实迁移验收。
- [ ] 支持默认组合在所选来源生成首次 V5 配置、从已安装 wheel 的 defaults.toml 资源显式导入候选组合，后续加载保存配置；CLI／页面与手写文件使用同一模型，复用已有条目并保留用户参数、后端及停用选择。
- [ ] 使用真实独立载体 wheel 与可读写的配置中心服务，在没有本地 config.toml 的隔离环境完成启动、读取、编辑、提交、退出和部署重建；多客户端写入验证修订冲突、原配置保留和重新校验，真实连接中断验证提交未知后的读取核对，实际 entry 验证入口与服务诊断。
- [ ] 接入统一应用入口与实体生命周期，AGENT 定义随 agent 自动创建和清理；空 agent 时保持启用定义、运行实例数为零。
- [ ] 从真实发行元数据加载模块，使用空 agent 和带 agent 的 ROOT 组合、临时配置、数据库、socket 与隔离节点验证 app 的普通入口调用 core 的 ContribHost.run、分阶段启动、加载中断和完整退出生命周期。
- [ ] 完成包内新测试、Ruff、Pyright/LSP 检查，提交供 review。

### Task 4：首个日志模块

- [ ] 定义公共审计事件契约并接入新 app 组合的审计发布流程。
- [ ] 使用 Contrib(scope=ROOT, listen=..., lifespan=...) 实现共享日志，验证各 agent 事件消费、输出服务、handler 所有权和退出清理。
- [ ] 完成 `bazaar-contrib-audit` 和 `bazaar-contrib-logging` 的独立发行包，`logging` entry point 用构建 wheel 证明安装发现与事件消费。
- [ ] 验证实际节点事件输出、并列事件消费者和停止后的资源回收。
- [ ] 完成包内新测试、Ruff、Pyright/LSP 检查和包构建，提交供 review。

### Task 5：模块安装管理与升级

- [ ] 在 `bazaar-contrib-manager` 提供 `contrib-manager` 入口，复用自身 uv 与节点升级的执行机制，维护完整发行依赖清单和环境操作锁。
- [ ] 提供共用的模块索引与管理契约，记录操作进度、结果和下次启动生效状态。
- [ ] app 构建将 BCN workspace 的必要包与 catalog-external.json 的独立可选条目合并为官方 package/source 索引，独立 wheel／sdist 验证可选包在未安装时可见；管理服务消费该清单；实现 `contrib_indexes` 默认值、追加、移除、读取与缓存。
- [ ] 完成 `bcn contrib` 索引与条目管理、安装、卸载、启停、列表和详情；安装后从发行元数据提供入口选择及配置模型。
- [ ] 实现官方短名 add、`--enable` 安装后导入组合和模块通用配置编辑，分别记录安装与启用结果，保留已有参数和选择。
- [ ] 在节点未运行及配置尚未创建的隔离环境，用真实多入口示例 wheel 验证首次 add、enable、编辑、disable、保存和后续启动，复用配置自举与短生命周期管理组合。
- [ ] 由完整依赖清单生成绑定来源的候选输入，使用自身 uv 解析出 pylock，并在 tool／专用 venv 安装时消费同一解析产物。
- [ ] 官方组合与额外模块依赖贯穿一次宿主升级，安装与升级使用相同声明和目标环境；候选解析冲突保留当前组合，配置中的 provider 替换和停用选择在升级后保持。
- [ ] 宿主升级预检保留配置载体及自举输入，预先读取配置迁移所需包，连同全部依赖与保存来源解析；目标环境完成候选校验后提交 V5，语言阶段用真实中文包验证自动纳入依赖及 lang 选择。
- [ ] 以独立临时 uv tool 环境和 venv 验证 wheel 安装、发现、启用、卸载和升级保留。
- [ ] 从真实官方与额外模块索引读取包名和来源，验证多入口 wheel、同名不同源的选择、私有源安装与升级来源保留。
- [ ] 使用真实包管理器完成验收，执行相关检查，提交供 review。

### Task 6：公共语言服务与默认英语

- [ ] 交付 bazaar-contrib-language 的 Translator、语言后端注册及选择契约，提供 languages、language 两个 ROOT 入口与随包英语实现。
- [ ] 交付 bazaar-contrib-node-text 的节点文案 key、模板参数和英语资源，由 CLI／节点调用者导入；翻译后端依赖同一 key。
- [ ] 按 [语言服务与后端](roadmap.md#语言服务与后端) 实现命名空间、模板参数、所选后端及逐条英语回退，资源登记随生命周期释放。
- [ ] app 默认依赖上述包，初始组合明确 language.config.backend="en"；CLI 读取配置后使用共享 LANGUAGE，基础配置诊断使用节点英语资源。
- [ ] 使用真实资源与独立 wheel 验证翻译、参数和资源退出，完成包内检查及根目录 LSP，提交供 review。

### Task 7：调用与 channel／harness 公共登记

- [ ] 交付 bazaar-contrib-invoke 的 commands、local-rpc 入口、命令／RPC 登记契约、Pydantic 输入边界及本地 socket／pipe 传输，业务 handler 由功能模块登记。
- [ ] 按 [channel 与 harness 公共接口](roadmap.md#channel-与-harness-公共接口) 交付两个独立公共接口包、Pydantic 输入／输出／审批模型及 CHANNEL_BUILDERS／HARNESS_BUILDERS；具体 provider 依赖接口包，默认 agent 通过同一接口调用。
- [ ] 提供 channel-builders、harness-builders 两个 ROOT 注册入口，builder 登记 provider 实体模型、运行入口和能力；将新装配层的 ScopeKind.RUNTIME 及 runtime 命名统一改为 HARNESS／harness。
- [ ] 节点未运行时，CLI 使用本次管理组合直接调用登记的功能服务；运行后的本地 RPC 按同一登记表分发并绑定身份和目标。
- [ ] 将已交付模块管理命令接入登记及 LANGUAGE，app 默认组合启用调用和两类 builder 注册服务。
- [ ] 用真实独立示例 wheel、配置模型和本地传输验证登记、调用与资源退出，完成独立安装及检查，提交供 review。

### Task 8：公共存储、SQLite 与 schema

- [ ] 交付 bazaar-contrib-storage 的通用数据、显式查询、事务及错误契约，以及 storage 注册表和 database 选择入口；依赖按所选 backend 展开。
- [ ] 交付 bazaar-contrib-sqlite，提供 sqlite 后端及真实连接、公共操作翻译；默认配置明确 database.config.backend="sqlite"。
- [ ] 交付 bazaar-contrib-schema 的模型／索引登记、迁移顺序和 ledger；按 [数据与查询](roadmap.md#数据与查询) 保留已发布迁移的原文及 checksum，新增模块迁移使用独立账本。
- [ ] app 默认依赖并启用 storage、sqlite、database、schema；用隔离数据库验证实际查询、事务、迁移及后端选择。
- [ ] 完成各包独立 wheel 安装、真实组合及检查，提交供 review。

### Task 9：消息与会话

- [ ] 交付 bazaar-contrib-messages 的消息、会话 target、历史、未读 cursor、收发与回执、附件、草稿、搜索和 thread follow 契约及业务服务。
- [ ] 模块通过 DB 自持模型、schema 与显式查询，使用 schema 和调用登记入口，共享服务供 CLI／RPC 消费。
- [ ] app 默认安装并启用 messages；通过真实隔离数据和文件验证消息、历史、搜索、草稿及附件流程和资源退出。
- [ ] 完成独立包安装与检查，提交供 review。

### Task 10：身份与协作

- [ ] 交付 bazaar-contrib-collaboration 的 sender／contact 身份、refs、会话登记、审批、消息引用和关联服务。
- [ ] 模块依赖消息、DB 与调用契约，自持业务规则和 schema，状态变化及读取沿用原协作语义。
- [ ] app 默认安装并启用 collaboration；通过真实隔离数据验证身份、会话、引用和审批流程，完成独立安装及检查，提交供 review。

### Task 11：定时器与 Reminder

- [ ] 交付 bazaar-contrib-timer 的 TIMER、句柄和到期／取消／关闭契约，以及 bazaar-contrib-timer-wheel 的驱动和资源生命周期。
- [ ] 交付 bazaar-contrib-reminder 的持久化、到期、snooze、更新、取消及唤醒规则，依赖 DB、schema、timer 和消息。
- [ ] app 默认安装相关包，启用 timer-wheel 和 reminder；消费者通过 TIMER 管理自身句柄，按 [定时器契约与时间轮实现](roadmap.md#定时器契约与时间轮实现) 处理退出与驱动故障。
- [ ] 以真实定时器和隔离数据库验证到期、更新、取消、持久化及清理，完成独立安装与检查，提交供 review。

### Task 12：默认 BCN agent 与替换契约

- [ ] 交付 bazaar-contrib-agent 的 turn、会话绑定、inbox、调度、输出交付、审批及任务故障服务，依赖语言、消息、协作、Reminder、timer、builder 与审计。
- [ ] 将现有 developer instruction 资源及渲染、bcc 工具环境与身份绑定、上下文及通讯编排归入默认 agent；按 [默认 agent 行为与替换](roadmap.md#默认-agent-行为与替换) 由 agent 选择提示词、工具及所需公共服务。
- [ ] agent 消费 channel／harness 公共包的输入、请求、输出／审批和生命周期契约，harness provider 执行 agent 提供的指令和工具环境；具体 provider 自身持有 SDK 和资源。
- [ ] 在 core 交付通用 compose(config) -> Composition 的资源启动前展开、候选校验、循环诊断、配置来源及 Enablement 归属，以真实组合验证内部定义与实体按依赖装配和回收。
- [ ] 公开 agent 入口声明 ROOT，拥有 config.agents 及 channel／harness 参数模型；compose 展开内部 AGENT 定义和所属实体，ROOT lifespan 经公共 builder 服务登记输入，内部执行任务等待 provider 实例就绪。
- [ ] 将新 ConfigDocument 的顶层 agent 模型及 app build 中的实体解释移至 agent 插件，同步 V4→V5 的 agent／runtime 字段映射；app defaults.toml 选择 agent ROOT 条目并明确 config.agents=[]。
- [ ] 用真实自定义 ROOT agent 组合替换默认入口，验证其配置模型、实体装配、指令、工具环境、通讯行为、所选依赖与资源退出。
- [ ] 使用 TestChannel 控制面及真实隔离资源验证消息注入、调度、输出、审批、故障和退出；具体外部 provider 的 e2e 由其交付阶段使用相同控制面验证。
- [ ] config.agents 为空时保持 AGENT／CHANNEL／HARNESS 运行实例数为零，真实实体组合验证服务可见范围和所属资源回收，完成独立安装与检查，提交供 review。

### Task 13：运行节点前的 agent 配置管理

- [ ] 在 bazaar-contrib-agent 的 ROOT 入口提供自身配置管理服务并登记 `bcn agent add`、列表、编辑、删除命令，按当前载体读写配置。
- [ ] 命令读取已启用 builder 的 provider 选择和实体模型，填写 agent 信息、channel／harness 参数与凭据引用，更新自身 contrib 条目的 config.agents；模型边界校验及组合展开后条件提交完整候选。
- [ ] 节点未运行时通过短生命周期管理组合完成首次默认配置创建和 agent 管理，模块选择与业务参数统一保存在相应 contribs.config，默认 agent 的实体参数由其 config.agents 持有。
- [ ] 管理模式启用配置、命令及所需 ROOT 登记服务，完整业务候选只执行资源启动前校验；后续 bcn run 才创建 AGENT／CHANNEL／HARNESS 实例。
- [ ] 自定义 agent 可登记自己的模型与管理命令；通用 app 命令分发消费登记结果，配置保存复用 CONFIGURATION。node-management 持有节点与平台管理职责，按默认节点清单接入其基础入口并在后续平台阶段补齐。
- [ ] 在隔离 tool／venv 和真实示例包中完成安装、add --enable、agent add、读取保存配置、bcn run 及退出流程，完成独立安装与检查，提交供 review。

### Task 14：首阶段完整验收与合入

- [ ] 从源码 workspace 完成未发布模块的联调、入口／依赖变更后的 lock／sync 与实际宿主重启，再从逐包构建的独立 wheel 在 workspace 外完成同一流程。
- [ ] 从干净环境完成“安装 BCN → 安装日志模块 → 启用 → 启动 → 事件输出 → 退出 → 升级再启动”。
- [ ] 验证全部默认基础包的正式依赖与初始启用组合，空 agent 时的节点管理及真实示例 provider 的 add --enable／agent add／run 流程；具体 Codex／Telegram 的真实首次使用流程随其交付阶段及最终发行复核。
- [ ] 补齐开发模块、服务契约、配置和安装文档，给出可实际执行的最小模块示例。
- [ ] 运行各成员的新测试与 Ruff、根目录 `uv run --quiet scripts/pyright_lsp_check.py .` 的逐包检查及包构建。
- [ ] Linux、macOS、Windows 独立 CI 完成安装与生命周期验收；native service 验收在隔离 CI runner 执行。
- [ ] 登记本仓库 app、core 与首阶段 contrib 的发行清单、共同版本和正式依赖；验证入口组、contribs 配置、contrib_indexes 与 bcn contrib 命令采用统一命名。
- [ ] 提交到首个特性分支，review 通过后合入 `0.3`，以该集成点开始路线图下一分支。

## 首阶段验收结果要求

新写的独立模块包可以声明新服务、消费现有服务、订阅业务事件，并通过配置与统一入口启用。
用户统一在节点主配置的顶层 contribs 启用入口和填写参数，模块通过 scope 的应用 Enum 成员声明实例归属，默认 ROOT；应用向宿主声明类别、父子关系和 provider 允许范围。
ROOT 定义创建共享实例，AGENT 定义随各 agent 自动创建和清理；provider 由 builder 匹配实体，实例依赖就绪后进入生命周期。
模块以独立函数声明 lifespan 与固定事件处理器，运行时操作通过显式 Context 参数完成。
lifespan 到达 yield 后服务与监听器就绪，回调结束后退出生命周期并撤销服务。
乱序配置仍按依赖装配；不同 agent 的服务和事件范围明确；模块资源随所属作用域结束。
按配置计算的依赖在启动前展开，所选后端就绪后才启动 DB 选择层，缺失及冲突诊断定位到具体配置。
同一模块多次启用时，各实例的配置与资源独立，宿主返回的句柄可以分别释放所属实例。
用户只选择模块入口与参数，宿主按模块类型和已有实体创建上下文及内部运行标识；配置列表与派生实例分别展示。
`bazaar-contrib-logging` 从独立 wheel 加载，实际节点输出正确；包安装、启用和节点升级共用 `bazaar-contrib-manager` 的 uv 与依赖组合。
官方与额外索引条目经包名和来源交给 uv，已安装发行元数据将包关联到入口，CLI 与页面保存相同的运行配置。
首次配置生成、保存后加载、更新已有选择条目和完整组合升级均保留用户参数与后端选择。
ConfigStore 来自启动输入选中的已安装载体入口，默认 filesystem，配置中心组合可在无本地 config.toml 的环境读写配置并在部署重建后恢复。
新建配置使用 V5；历史版本经发行自举的五版本模块链产生候选，所需包安装及配置依赖校验后经载体保留原内容、条件提交；修订冲突后重新校验。
语言等能力的参数随各阶段映射到所属模块；最终组合从真实历史配置验证 lang、provider、凭据引用和用户选择保留。
app 的普通 Python 入口提供启动输入和默认资源，core 的运行接口完成发现、自举装配、就绪与完整退出。
配置加载使用配置包公共逻辑与 core 的通用候选校验接口，具体读写和转换由载体／版本模块提供。
公共宿主机制保持业务无关，新功能契约由功能包定义。

所有测试使用测试专用配置、临时数据和隔离进程。真实外部 provider 使用现有 TestChannel 控制面；
三平台 native service 测试使用独立 CI runner。测试观察有效业务流程与资源行为，使用真实实现和构建产物。
