# Bazaar Compute Node

## 本地开发

项目使用 uv 管理 Python 环境和依赖。源码 checkout 后可以直接运行：

```bash
uv run bcn --help
uv run bcn --version
```

## 一行启动

使用者无需手动 clone 仓库，直接执行：

```bash
uvx --from 'git+ssh://git@github.com/bazaar-compute-node/bcn@main' bcn start \
  --channel dummy --runtime dummy
```

后台进程可使用同一条命令关闭：

```bash
uvx --from 'git+ssh://git@github.com/bazaar-compute-node/bcn@main' bcn stop
```

稳定部署时建议进一步固定 tag 或 commit，避免运行版本随分支内容漂移。
