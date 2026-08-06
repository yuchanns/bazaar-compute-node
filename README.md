# Bazaar Compute Node

企业内部 Agent runtime 的 Python 基础项目。

## 本地开发

项目使用 uv 管理 Python 环境和依赖。源码 checkout 后可以直接运行：

```bash
uv run bcn --help
uv run bcn --version
```

## 一行启动

使用者无需手动 clone 仓库，可以让 uvx 从私有 Git source 拉取并执行：

```bash
uvx --from 'git+ssh://git@github.com/bazaar-compute-node/bcn@feat/bcn-uvx-bootstrap' bcn
```

这里的分发名是 `bazaar-compute-node`，命令名是 `bcn`，所以需要使用 `--from` 指定
分发 source。`uvx run` 不是该项目的启动形式；`uvx` 本身是 `uv tool run` 的别名。

当前基础框架位于 `feat/bcn-uvx-bootstrap`；合并到 `main` 后应将命令中的分支替换为
`main`，稳定部署时再进一步固定 tag 或 commit，避免运行版本随分支内容漂移。
