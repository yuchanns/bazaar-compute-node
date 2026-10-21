# Native Host Service Integration

## 目标

为 bcn 增加显式的宿主机后台托管集成，让 Linux、macOS 和 Windows 使用各自的原生机制启动、保持和停止 bcn，同时复用现有的前台 `bcn run` 与本机 control endpoint。

## 当前基线

- `bcn start` 保留为兼容入口，输出弃用提示后转发到 `bcn system-service start`；前台运行使用 `bcn run`。
- `bcn run` 在当前进程中持有 `NodeApplication`，并处理 daemon shutdown。
- `bcn stop` 和 `bcn restart` 是带弃用提示的兼容入口，转发到 native host service；`bcn run` 前台调试进程仍可通过 control endpoint 关闭。
- Agent 配置只保存 `token_env` / `secret_env` 的环境变量名，后台启动不会继承交互式 shell 的完整环境。

## 已确定的边界

1. OS 托管入口始终执行前台 `bcn run`，不能执行会再次 daemonize 的 `bcn start`。
2. 默认安装为当前用户级服务，保持 `Path.home()/.bcn`、配置文件和凭据环境一致；不默认要求 root 或 system-wide 安装。
3. PyPI/uv 安装不静默修改宿主机服务状态；服务注册必须由显式命令触发。
4. 生成的 service definition 不保存 token、secret 或其他凭据值；环境由用户提供的权限受限 env file 或平台环境机制注入。
5. 平台定义由 `src/bazaar_compute_node/app/system_service.py` 动态渲染；安装到宿主机的文件是渲染后的运行时产物。
6. Windows 第一版沿用用户登录触发的 Task Scheduler + PowerShell wrapper；不引入 Windows SCM service 依赖。

## 平台渲染与安装产物

`system_service.py` 使用当前已安装的 `bcn` executable、绝对 config/data/log 路径和平台运行时状态动态生成服务定义；macOS 安装产物例如位于 `~/Library/LaunchAgents/io.github.yuchanns.bazaar-compute-node.plist`，不依赖源码目录。

## 分阶段任务

### Task 1：平台定义契约与模板布局

- 为 `bcn run --config ...` 定义一致的启动参数、环境注入和日志契约。
- 在 `system_service.py` 中实现不包含凭据值的 systemd、LaunchAgent 和 Task Scheduler 动态渲染。
- 添加渲染结构测试，确保入口不会误用 `bcn start`，并覆盖关键重启/退出语义。
- 完成后停止，等待 review。

### Task 2：显式服务安装与管理 CLI

- 增加 `bcn system-service install|start|stop|restart|uninstall|status` 命令。
- 按平台渲染模板并执行 enable/register；`install` 只注册服务，不启动当前进程，不使用 `--now` 或 `schtasks /Run`。独立的 `system-service start` 负责调用平台原生启动动作。
- `status` 同时检查 native manager 的注册状态和 bcn health；旧 `bcn start/stop/restart` 均输出弃用提示后直接复用对应的 `system-service` 生命周期动作，未安装服务时明确要求先执行 `bcn system-service install`。
- 安装与卸载保持幂等，只操作由 bcn 管理的 definition，不触碰数据库、配置和 workspace。
- 将平台控制实现限制在 host-service 层，保持 core、`NodeApplication` 和 provider adapter 无 OS 依赖。

### Task 3：文档与完整验证

- 补充用户级安装、环境文件、升级、日志和 graceful stop 文档。
- 将 bcn 主 CLI、agent/system-service help、弃用提示和未安装错误接入现有 i18n catalog，并支持嵌套命令的独立 `--help`。
- 添加跨平台命令构造、路径转义、状态检查和失败回滚测试。
- 执行 focused tests、全量测试、Ruff、Pyright/LSP、package smoke 和 diff 检查。

## 当前停止点

Task 1、Task 2 和文档/验证已完成。当前分支不提交、不推送，保留工作区供 review；review 通过后再决定是否进入提交与推送流程。
