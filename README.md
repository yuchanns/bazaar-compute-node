# Bazaar Compute Node

## 本地开发

项目使用 uv 管理 Python 环境和依赖。源码 checkout 后可以直接运行：

```bash
uv run bcn --help
uv run bcn --version
```

## 启动

启动时必须显式选择已安装的 Channel 和 Runtime；项目不为二者提供默认值。以 WeCom 和
Codex 组合为例：

```bash
uvx --from 'git+ssh://git@github.com/bazaar-compute-node/bcn@main' bcn start \
  --channel wecom --runtime codex
```

后台进程可使用同一条命令关闭：

```bash
uvx --from 'git+ssh://git@github.com/bazaar-compute-node/bcn@main' bcn stop
```

稳定部署时建议进一步固定 tag 或 commit，避免运行版本随分支内容漂移。

## 启动配置

启动组合配置保存在固定路径 `~/.bcn/config.toml`：

```toml
[node]
channel = "wecom"
runtime = "codex"
storage = "sqlite"
audit = "logging"

[runtime]
model = "gpt-5.6-luna"
effort = "max"
```

命令行参数会覆盖对应的文件配置；`storage` 和 `audit` 的内置默认值分别为 `sqlite` 和
`logging`。`channel` 和 `runtime` 必须由命令行或配置文件显式提供，缺少任一项都在启动前
报错。未设置 `model`/`effort` 时由 App Server 使用默认值。
Unix 直接使用 socket 文件作为 daemon endpoint；Windows 使用 per-user named pipe 和 named
mutex，不需要 PID/lock 文件。SQLite 只保存 session、message、turn 等运行时状态。
