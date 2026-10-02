# BCN_HOME 节点根目录

## 目标

通过 `BCN_HOME` 决定节点 `.bcn` 目录放在哪里，统一配置、SQLite 数据库、工作区、日志、锁和默认通信端点的路径来源，并支持 Windows 使用其他盘符及盘符根目录，例如 `E:\`。

## 已确定的设计

- `BCN_HOME` 未设置或为空时，默认使用 `Path.home()`；节点目录固定为 `BCN_HOME / ".bcn"`，因此默认仍为 `~/.bcn`。
- 默认用户 HOME 与显式 `BCN_HOME` 都展开 `~` 并解析为真实绝对路径，再追加 `.bcn`，保证链接目录在安装、服务启动和端点标识中一致。相对路径以当前进程工作目录为基准；Windows 使用原生 `Path` 处理 `E:\`、`E:\bcn` 和含空格的路径，`E:\` 对应 `E:\.bcn`。
- 删除 `BCN_DATA_NAME`、目录名常量和 `resolve_data_name()`，保留各调用方共享的 `resolve_data_dir()`。
- `--config` 和 `--endpoint` 覆盖各自位置；默认配置、数据库、工作区和端点继续通过已有入口派生。
- 系统服务安装记录节点目录的父目录作为 `BCN_HOME`：systemd 使用 `Environment=`，launchd 使用 `EnvironmentVariables`，Windows PowerShell wrapper 设置 `$env:BCN_HOME`。服务的工作目录、配置与日志路径仍指向 `.bcn` 内。服务的环境文件仍在启动时读取，与已有平台环境注入顺序一致。
- `install` 使用当前环境选择安装位置；`start`、`stop`、`restart`、`status` 和 `uninstall` 从已注册服务读取工作目录和配置路径。因此安装时设置一次 `BCN_HOME`，后续管理服务可直接使用命令。显式 `--config` 继续覆盖配置路径。
- 服务加载运行时环境文件后，安装时记录的 `BCN_HOME` 优先于文件里的同名变量，保证配置、数据和管理端点使用同一目录。
- `server connect` 从已注册服务恢复默认配置及环境文件位置；Windows 任务目录查询由管理命令和连接命令共享，前台节点仍按自身目录查找环境文件。
- 已注册配置和数据目录的读取提取为共享函数，服务管理和 `server connect` 都复用。连接命令仅查询路径，服务上下文仍独立解析安装账号，支持无用户名记录的数字 UID，同时保留 `BCN_HOME` 的注册位置恢复和显式 `--config` 覆盖。
- 数字 UID 验证通过 Linux `setpriv` 为实际 CLI 子进程设置无 passwd 记录的 real UID，并清除用户名环境变量；effective UID 保持测试文件所有者，以读取 runner 安装的 Python 和项目依赖。[Python getpass](https://docs.python.org/3/library/getpass.html#getpass.getuser) 使用用户名环境变量或 real UID 查询账号，因此该进程真实覆盖账号解析失败的条件，配置和 token 写入仍访问原服务位置。

## 调研结论与修改位置

- `src/bazaar_compute_node/core/paths.py` 是节点持久化根目录的共享入口；`app/config.py`、`app/application.py`、`contrib/sqlite/database.py`、`cmd/bcn/node.py` 和 `app/windows_pipe.py` 已从这里派生路径。
- `app/system_service.py` 在安装时构建 config/data/log 路径并渲染三个平台模板；Windows Task Scheduler 的 `WorkingDirectory` 已使用 data_dir，跨盘路径由绝对路径传入。
- [systemd 官方 Environment 文档](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.exec.xml) 规定整条环境变量赋值可加引号，环境文件在 `Environment=` 之后注入。统一在 `_systemd_quote()` 中转义所有路径的 `%`，覆盖 executable/config/data/env-file 和环境赋值；管理命令和 `installed_env_file()` 读回 unit 路径时还原 `%%`。
- [systemd 官方解析实现](https://raw.githubusercontent.com/systemd/systemd/main/src/core/load-fragment.c) 对 `WorkingDirectory=` 和 `EnvironmentFile=` 直接解析整条路径，不移除 shell 引号。因此这两项保留空格、仅转义百分号；`ExecStart` 参数和 `Environment` 赋值使用双引号并转义反斜杠和双引号，分别按对应格式读回。
- launchd plist 已对路径进行 XML 转义；Windows wrapper 的路径使用已有 PowerShell 单引号字面量处理，盘符根目录末尾反斜杠无需 shell 转义。
- 服务管理路径恢复复用当前注册产物：Linux 读取 managed unit 的 `WorkingDirectory` 和 `ExecStart --config`；macOS 读取 managed plist 的工作目录和 `BCN_CONFIG`；Windows 通过 [Get-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/get-scheduledtask?view=windowsserver2025-ps) 查询当前用户的已注册任务，核对 Description 标记后读取 Actions.WorkingDirectory，再读取该目录内 managed wrapper 的 `$configPath`。
- [systemd 官方命令行文档](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml) 规定 `:` 前缀关闭环境变量替换；使用 `ExecStart=:env` 传入安装目录赋值后启动 bcn，使 `${TEAM}` 等字面路径保持原样，并在环境文件加载后重设目录。launchd wrapper 保存原目录，在 source 后恢复；Windows wrapper 在 dot-source 后重新设置安装目录。
- 服务测试通过原生管理器启动真实 bcn 和临时 SQLite，再清除当前终端 `BCN_HOME`，用恢复后的管理上下文访问真实健康端点和数据库。Linux 使用唯一命名的临时 systemd unit；macOS 使用唯一 Label 的临时 launchd job；Windows CI 使用隔离 runner 的真实 Task Scheduler 注册、启动、重连和清理。环境文件故意含其他 `BCN_HOME`，路径含空格、美元符号和百分号，验证系统实际解释结果。替换服务模块中的 mock、文本快照和渲染后读回测试。
- `tests/conftest.py` 和 `tests/support/src/bcn_test_support/environment.py` 当前依赖旧变量；改用临时目录的真实绝对路径，使测试数据根与用户 home 独立，并统一 macOS `/tmp` 与 `/private/tmp` 的路径表示。

## Task 1：统一根目录与服务启动环境

- [x] 实现路径入口，删除旧目录名配置，更新三平台服务环境渲染和真实原生服务测试。
- [x] 更新测试隔离工具及现有路径测试，验证配置、SQLite 和真实隔离子进程仍使用自定义根目录。
- [x] 管理命令恢复已注册服务的路径，验证清除终端 `BCN_HOME` 后仍能定位配置和健康检查端点。
- [x] 按审查意见使用实际配置/数据库/文件和范围行为验证代替精确路径断言，验证 HOME 链接及 systemd 百分号路径的完整写入/读回。
- [x] 修复连接命令的已注册环境文件定位、systemd 字面美元符号和三平台安装目录优先级，并执行真实行为验证。
- [x] 执行相关现有测试、Ruff、`uv run scripts/pyright_lsp_check.py --outputjson .` 和 diff 检查；记录 Windows 原生验证范围。

## 验证结果

- [三系统 CI](https://github.com/yuchanns/bazaar-compute-node/actions/runs/37010460774) 全部成功。每个平台先执行普通测试，再独立执行 `pytest -m system`；外部 Provider e2e 继续排除。
- Linux 真实启动唯一命名的临时 systemd unit，macOS 真实 bootstrap 唯一 Label 的 launchd job，Windows 真实注册并启动 Task Scheduler 任务。三个平台的真实 bcn 都在包含空格、`${TEAM}` 和 `%` 的安装目录创建 SQLite 数据库、完成迁移并通过健康检查；环境文件中的其他 `BCN_HOME` 未改变节点目录。
- 清除当前终端 `BCN_HOME` 后，三个平台的管理上下文都能访问原节点的真实健康端点；`server connect` 将配置写回原配置文件、将 token 写回服务实际读取的环境文件。临时服务在测试结束后由原生管理器停止并清理。
- Linux 的真实 CLI 子进程使用没有账号记录的 real UID、清除用户名环境变量后，仍成功连接并写回原配置和环境文件。Linux system 测试 2 passed；macOS 和 Windows 各 1 passed、1 skipped，跳过的仅为 Linux 专属数字 UID 测试。
- 普通测试：Linux 583 passed、1 skipped；macOS 582 passed、2 skipped；Windows 572 passed、12 skipped。普通测试各排除 49 个 e2e 和 2 个 system 测试。
- 全仓库 Ruff lint/format 检查 411 个文件通过，仓库根目录 Pyright LSP 检查 347 个文件、零诊断；三系统 wheel/sdist 构建及两种安装包冒烟验证全部通过。

Task 1 完成，当前停止点为 review。
