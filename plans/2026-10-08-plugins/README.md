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
