# BCN 0.3 模块工程

## 版本目标

BCN 的业务能力、平台 provider、存储查询、控制入口和界面扩展都通过公共模块范式装配。
所有组合使用同一个宿主和 `bcn run`：`bazaar-contrib-server` 提供服务端组合，`bazaar-contrib-starlink` 提供节点连接，`bazaar-contrib-web` 依赖 server 提供页面。
应用入口、核心框架及默认运行必需的 contrib 留在 BCN 仓库，各包分别构建 wheel，以同一版本发行，首版为 `0.3.0`。
具体 provider、server／web 及服务端迁移等官方可选 contrib 在 open-bazaar 的独立仓库维护，声明对 core 与公共接口包的兼容范围，独立构建和发行。
`open-bazaar/contrib-server` 中的 `bazaar-contrib-server` 是同一个插件包，持有 `server-migrations` 及按阶段交付的 `server`／`server-api` 入口。
从 `0.3` 开始，Bazaar Compute Node 及本仓库官方发行包使用 Apache License 2.0，各包携带许可证全文与 `Apache-2.0` 发行元数据。
BCN 源码仓库只维护根目录 `LICENSE`，官方包的 `LICENSE` 使用相对符号链接指向它，包元数据统一声明 `license-files = ["LICENSE"]`。
独立 contrib 仓库各自携带许可证文件及发行元数据。uv_build 在 wheel 和 sdist 中保存许可证正文，sdist 内为普通文件，独立构建与安装可直接读取。
`license-files` 的路径规范禁止父目录 `..`，因此共享源文件通过链接实现。
Windows 源码开发需启用符号链接权限及 Git 的 `core.symlinks`；Windows CI 在 checkout 前启用该设置。
用户可以发现并选择组合；功能的新增与替换由功能包完成，第三方 contrib 使用自己的仓库与版本。
contrib 表示可独立分发和装配的功能模块，官方默认、官方可选及第三方模块均使用这一称呼。

`0.3` 的 BCN 主配置使用 `version="5"`，按新的模块契约替换内部实现。
配置加载通过五个版本模块的依赖链将 V1／V2／V3／V4 转为 V5，把能力选择及参数映射到顶层 contribs，保留 agent、provider 和凭据引用。
配置读写由 `bazaar-contrib-config` 的 CONFIG_STORE 契约与载体模块提供，默认 `bazaar-contrib-config-filesystem` 读写 config.toml；
自定义配置中心模块提供相同服务，载体入口和连接参数由命令行 flags 自举，完整业务配置由所选载体读取。
bazaar-contrib-config-versions 提供 config-v1 至 config-v5 和 VERSIONS 装配清单，配置契约以 CONFIG_CURRENT 指定目标服务，在读取业务文档前装配其依赖；
配置包的普通加载器调用目标版本服务，经 core 的通用候选校验后组织提交；迁移先形成候选配置与所需包清单，安装并校验完整组合后经载体保留旧内容、条件提交。
filesystem 实现锁和原子替换，远端载体实现修订检查与条件提交；后续启动直接使用 V5。
具体启动和读写规则见 [配置载体与启动自举](framework.md#配置载体与启动自举)，版本服务定义见 [配置版本模块](framework.md#配置版本模块)。
开发阶段在 app／core／contrib 的独立成员中实现新组合，旧 src 与旧测试保留原样。
全部新包齐备且整体验收通过后统一切换入口，一次清理旧 src、旧测试、旧依赖与装配。
各功能模块依赖 `bazaar-contrib-storage` 的公共 `DB`，自己持有数据模型、schema、显式查询和业务规则；
数据库 provider 实现公共数据操作契约并翻译后端查询。
共享业务能力通过其拥有者的服务契约调用。core 是框架运行内核，执行自举阶段、入口发现、通用候选校验、装配、本地绑定与生命周期；
配置包负责文档读写、版本转换与条件提交，app 提供启动输入及发行资源。

Bazaar Compute Node 的 contrib 框架实现在 `core/src/bazaar_core/bazaar.py`，由 `bazaar_core` 导出公共类型，提供模块宿主、typed 服务和事件、本地绑定、依赖与生命周期。
Contrib 通过 `offers / accepts` 声明服务，`accepts` 可以依据已校验配置计算依赖，
通过 `lifespan / listen` 登记生命周期与固定事件处理函数。
节点模块统一在 BCN 主配置的顶层 contribs 启用并填写参数。组合根据已校验输入明确构造具体实例，默认服务绑定共享；`ctx.enable(..., local=(...))` 为指定服务和事件建立私有绑定并管理子资源。
函数显式接收本实例的 Context，声明依赖由框架注入，通过 offer／accept／announce 使用服务与事件；id、name 和快照提供实际实例身份及状态。
异步上下文管理器负责创建和清理资源，依赖图决定启动、故障传播和退出顺序，子组合随创建实例结束。
服务接口、数据模型、查询、配置、命令、RPC 和 UI 登记协议由功能包拥有。
`bazaar-contrib-server` 与 `bazaar-contrib-web` 是独立安装的可选包。server 在同一 Hub 的本地组合读取 ~/.bcs 配置，管理自己的 CONFIG_STORE、STORAGE、DB、API 与业务服务；公共审计可共享。
一个宿主可以同时运行 server 和 agent，各具体实例管理自己的后台任务或子进程；分开部署使用独立配置执行 bcn run，经公开 HTTP 协议通信。
starlink 默认停用，配置目标 server 后启用；其 getUpdate／send 循环接收请求，通过 accept 调用节点功能服务，按 listen 订阅业务审计并上报。
数据投影与访问策略由普通模块消费事件和调用服务，完整已知配置在资源启动前生成并校验依赖图。动态子组合在启用时校验。
用户选择模块与参数；默认 agent 的实体列表、channel／harness 选择及参数保存在 `contrib="agent"` 条目的 `contribs.config`，由 agent 插件的模型校验并展开实体。模块声明和实例状态分别管理。
存储组合由公共后端注册表、具体后端及 DB 选择入口构成，默认配置明确选中 SQLite，
用户可以启用 PG 等其他后端并更新数据库选择，业务继续通过公共 DB 使用显式数据操作。
当前先交付 COMMANDS 与 invoke 自持的命令入口扩展：功能包声明命令归属、在 lifespan 登记 Click 命令并持有文案。
boot／load 后按入口选插件根，core 按绑定选择依赖；app 的 run 同样是命令插件，由其回调启用完整组合并等待退出。
跨进程调用由 sidecar 接口包提供 SERVER／CLIENT 契约与接受 SERVER 的 ctx handler 入口；
Unix 和 Windows 实现包各自提供两个服务，消费方只依赖接口包并接受 CLIENT，自行组合远端公开调用。
app 通过平台 marker 声明默认发行依赖与启用条目，配置服务在创建默认文档时选定当前平台实现。
sidecar 承接 0.2 的 bcn.sock 本机通信，节点默认启用；V4→V5 无条件加入 sidecar 和本机实现，与 node.control 的选择无关。
功能包同时持有自身的通知与页面文案，默认使用英语。
web 组合提供 contrib 市场页面，读取随 app 发布的官方本地索引，以及用户追加的本地或 HTTP(S) 索引，按条目的包名和 Python 包源交给目标宿主 uv 安装。
包的发行元数据将发行包关联到模块入口，用户选择入口和参数后经该宿主的当前 CONFIG_STORE 保存。
页面分别展示包安装、配置与实际运行状态，以及索引、包源和安装目标。

## 全新安装与默认运行组合

`0.3` 完整发行的首次使用流程为：

```sh
uv tool install bazaar-compute-node
bcn contrib add codex --enable
bcn contrib add telegram --enable
bcn agent add ...
bcn run
```

基础安装提供并默认启用配置、状态与日志、模块管理、调用、存储、消息、协作、定时、Reminder、agent 编排及节点管理所需的公共服务。
初始配置中默认 agent 插件的 `config.agents` 为空；各服务的默认入口见 [全新安装的基础服务](roadmap.md#全新安装的基础服务)。
Codex、Claude Code、Telegram、Lark、WeCom 是用户选择安装的具体 provider；它们通过自身组合资源启用 builder 和运行定义，随后由 agent 配置选择实际实例。
`codex`、`telegram` 等官方短名对应索引中的 `bazaar-contrib-codex`、`bazaar-contrib-telegram` 发行包及包源。
安装提交包依赖清单，enable 经校验后写入顶层 contribs；`add --enable` 顺序完成两项操作，并分别报告安装和配置结果。
默认 agent 是可替换的 BCN 行为实现，负责 developer instruction、bcc 工具环境、上下文及通讯编排，依赖消息、协作、存储和定时等公共服务。
用户可将默认 agent 组合入口替换为自己的 agent 插件，由该插件拥有配置模型、实体装配、指令、工具、依赖与工作方式；具体边界见 [默认 agent 行为与替换](roadmap.md#默认-agent-行为与替换)。
`agent add` 填写 agent 信息及所选 channel／harness 的参数和凭据引用，保存配置后由 `bcn run` 创建连接与进程。
`bcn agent add` 由默认 agent 插件登记，更新自身 `config.agents`；省略号表示该命令及所选 provider 模型定义的具体输入。

`contrib add`、模块配置编辑和 `agent add` 使用本地管理组合，在节点尚未运行时完成读取、首次配置生成、候选校验与保存。
首个特性分支交付默认基础组合和运行前管理命令；具体 provider 随后在各自分支交付，完整流程在 provider 阶段及最终发行验收中使用真实 provider 验证。
本地管理装配、安装与 enable 的提交顺序见 [模块管理与自身 uv](framework.md#模块管理与自身-uv)。

## Python 与运行基础

0.3 的 app、core 与官方 contrib 统一要求 Python 3.15。Python 3.15.0 于 2026-10-09 正式发布，
开发解释器固定 3.15.0，CI 使用 uv 0.13.0；依赖升级后验证 Linux、macOS 与 Windows 的 CPython 3.15 二进制安装。
生命周期任务归属任务组，进入与退出保持同一任务；取消时等待所属任务清理完成，lifespan 内的资源按上下文栈逆序释放。
新包测试验证自身的真实资源装配、事件、故障与取消行为，使用 pytest-asyncio、aiosqlite、aiofiles 和 TCP streams。
当前 Task 1 实现独立 core、任务故障 guard 和实例故障隔离；后续阶段在新成员中实现业务能力、调用者与包内测试，最终发行阶段完成整体切换与旧代码清理。

## 计划组织

| 文件 | 内容 |
| --- | --- |
| [framework.md](framework.md) | 首个特性分支：框架、默认节点基础、模块与 agent 配置管理、串行 Tasks 和验收 |
| [roadmap.md](roadmap.md) | 全部能力的模块归属、替换依赖与顺序、阶段验收、0.3 发行清单及发布顺序 |
| [bazaar.md](bazaar.md) | contrib 市场页面：官方与额外模块索引、包安装到入口配置的流程、管理状态和串行 Tasks |
| [workspace 发版](../2026-10-21-workspace-release/plan.md) | 可选择分支的统一版本、候选产物验收与批量发布 |

后续特性分支的详细计划继续放入本目录，按能力命名，并从本入口与路线图关联。
公共版本目标、分支及仓库组织由本文件维护，阶段实现细节由各阶段文件维护。

## 后续待办

- [x] 完成 [Task 6](framework.md#task-6模块安装管理与升级) 的配置与管理入口接入，通过公共组合契约应用运行更新。
- [ ] 按下方文档站点安排发布统一开发与使用文档。

## 文档站点

插件定义、入口登记、发现、服务与事件、本地绑定和生命周期的开发文档归 core，完整指南源文件为
`core/docs/plugin-development.md`；app 持有节点安装、启动和命令说明，各 contrib 持有自身接口与参数说明。
包内 README 保留简明入口与用法，站点组织这些来源的完整指南。

后续使用 Markdown 与 MkDocs 构建 GitHub Pages 文档站点：构建时把选定的包内文档收集到临时文档目录，
通过 `mkdocs.yml` 的 `docs_dir` 与导航配置组织页面，用 `mkdocs serve` 本地预览、`mkdocs build --strict`
生成静态 `site/`，再由 GitHub Actions 发布 Pages 构建产物。文档源继续按包维护。

文档站点地址采用 `https://yuchanns.github.io/bazaar-compute-node/`，对应当前
`yuchanns/bazaar-compute-node` 仓库的 GitHub Pages 项目站点。

## 分支与 review

`0.3` 是版本集成分支，从 `main` 的 `a6516f4` 开始。
`feature/20261008-plugin-framework` 是首个特性分支，从同一基线开始，合入目标为 `0.3`。

```text
main
  └─ 0.3
       ├─ feature/20261008-plugin-framework ── review ──┐
       ├─ feature/<下一能力>              ── review ──┤ → 0.3 集成验收
       └─ feature/<后续能力>              ── review ──┘
                                                      ↓
                                      官方包候选组合全部验证
                                                      ↓
                                    0.3 → main → 官方包统一发版
```

每个能力有自己的详细计划、串行 Tasks、可审提交和特性分支，每个 Task 结束后 review。
必要包的特性分支合入 `0.3` 后，下一分支基于新的集成点创建，并验证已完成模块组合。
可选能力的实现分支在所属仓库 review；BCN 集成分支记录其固定提交、候选产物与兼容范围，更新官方索引并验证跨仓库组合。
完整版本通过最终验收后，`0.3` 合入 `main`，再发布 BCN `0.3.0`。

## 仓库、包结构与发行

BCN 仓库根管理 uv workspace、共享锁、开发工具与 CI，各发行包拥有自己的 pyproject.toml、src、资源及 wheel。
仓库目录、发行包、entry point 与运行实例分别标识；一个发行包可以提供多个入口，运行实例由宿主自动创建。

```text
bcn/
├── pyproject.toml                         # workspace 与开发配置
├── uv.lock
├── app/
│   ├── pyproject.toml                     # bazaar-compute-node
│   └── src/bazaar_compute_node/           # 命令入口、启动输入、默认资源
├── core/
│   ├── pyproject.toml                     # bazaar-core
│   └── src/bazaar_core/                   # 框架运行内核与公共 API
├── contrib/
│   ├── config/
│   │   ├── pyproject.toml                 # bazaar-contrib-config
│   │   └── src/bazaar_contrib_config/
│   ├── logging/
│   │   ├── pyproject.toml                 # bazaar-contrib-logging
│   │   └── src/bazaar_contrib_logging/
│   └── <功能>/                           # 每个官方功能包使用相同结构
├── scripts/
│   └── release.py                        # 官方包版本与发行产物工具
└── tests/
    └── support/                          # 开发测试辅助包
```

发行名与 import 名采用同一套命名：`bazaar-core` 对应 `bazaar_core`，`bazaar-contrib-<功能>` 对应 `bazaar_contrib_<功能>`，
仅将连字符换为 Python 标识符允许的下划线。用户安装的应用发行名继续为 `bazaar-compute-node`，模块为 `bazaar_compute_node`。

### 应用入口与依赖方向

`app/` 的 `bazaar-compute-node` 提供 bcn 命令、bcc 调用入口、启动参数与进程退出信号，持有默认依赖及业务默认资源。
默认节点组合位于 `bazaar_compute_node/defaults.toml`；版本包导出 `VERSIONS`，配置契约以 `CONFIG_CURRENT` 指定当前版本。
命令入口与启动调用使用普通 Python 函数；默认组合直接由 app 的发行依赖和配置资源表达。
app 直接 import bazaar_core，创建 ContribHub，将自举条目与声明 `CONFIG_STORE`、`CONFIG_CURRENT` 依赖并绑定默认资源的配置加载器交给其统一运行接口，
在宿主就绪后等待进程退出信号。

`core/` 的 `bazaar-core` 是框架运行内核，通过普通 Python 公共 API 使用。
core 的 discover／load 读取发行元数据并导入 entry point 的定义；ContribHub 启动自举与加载器，校验参数和服务依赖，管理实例、本地绑定、事件及完整退出。
应用或插件根据已校验业务输入构造 ContribEntry；Hub 根据 offers／accepts、绑定身份和父实例关系建立运行图，按依赖启动及回收资源。

contrib 持有功能契约、实现与第三方依赖。公共配置、审计、storage 与 timer 包同样位于 contrib/，
消费者与实现者显式导入所属功能包的公共类型和 key，具体模块实现由 entry point 加载。
配置加载器使用注入的 `CONFIG_STORE`、`CONFIG_CURRENT` 读写文档、调用版本服务与条件提交，通过 `ctx.validate()` 校验并以 `COMPOSITION` 将模块条目及运行实体交给 core；
core 的运行接口以普通异步函数调用该加载逻辑，依赖和导入保持框架通用。

```text
bazaar-compute-node (app) → bazaar-core
bazaar-compute-node (app) → 默认 bazaar-contrib-* → bazaar-core
可选／第三方 contrib     → bazaar-core + 所需功能契约包
server／web 组合包       → 所需 contrib 功能包
```

core 的发行依赖与导入保持通用；app 直接声明默认官方包及资源，server／web 各自声明可选组合。
包依赖负责安装代码，运行依赖负责装配服务，两者按各自边界校验。

### 本地 workspace 开发

BCN 必要包在同一个源码 checkout 内作为 workspace 成员联调，共用根目录 uv.lock 与 .venv。
首阶段建立 core、status、app、config、audit、config-filesystem、config-versions、logging 与 manager 成员；
各成员的源码和元数据随同一提交保存。根配置如下：

```toml
[project]
name = "bcn-workspace"
version = "0.0.0"
requires-python = ">=3.15"
dependencies = []

[dependency-groups]
dev = [
    "aiofiles>=25.1.0",
    "aiosqlite>=0.20.0",
    "bazaar-test-support",
    "blockbuster>=1.5.29,<1.6",
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.9.0",
    "pyright==1.1.414",
    "types-aiofiles>=25.1.0",
]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["app", "core", "contrib/*"]

[tool.uv.sources]
bazaar-compute-node = { workspace = true }
bazaar-core = { workspace = true }
bazaar-contrib-config = { workspace = true }
bazaar-contrib-audit = { workspace = true }
bazaar-contrib-config-filesystem = { workspace = true }
bazaar-contrib-config-versions = { workspace = true }
bazaar-contrib-logging = { workspace = true }
bazaar-contrib-manager = { workspace = true }
bazaar-contrib-status = { workspace = true }
bazaar-test-support = { path = "tests/support", editable = true }
```

根 project 是开发聚合配置；公开发行版本来自 app、core 与各 contrib 成员。
各包的 project.dependencies 保留正式包名、版本要求与 extras，tool.uv.sources 将官方依赖绑定到本地成员。
根 sources 供成员使用；成员的同名 source 优先，阶段检查复核覆盖与实际来源。
uv lock 解析完整依赖组合，解释器使用各成员 requires-python 的交集；
uv sync 将成员 editable 安装到开发环境，真实发行元数据与 entry points 用于发现。

在仓库根执行：

```sh
uv lock
uv sync --locked --all-packages
uv run --no-sync --package bazaar-compute-node bcn run --config /tmp/bcn-dev/config.toml
uv build --package bazaar-contrib-config-filesystem --no-sources --wheel --out-dir dist
```

源码编辑后重新启动开发宿主或执行检查；依赖、版本、入口和元数据变更后重新 lock／sync。
多成员环境以 all-packages 同步，运行使用 no-sync 保持该联调组合；开发使用测试专用配置与隔离数据。
各包使用自己的检查配置，Ruff 与 Pyright/LSP 和单测一样按 workspace 成员执行。仓库根命令逐包运行并汇总结果。
新增必要包时登记 BCN 成员、workspace source、发行依赖和入口，并更新共享锁。
独立可选包在各自仓库维护依赖、配置与锁；跨仓库联调采用以下外层开发工作区，并记录各仓库提交。

交付时逐包以 uv build --package <发行名> --no-sources 构建 wheel 与 sdist，检查 Requires-Dist、入口及资源，
在 workspace 外的专用临时环境从这些产物安装所需组合，以正式依赖验证安装、发现与业务流程。
sdist 在独立环境重新构建 wheel，验证正式产物所需源码、配置和静态资源完整。
最终发行从实际发布地址重复安装验收。

### 跨仓库开发工作区

开发者在不纳入任何仓库的 `dev/` 下放置 BCN 和所需可选包的 checkout：

```text
dev/
├── pyproject.toml
├── uv.lock
├── .venv/
├── bcn/                    # 自己的 Git 仓库与 uv workspace
├── contrib-codex/          # 独立 Git 仓库
└── contrib-telegram/       # 独立 Git 仓库
```

外层虚拟开发项目通过来源引用聚合这些独立工作区：

```toml
[project]
name = "bcn-dev"
version = "0.0.0"
requires-python = ">=3.15"
dependencies = ["bazaar-compute-node", "bazaar-contrib-codex", "bazaar-contrib-telegram"]

[tool.uv]
package = false

[tool.uv.sources]
bazaar-compute-node = { workspace = "bcn" }
bazaar-contrib-codex = { path = "contrib-codex", editable = true }
bazaar-contrib-telegram = { path = "contrib-telegram", editable = true }
```

外层执行 `uv lock`、`uv sync --locked`，共同环境 editable 加载各仓库源码；各仓库继续持有自己的配置和独立检查。
多包 contrib 仓库同样通过 `workspace="<checkout>"` 引用所需成员。
在 uv 0.12.2 与 Python 3.15.0 的隔离多仓库示例中已验证 lock、sync、实际导入、源码即时生效及内层配置／锁保留。
交付验收仍从各仓库固定提交构建正式 wheel，并在开发工作区之外安装。

### 官方统一版本与兼容规则

BCN 仓库的 app、core、公共接口及必要 contrib 使用同一个版本，首版为 `0.3.0`，共用一次发行与 `v<版本>` 标签。
这些包的仓库内正式依赖按各自实际使用的公共接口声明三段式兼容范围，如 `bazaar-core~=0.3.0`（`>=0.3.0,<0.4.0`）。
必要包即使源码未改，也随 BCN 发行升版；依赖下限独立维护，仅在使用更新接口时提高。
升级 app 时，用户保存的兼容版本要求继续参与解析，可以保留选定的旧依赖；workspace 的共享锁固定开发和验收使用的实际版本。
可选官方包与第三方包由各自仓库独立维护版本、标签和产物，声明 core 的兼容范围，例如 `bazaar-core>=0.3.0,<0.4.0`，以及所需公共接口包的兼容范围。
server／web 等独立组合声明自身所需包范围；整体集成验收保存每个包的实际版本、来源、仓库和源码提交。

app 的共同发行清单只标识 BCN 必要包；官方安装索引从 BCN workspace 自动生成 contrib 工作区包的 package/source 条目，随 wheel／sdist 发布。
管理服务将目标 app 和保存的全部额外包要求交给 uv 一次解析；可选包版本依其兼容范围与用户要求选择，显式固定和来源继续参与冲突检查。
Contrib 声明的公共 API 范围供加载边界复核。
0.3.x 维护已公开的 0.3 框架契约，破坏性契约调整进入新的兼容边界；依赖范围外的组合在安装解析与启动校验时诊断。

固定发行名、目录、入口与默认／可选归属见 [路线图的拆包清单](roadmap.md#发行包模块入口与内置组合)。
app、core、公共接口和默认节点包留在 BCN 共同发行，可选包在 open-bazaar 独立维护；channel 与 harness 两个公共接口包各附带共享 builder 注册入口。
config 载体和五版服务先于完整业务文档装配；storage 提供 storage 与 database 两个运行入口。
server／web 保持可选安装；BCS 组合直接读取自己的 ~/.bcs/config.toml、bcs.sqlite3 与 session.key，
保留原配置结构、账户、权限、历史数据、已发布迁移及旧节点协议。
完整升级规则见 [BCS 连续升级](framework.md#02-bcs-配置与数据源连续升级)。

### 发版工具与 CI

PR 的规划 job 读取 PR base 与 head 的 merge-base 变更列表，再把受影响包交给下游动态矩阵。
包集合及依赖直接来自 uv workspace metadata 的 members 和 resolution，
CI 固定 uv `0.13.0` 并启用 `workspace-metadata` preview；使用不透明 node id 遍历依赖图。
PR 与 Release 共用 `.github/actions/setup`、`checks` 和 `test`，统一环境准备、逐包静态检查、
普通测试、独立 system 测试及 wheel／sdist 验收；workflow 传入各自规划的包集合。
文件归属采用最深的本地包目录，新增、删除与移动文件都参与判断；受影响包及依赖它的消费者进入矩阵，
开发依赖和 extras 的变更同样影响对应消费者。根 workspace 配置、共享锁、许可证与 CI 工具变更验证全部成员。
变更分析包含各平台条件依赖，产物安装使用当前平台的发行依赖闭包。

Ruff 格式与代码检查、Pyright/LSP 共用受影响包清单。包内 `pyrightconfig.json` 或 `pyproject.toml` 的 `[tool.pyright]` 通过 `extends` 继承根目录 `pyrightconfig.json` 的公共规则，只声明自身源码与测试范围；根目录配置的检查范围为仓库工具。
根目录 Ruff 配置启用 `ASYNC` 规则，成员通过 `[tool.ruff].extend` 继承共享规则，并在自己的包目录执行检查。
每个成员使用独立的 LSP 工作区和语言服务器会话，Ruff 检查同一成员选定的 Python 文件，并读取该包生效的 Ruff 配置。
根目录 `uv run --quiet scripts/pyright_lsp_check.py .` 从 uv 元数据发现成员，逐包检查；`--package` 可指定单个成员。
输出供 LLM 读取，每条诊断为 `文件:行:列: 级别[规则]: 原因`，多行详情缩进。仅输出有诊断的文件，失败末尾带 `FAILED` 和诊断数量；全通过时静默退出 0，任意诊断退出 1，异常或检查未完成退出 2。
CI 使用同一个成员矩阵执行 `scripts/ci.py check --package`，新增包通过自己的标准工具配置参与检查。

矩阵按发行包与 Linux／macOS／Windows 展开。各成员按自己的 pytest `testpaths` 运行新包测试和独立的 `pytest -m system`；
缺省测试目录为成员自己的 `tests/`，仓库脚本作为开发工具维护。
包测试通过共享 pytest 入口启用 Blockbuster，在 asyncio 事件循环中执行已知同步阻塞调用时使测试失败。
发布检查通过 `scripts/ci.py test --all-packages` 和 `scripts/ci.py check --all-packages` 验证全部 workspace 成员，沿用各包的测试与静态检查范围。
native service 仅在隔离 CI runner 执行。每个受影响包及其 workspace 发行依赖分别构建 wheel／sdist，
在 workspace 外安装相应产物，验证发行版本、许可证元数据、许可证文件与模块导入；
成员配置的测试目录存在 `package_smoke.py` 时，以发行版本为参数执行其真实资源与业务验收。
新增 workspace 成员沿用这些目录约定，自动参与规划、测试、构建与安装验证。

Release workflow 保留单个 version 输入（X.Y.Z 或 X.Y.ZrcN）。`scripts/release.py` 从根 workspace 成员发现官方公开包，
读取各成员 project.name、version、正式依赖和源码目录，形成 BCN 必要包的发行集合与依赖顺序。
根开发 project 与 tests/support 属于开发资源；发行集合为 app、core 与 contrib/ 下的公开成员。

工具用一个目标版本同步 BCN 仓库的所有公开成员的 project.version 及所需资源版本引用，保留各包仍适用的依赖下限与兼容范围，更新 uv.lock，
复核共同版本、依赖闭包和运行入口唯一性。变更形成一次已验证签名的 Release v<版本> 提交，
提交使用准备时所选发行分支的 HEAD 作为预期值；源码推进时重新准备，所有构建与后续发布固定使用该发行提交。
Release 已在默认 main 登记，运行时使用 GitHub 自带的 Branch 选择；版本仍只输入一次。
正式版必须从 `main` 运行，RC 可以使用其他发行分支。
当前基础组合先从 `0.3` 发布 `0.3.0rc1`，为后续独立 provider 的安装和测试提供真实包源。
工具及流程见 [workspace 发版计划](../2026-10-21-workspace-release/plan.md)。

独立可选仓库按其兼容范围完成自己的构建与 CI，集成清单固定所用提交、版本和产物。
BCN CI 逐包构建独立 wheel／sdist，以必要包产物及固定的独立可选产物完成干净安装和三平台组合验收，
再按正式包依赖拓扑顺序上传：core 与基础契约先于消费者，app 在默认依赖上传并可解析后上传。
每包记录发行名、版本、源码提交、文件名、校验值及发布结果；部分上传失败时保留同一发行提交和构建产物，
重跑按目标版本找到已保存的发行提交与产物清单，核对已发布文件的校验值后补齐缺失项。
发现同名同版产物不一致时报告冲突并停止该次发行。
全部包发布和实际包源安装验收完成后，发布同一提交上的 annotated v<版本> 标签和整体 release。
版本准备、构建、验收与发布均由仓库工具及 CI 执行，人工输入只需要共同目标版本。
0.3 最终发行的冻结、升级验证与发布顺序见 [路线图的发行清单](roadmap.md#03-发行清单与发布顺序)。

## 默认初始化提示中的运行信息（2026-10-25）

### Task：移除会话号、运行时及对应的提示变量

默认 agent 通过 `SessionInput.id` 和 `BCN_RUNTIME_SESSION_ID` 绑定实际运行会话。
本任务删除默认初始化模板中的 `Session`、`Runtime` 展示行，
清理 `DeveloperInstructionContext` 的 `runtime_session_id`、`runtime` 字段及启动会话时的对应传参。
独立安装验收继续验证工作目录和会话关联，移除对这两项提示展示的旧要求。
验证包括真实模板渲染、agent 包的 Ruff 检查和格式检查、仓库根目录的 Pyright/LSP 检查，
以及 wheel、sdist 的独立安装验收和发行包内模板核对。

## Codex 行为对照后的宿主契约（2026-10-25）

开发线程在 `46341e85-6dbe-5a66-b94c-1cf6ee3d91dc` 确认九项决定和审计失败隔离，
要求主仓库从 `0.3` 建分支并提交 PR，完成后等待 review。
第 2 项按 `20a6edaa-99ea-5767-a131-88c9064f548e`、`1357d54e-71b0-519e-89d8-fe9e95180a4a`
的最终 review 决定，将 session 沙箱配置收拢到 Agent，所有 Harness 共用，审批由实际沙箱权限决定，网络固定开启；
第 6 项按 `678aca6f-389a-5c8b-ad39-2827685f0436` 将原生 interrupted 作为已确认取消，
只报告结构化中断错误。其余故障处理、第 7 项调用预算与退出、第 9 项事件投影沿用 0.2。

### Task 1：宿主策略、生命周期输入及审计失败边界

Agent 的 mode 继续决定按 Thread 或整个 Agent 处理消息，权限设置也由所属 Agent 统一提供。
session 的沙箱配置位于 Agent 的 `session.config.sandbox.policy`，取值为 workspace-write 或
danger-full-access，省略时默认 workspace-write；dangerous_individual 固定使用 full access，
携带 session 配置时仅记录 warning 并忽略该配置，继续启动。
Harness 选项只保留 provider 自身参数。
命令编辑器沿 `--set` 路径赋值时，为缺省或 `None` 的中间配置节点创建字典，继续向下赋值。
Agent 的 session 保持可省略，首次通过 `agent edit` 修改 `session.config.sandbox.policy` 可正常保存并应用。
公共 `SessionInput` 只携带实际 SandboxPolicy，移除 ApprovalPolicy 与 network_access 开关。
provider 固定开启网络、保留原生审批回调；宿主先校验请求的 session／turn 关联，
full access 自动返回 approved，其余沙箱使用原有 Channel 审批卡收集人工决定。
原生已允许而没有产生审批请求的操作正常执行，provider 不使用 never 禁用审批请求。

V4→V5 在 session 模式只取第一个历史 runtime 的 sandbox_mode，迁移到 Agent 的 session 配置，
无 runtime 或未指定沙箱时使用 workspace-write；individual 直接丢弃 session 配置。
各个 runtime 的 provider 参数仍分别迁移到 harness，不合并或删除 runtime。
移除旧 network_access 用户选项，provider 的 model、effort、env 等实际参数继续保留。
V5 的 contribs 文档结构不变，既有更早版本转换继续沿当前版本链执行。

旧版组合根分别提供启动 60 秒、普通调用 600 秒及关闭 5 秒，恢复调用使用启动预算。
当前无参数的 provider lifespan 关闭无法取得独立关闭预算，初始化也只能复用普通调用预算。
`HarnessInput` 增加调用者必须提供的 startup_timeout、shutdown_timeout，使用接口边界校验，
默认 agent 在装配 provider 时明确传入原有 60／5 秒；每次调用仍使用既定调用预算。
独立 agent 消费者按同一公共契约明确提供自身生命周期预算。

`NodeAudit.publish` 仅捕获 `ctx.announce` 的事件投递异常，记录日志后隔离该发布失败。
事件构造与模型校验正常执行，构造或校验错误正常抛出。
审计接口接收直接构造的 `AuditContext` 和 `JsonValue` payload，调用方直接执行
`model_dump(mode="json")` 并传值，保留已有的凭据字段排除；调用方序列化位于 publish 的捕获范围之外。
按 `afc9531b-02f7-5aac-a4f1-934b4e4c3db3` 和 `04e43538-afc7-5b42-9af7-37a5f2ff78db`
的 review 意见移除 context 与 payload 的延迟构造及其 Callable 传递。
消息、协作、提醒直接构造审计记录并调用 `publish`，移除包住整段构造的三处异常捕获。
该边界按 `1f46641a-aadc-5fe9-8009-4283bb05b827` 的 review 收敛到事件投递，
调用方的 context／payload 组装、序列化与校验错误正常抛出。
原生中断结果使当前回合结束并报告错误，跳过恢复、provider 故障封禁及替代执行。

验证采用现有独立 wheel／sdist agent 消费者与真实文件／子进程业务场景，
通过现有 TCP Channel 场景验证受限 session 人工审批、full-access session 和 individual 自动审批，
其中 individual 携带 workspace-write 的 session 配置，验证仍按 full access 执行业务。
既有 agent-command 安装验收通过 CLI 首次设置离线 Agent 的 workspace-write，
并在运行中为另一默认 Agent 首次设置 danger-full-access，再通过 Channel 完成自动审批和文件报告业务。
核对实际文件写入及 Thread／Agent 会话归属；既有转换测试使用不同 runtime 沙箱值验证首项选择。
执行 Ruff、格式检查、仓库根目录 Pyright/LSP，以及受影响包的构建安装检查。
主仓库形成一个签名提交和指向 `0.3` 的 PR，等待人工 review。

上一轮 Task 1 本地验证结果：受影响 Python 源码的 Ruff 与格式检查通过，
仓库根目录要求的 Pyright/LSP 检查通过；既有 agent 独立 wheel／sdist 消费者
1 项通过（234.91 秒），消息、协作、提醒的安装态业务测试各 1 项通过。
本轮 Agent 共用 session 配置修订验证完成：Ruff／格式检查 51 个文件及仓库根目录 Pyright/LSP 通过，
既有 agent 独立 wheel／sdist 安装验收 1 项通过（272.52 秒），
agent-command 的 venv／uv tool 安装验收 2 项通过（124.62 秒），
config-versions 转换测试 3 项通过（0.16 秒）。
实际转换器产出的两种 mode 配置均通过目标 Agent 配置模型校验，
验证首个 runtime 的沙箱选择及各 runtime provider 参数保留。
Codex 对新契约的真实接入验收在 Task 2 执行。

payload 直接传值修订的 Ruff、格式检查及仓库根目录 Pyright/LSP 通过，
既有 agent 独立 wheel／sdist 安装验收 1 项通过（264.88 秒）。

非 session 配置仅 warning 的修订通过 Ruff、格式检查及仓库根目录 Pyright/LSP；
既有 agent 独立 wheel／sdist 安装验收 1 项通过（261.80 秒），
确认 individual 携带 session 配置仍可启动、自动审批并完成文件操作、配置重载与进程恢复。

审计异常边界收敛的 Ruff、格式检查及仓库根目录 Pyright/LSP 通过；
既有 wheel／sdist 安装态业务验收 agent 1 项通过（268.92 秒）、
消息 1 项通过（43.27 秒）、协作 1 项通过（16.57 秒）、提醒 1 项通过（40.09 秒）。

CLI 首次编辑可选 session 配置的修订通过 Ruff、格式检查及仓库根目录 Pyright/LSP；
既有 agent-command venv／uv tool 安装验收 2 项通过（117.84 秒），
离线首次设置受限策略、运行中首次切换 full access 并生成文件报告均通过。

README 按 `b8dc4a40-e061-5e46-acac-6a2602ddea3e` 的 review 收敛为安装、配置与使用：
agent 提供创建、权限调整及两种会话模式的命令示例；harness 提供安装与 contrib 选择；
audit 说明启用及订阅 contrib 的使用。使用说明中的可选模块统一称为 channel／harness contribs。
配置归属、版本转换、生命周期预算、审批路由、
中断处理和审计异常边界统一记录在本计划，接口说明由框架设计文档维护。
本次文档示例通过现有命令参数解析与 AgentConfig 校验，TOML 和相对链接核对通过；
当前 app／core／contrib／scripts 的 Ruff 检查及全仓库格式检查通过。

### Task 2：Codex provider 衔接

在 Task 1 review 后，Codex 按实际 SandboxPolicy 和分阶段生命周期预算映射原生请求，
固定开启网络并接入原生审批请求，审批决定统一交给宿主的 full-access／人工路由处理；
恢复、非主动取消故障处理、写入与退出等待、子进程回收及输出投影按已确认的 0.2 行为实现，
保留新的审批能力、审批异常拒绝及关闭活动流时中断的决定。
通过独立仓库的 TestChannel、隔离 BCN 及真实 Codex 路径验证业务结果，
在原验收计划登记所用契约版本、提交与实际证据。

### Task：timer 公共超时封装（2026-10-25）

开发线程 `6e006c15-32b1-5bde-9401-7a63216c1019` 确认包职责，
`7d4ec73a-6fb2-5dac-a8b9-6318279c4a86` 授权实现。
`bazaar-contrib-timer` 导出 `timeout(timer, delay_ms)` 异步上下文管理器，
基于已有公共 Timer 的 create／wait／cancel 驱动超时；毫秒参数沿用提供者的校验与调度粒度，
`None` 表示禁用超时。具体时间轮继续实现原基础契约。

进入上下文时记录当前任务的取消计数，创建 handle 和所属等待任务；到期取消当前任务，
退出时只撤销本作用域发起的一次取消，并在没有额外取消请求时转换为 `TimeoutError`。
外部取消和外层超时继续传播；提供者等待失败唤醒所属作用域并保留原异常。
正常完成、业务异常和取消都回收 handle，并等待所属任务结束。
参考已核对的 [CPython 3.15 timeout 实现](https://github.com/python/cpython/blob/3.15/Lib/asyncio/timeouts.py)
及 [Task 取消计数语义](https://docs.python.org/3.15/library/asyncio-task.html#asyncio.Task.uncancel)。
插件依赖图已有逆序退出及清理等待保证，消费者通过 accepts 声明 TIMER。

使用既有真实 ContribHub／timer-wheel 测试装配，验证文件操作正常完成、超时后的业务回退与继续工作、
外部取消清理、内外层分别到期及超时清理期间外部取消。补充公共使用说明，
执行 Ruff、格式、timer-wheel 包测试、仓库根目录 Pyright/LSP 和相关发行包构建后交人工 review。

本地结果：真实插件装配的 timer-wheel 测试 **11 passed（3.36 秒）**，
包含原有基础能力、提供者关闭时超时作用域收到原异常，以及五项文件／取消／嵌套业务检查。
app／core／contrib／scripts 的 Ruff、三个 Python 文件的格式检查通过；
timer 包 Pyright CLI 为零错误、零警告，仓库根目录语言服务器检查通过。
timer 与 timer-wheel 的 wheel／sdist 构建通过，timer 的 wheel 制品包含公开导出的封装实现。

远端 [CI 40202866614](https://github.com/yuchanns/bazaar-compute-node/actions/runs/40202866614)
在实现提交 `fd5880e8efff498fb2cad8fa153f48e8bf0fe41a` 上全部 9 个任务通过。
timer-wheel 的同组真实插件测试 **11 passed（3.35 秒）**；Agent 在 Linux／Windows 的安装态业务验收，
以及 agent-command、消息、协作、提醒的 Linux 安装态回归均通过，静态检查通过。
本次仅将终态结果补入原计划，提交实现与测试内容保持与该 CI 一致，交人工 review。

同一迁移任务按 `4ac73882-0828-5c7c-bfc9-56c26f50367e` 补充公开的
`wait_for(timer, awaitable, delay_ms)`，泛型结果直接返回被等待对象的结果。
它复用同包 timeout 的到期取消、外部取消和清理逻辑，保持毫秒与 None 语义。
原来等待单个 awaitable 的九处迁移调用改用 wait_for；reminder 启动恢复的多步操作继续使用 timeout。
现有真实文件、回退与外部取消测试同时覆盖直接作用域和单次等待，相关安装态业务继续通过原装配验证。
参考 Python 3.15 的 asyncio.wait_for 与 timeout 文档，公共 Timer 与时间轮基础契约保持原样。

按 `86c7ba8b-6b73-5813-a66a-cd5cb6aae578` 收敛到实际需要的 timeout 和 wait_for。
wait_for 补充后的 Ruff、264 个文件的格式检查及仓库根目录要求的 Pyright/LSP 通过；
八个包共十六个 wheel／sdist 构建并逐项核对源码字节与依赖元数据通过。
迁移初版 `29220b4718030b5b9a1dcd9b594b4a2acc0bcf78` 的
[CI 40208448618](https://github.com/yuchanns/bazaar-compute-node/actions/runs/40208448618)
全部二十七项任务通过，包括 Linux／macOS Unix 连接和 Windows 命名管道安装场景；
新增 wait_for 与消费者调用完成本地复验后，在更新后的提交上重新执行 CI。

wait_for 复验：timer。公开 wait_for 的实际文件结果返回、禁用预算、到期回退与外部取消，在现有真实时间轮场景中复验，11 项通过（3.31 秒）。

零时限语义按 `395cbddd-8a77-58fd-a838-2121585bb48c`、
`ad9831c4-a330-5068-8745-be2984995cfe` 收敛到 asyncio 的实际行为。
已核对 CPython 3.15 的 tasks.py 414–457 与 timeouts.py 46–120：
wait_for 的非正预算直接处理已完成结果或取消等待对象；timeout 的非正预算通过 call_soon 取消当前任务。
这两条路径复用 asyncio 的公开实现，不创建 TIMER handle；None 继续禁用时限。
正预算使用 TIMER handle.wait 取得到期通知，再对公开 asyncio.Timeout 调用 reschedule(loop.time())，
由标准库完成取消计数、嵌套、外部取消和异常链转换；其初始截止时间为 None，实际正时限只由 TIMER 计时。
退出时先关闭原生 Timeout 作用域，再取消并等待所属 Timer 等待任务，避免清理等待使已经完成的业务再超时。
保留提供者关闭异常的传播；消息和提醒排空可直接调用已经支持零预算的公共 wait_for。
现有真实文件回退与嵌套场景覆盖非正和正预算，复验外部取消、提供者退出及安装态业务。
参考 https://github.com/python/cpython/blob/3.15/Lib/asyncio/tasks.py#L414-L457
及 https://github.com/python/cpython/blob/3.15/Lib/asyncio/timeouts.py#L46-L120 。

零时限修订的验证：Ruff、264 个文件的格式检查和仓库根目录要求的 Pyright/LSP 通过。
真实时间轮的既有文件回退、非正预算、内外层超时、外部取消及提供者关闭共 14 项通过；
提供者异常从已完成等待任务的结果读取，最终该分支已随同组场景复验通过。
使用真实 ContribHub／时间轮及实际文件对照 asyncio：零预算未开始的投递被取消，
已完成投递结果可直接返回，timeout(0) 的代码块执行至让出点后超时，三项观察结果一致。
SQLite 8 项、Unix sidecar 4 项、消息 1 项、提醒 1 项及 Agent 完整安装态 1 项通过，
八个包的十六个 wheel／sdist 构建、源码字节与依赖元数据核对通过，共享锁内容保持不变。
修订前 `4c808b4b69a8e8d5c1acce5121305306f85a0c7b` 的
[CI 40210350758](https://github.com/yuchanns/bazaar-compute-node/actions/runs/40210350758)
全部二十七项任务成功；本轮零时限与原生取消处理修订将随更新后的八个签名提交重新执行 CI。

零预算修订：timer。非正预算直接使用 asyncio 原生快路径，正预算由 TIMER 驱动公开 Timeout 的取消与异常转换；14 项真实场景及三种零预算文件行为对照通过。
