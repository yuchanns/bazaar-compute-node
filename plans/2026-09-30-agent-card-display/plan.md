# Agent 配置卡片显示修复

## 目标

Channel 和 runtime 卡片的图标与名称垂直居中；打开已有 Agent 的配置时，runtime 名称旁展示当前安装的应用版本。

## 实现

- `agent_form.html` 的图标被 `x-show` 外层 span 包裹，嵌套 inline 元素产生额外基线高度。给外层添加 `kind-logo`，使用 inline-flex，并让卡片和类型选择按钮内的 SVG 以 block 布局消除基线空隙。
- 新增卡片从类型选择按钮获得版本，已有卡片的 `editor()` 数据只有配置字段。复用现有 `/kinds/runtime` 请求，在配置表单或向导进入 runtime / 确认步骤时加载一次；类型响应按钮通过 `data-version` 提供版本，`kindFinished()` 按 runtime kind 更新卡片显示状态。使用表单与步骤状态决定已有卡片的加载，避免保存后的 morph 初始化期间 DOM 暂未可见而漏掉请求。
- 版本继续由节点的 `read_runtimes()` 探测；节点已有缓存。页面不等待探测完成才展示编辑器，版本只存在于前端显示状态。

## 验收任务

- [x] 修复共享图标布局和已有 runtime 的版本加载，使用独立配置、临时数据库与 TestChannel 节点，在真实浏览器检查新建和编辑卡片、保存后重新打开、桌面与手机尺寸；通过 Ruff、根目录 Pyright 和相关既有测试，提供截图供 review。

验收覆盖 1366、390、320 像素宽度，Telegram、飞书、企业微信、Codex、Claude Code 图标与名称居中。实际 Codex 版本 `0.159.2` 在初次打开、连续三次保存刷新及重新打开时显示。相关既有测试 12 项通过，Ruff 检查与格式、根目录 Pyright、JavaScript 语法和 diff 检查通过。
