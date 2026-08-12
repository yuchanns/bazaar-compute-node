# PyPI Release Publishing

## 目标

提供一个名称为 `Release`、可从 GitHub Actions 页面手动运行的 workflow。维护者只
输入不带 `v` 的稳定版本号，例如 `0.1.3`；workflow 更新仓库版本、创建
`v0.1.3` tag，并发布 `bazaar-compute-node` 到 PyPI。

这里的 `Release` 仅是 CI workflow 名称；流程不创建 GitHub Release 对象。

## 约束

- workflow 入参是唯一的发布版本来源，只接受 `X.Y.Z`；拒绝 `vX.Y.Z`、前后空格、
  缺失分段和 prerelease/post/dev 版本，不做兼容转换。
- workflow 只能从 `main` 运行，新版本必须严格大于仓库当前版本，远端不得已存在指向
  其他 commit 的同名 tag。
- CI 必须把新版本写回 `pyproject.toml`，同步更新 `uv.lock`，并提交到 `main`；不能只
  修改 runner 中的临时构建元数据。
- 不在 GitHub 保存长期 PyPI token；发布只使用 OIDC Trusted Publishing。
- prepare job 不获得 OIDC 权限；只有受 GitHub environment `pypi` 约束的 publish job
  获得 `id-token: write`。
- PyPI 上传失败时保留版本 commit，但不把本地 annotated tag 推到远端；同版本 workflow
  必须能安全重试，不回滚或重写 Git 历史。
- 所有重试必须从同一个 release commit 生成 byte-identical artifacts，避免 PyPI 部分
  上传后因同名文件内容变化而无法继续。
- 不发布当前已有的 `v0.1.2`；workflow 只处理合入后的新版本。

## 设计

### 单一运行时版本来源

当前包版本同时硬编码在 `pyproject.toml` 和
`src/bazaar_compute_node/__init__.py`。CI 只更新前者会造成 PyPI 元数据已经升级，但
`bcn --version` 和 runtime client 仍报告旧版本。

移除源码中的第二份静态版本，让 `__version__` 通过 `importlib.metadata` 读取已安装
distribution 的版本。这样 CLI、runtime client、wheel 和 source distribution 都共享
`pyproject.toml` 的包元数据；Release workflow 只需执行
`uv version <version> --no-sync`，由 uv 同时更新 `pyproject.toml` 与 `uv.lock`。

### 手动 Release workflow

新增 `.github/workflows/release.yml`，workflow `name` 为 `Release`，只监听
`workflow_dispatch`，提供必填字符串入参 `version`。workflow 使用全局
`pypi-release` concurrency 且不取消正在运行的发布，避免两个版本同时修改 `main`。

第一阶段在获得 `contents: write` 的 prepare job 中执行：

1. 要求触发 ref 为 `refs/heads/main`，checkout 最新 `main` 并 fetch 完整 tag 历史；
2. 校验输入严格符合 `X.Y.Z`，按数值比较确认它大于当前版本，并确认
   `v<version>` 尚未被其他 commit 使用；
3. 安装固定版本的 uv 与 Python 3.14，先在当前版本运行项目测试和静态检查；
4. 执行 `uv version <version> --no-sync`，检查改动范围只能包含
   `pyproject.toml` 与 `uv.lock`；
5. 提交版本更新到 `main`，commit message 为 `Release v<version>`；以该 commit 的
   timestamp 设置 `SOURCE_DATE_EPOCH`，让同版本重跑生成 byte-identical archives；
6. 以 `uv build --no-sources` 构建 wheel 与 source distribution；分别从两个真实
   产物创建隔离环境，断言 `bcn --version`、import metadata 与输入版本完全一致；
7. 在 runner 中创建 annotated tag `v<version>`，记录 tag object identity，再把经过验证的
   `dist/` 作为不可变 workflow artifact 交给 publish job。构建或 smoke test 失败时保留
   version commit，但不向远端创建 tag。

第二阶段 publish job 绑定 GitHub environment `pypi`，下载上一阶段 artifact，使用 PyPA
官方 `pypa/gh-action-pypi-publish` action 与 Trusted Publishing 上传 PyPI，并开启其
`skip-existing` 支持同版本失败重跑。该 job 不安装自定义发布 CLI、不获得仓库写权限，
也不创建或修改 GitHub Release。

第三阶段 finalize job 仅在 publish 成功后运行。它 checkout 精确的 release commit，按
prepare 阶段记录的 timestamp 重新生成同一个 annotated tag object，核对 object identity
和 commit 指向后才推送 tag。该 job 只有 `contents: write`，不获得 OIDC 权限；因此 PyPI
失败不会留下远端 tag，tag 推送失败则可通过同版本重跑补齐。

同版本重跑采用显式状态机：如果 `main` 已是输入版本，则验证 HEAD 是对应的 release
commit；tag 缺失时在 runner 中补建，存在时必须是指向该 HEAD 的 annotated tag，然后
重新构建并重试上传。任何已存在 tag 指向其他 commit、或 tag object identity 不一致时
都必须失败。可重复构建保证同一 release commit 的重跑产物 byte-identical；官方 action
跳过 PyPI 已存在的同名文件并上传缺失文件，不覆盖既有分发文件。

workflow 中所有第三方 actions 使用完整 commit SHA 固定，并在行尾标注对应 release
版本，避免可变 tag 改写发布链路。

### GitHub 签名的自动版本提交

首次 `0.1.3` 发布证明 runner 中普通 `git commit` 即使使用
`github-actions[bot]` name/email 并通过 `GITHUB_TOKEN` push，也只会生成 unsigned commit。
身份字段不是密码学签名，GitHub API 返回 `reason=unsigned`。

后续发布使用固定完整 SHA 的官方 `actions/github-script`，通过其预认证 Octokit client
调用 GitHub GraphQL `createCommitOnBranch` mutation。prepare job 使用当前
`GITHUB_TOKEN`，以触发时的 main HEAD 作为 `expectedHeadOid`，只提交 base64 编码后的
`pyproject.toml` 与 `uv.lock`，且不提供自定义 author、committer 或 signature。GitHub
官方保证该 mutation 在受支持的平台自动由 GitHub 签名；workflow 必须检查返回的
`signature.isValid=true` 和 `wasSignedByGitHub=true`，否则停止发布。mutation 原子更新
main 后，runner fetch 并 checkout 返回的精确 commit，再进入现有可重复构建与 tag 流程。

现有 `0.1.3` 版本提交保持原样；为补签而改写已发布 main/tag 历史不在本任务范围内。

### 文档与首次配置

新增最小 package smoke test，只验证真实安装产物的入口、distribution 名和版本，不引入
mock、fake 或网络依赖。README 只增加面向使用者的 PyPI 安装与升级方式；发布维护细节保留
在 plan 与 workflow 中。

首次上线前需要在 PyPI 配置 pending publisher：project
`bazaar-compute-node`、owner `yuchanns`、repository `bazaar-compute-node`、workflow
`release.yml`、environment `pypi`；同时在 GitHub 仓库创建 environment `pypi`。

## Task 1：实现并验证 GitHub Actions 到 PyPI 的完整发布边界

- 将 runtime 版本来源改为 installed distribution metadata，并补充 CLI/client 版本
  一致性测试。
- 新增真实 wheel/source distribution smoke test，覆盖命令入口、import metadata 和
  workflow 输入版本。
- 新增名为 `Release` 的手动 workflow：裸版本校验、单调升级校验、测试、版本写回、
  构建、真实 artifact 验证、版本 commit、annotated tag 和 OIDC publish。
- 实现同版本安全重跑与 tag 状态不一致时 fail-closed，不删除或覆盖既有 tag/package。
- 更新 README，只说明面向使用者的 PyPI 安装与升级方式。
- 本地验证当前版本的 focused/full non-real-home tests、Ruff format/check、Pyright、
  compileall、`uv lock --check` 与 `git diff --check`。
- 在隔离临时 checkout 中模拟一个未发布的新版本，执行版本更新、lock 校验、
  固定 `SOURCE_DATE_EPOCH` 后重复执行 `uv build --no-sources`，验证两次产物 SHA-256
  完全一致，并分别安装 wheel 与 source distribution 运行 smoke test；不 push commit、
  不创建 tag、不上传 PyPI。
- 使用 GitHub Actions workflow linter 检查 YAML 与 expression 语法，并核对所有 action
  引用均固定到完整 SHA。
