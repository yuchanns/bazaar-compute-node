# Task 11 最终验收记录（2026-09-27）

Task 6–10 的 Alpine 迁移已完成。Task 11 的代码审计、隔离浏览器回归及资源检查已完成；Task 5/11 的手机真机验收仍待设备。

## 验证结果

使用测试专用 BCS、真实 NodeApplication、临时数据库、TestChannel/TestRuntime 和独立 Edge。节点离线通过正常停止测试节点并等待实际心跳过期验证。测试实例已退出。

| 检查 | 结果 |
| --- | --- |
| 导航及历史恢复 | 34 项通过，另有 64 组路由/宽度/主题检查 |
| 创建、配置、删除和密码表单 | 47 项通过，包含实际创建、保存及节点读回 |
| 详情、长内容、成员、提醒及弹窗 | 63 项通过 |
| 空状态 | 32 组宽度/语言/主题组合及导航通过 |
| 补充加载、快速切换、种类、删除和离线 | 7 项通过 |
| morph、复制计时器释放、内容边界、旧版本及断网恢复 | 生命周期专项通过，记录 8 个状态 |
| 服务端既有测试 | tests/server 全部 53 项通过 |
| Ruff / 根目录 Pyright LSP | 通过；318 个文件零诊断 |
| 自有 JavaScript 语法 / git diff | 通过 |
| 本地资源与 wheel | 5 个 JS 资源来源/hash 核对通过；wheel 中 7 个必需静态资源与工作树一致 |

布局覆盖 320/390/430/768/959/960/1280/1440px、中英文、浅深色。移动端采用浏览器 mobile/touch 模拟，并改变短视口、横屏及恢复后的高度。最终回归继承 Task 8–10 已通过的多卡片错误保留、异步请求取消、长会话分页补偿和目录复用检查。

## 审计结论

[交互清单](alpine-interactions.md) 记录各组件和保留浏览器 API 的用途。模板已无可执行内联 script、hx-on 或原生 on* 处理器，纯 JSON 初始数据保留。官方 Focus/Collapse 与 htmx alpine-compat 管理对应交互和 morph 生命周期；自有计时器及请求有销毁处理。Markdown 内容保留文本边界。初始表单 JSON 仅包含允许编辑字段、凭据存在标记及环境变量名称。

本任务补齐已有 htmx 的来源/hash 记录，清理表单模板中失效的别名与摘要标记。详情参见 `src/bazaar_compute_server/resources/static/THIRD_PARTY.md`。

## 尚待验证

当前未连接可调试手机，亦无 adb/idevice 工具。真实软键盘、浏览器地址栏伸缩、安全区及系统横竖屏行为仍待真机验证，桌面模拟不能替代。Claude Code 可用配置受既有 SSH 连接环境限制；已验证不可用状态。以上项目没有标记为通过。

## 实现截图索引

以下 36 项对应原设计页面/状态。截图均来自最终隔离实例，保存在执行工作区 `artifacts/bcs-mobile-task11/`；`screens.json` 提供机器可读索引。关键截图直接在开发线程交付。连接指引内仅有已销毁测试实例的临时接入信息。

| 编号 | 页面/状态 | 截图相对路径 |
| --- | --- | --- |
| 01 | 登录 | `forms/01-login.png` |
| 02 | 智能体列表 | `navigation/01-agents.png` |
| 03 | 会话列表 | `details/03-contacts.png` |
| 04 | 待审核列表 | `details/04-requests.png` |
| 05 | 聊天 | `details/05-chat.png` |
| 06 | 审核 | `details/06-review.png` |
| 07 | 会话信息 | `details/07-contact-profile.png` |
| 08 | 删除联系人 | `details/08-remove-contact.png` |
| 09 | 智能体配置 | `forms/12-config-en-390.png` |
| 10 | 技能 | `details/10-skills.png` |
| 11 | 工作区 | `details/11-workspace.png` |
| 12 | 状态 | `details/12-status.png` |
| 13 | 活动 | `details/13-activity.png` |
| 14 | 删除智能体 | `forms/13-remove.png` |
| 15 | 电脑列表 | `navigation/19-computers.png` |
| 16 | 电脑详情 | `navigation/20-computer.png` |
| 17 | 添加电脑 | `forms/02-enrol.png` |
| 18 | 连接指引 | `forms/03-enrolled.png` |
| 19 | 删除电脑 | `supplement/19-remove-computer.png` |
| 20 | 向导基础 | `forms/04-basic.png` |
| 21 | 向导渠道 | `forms/05-channel-telegram.png` |
| 22 | 向导运行时 | `forms/06-runtime-codex.png` |
| 23 | 向导摘要 | `forms/23-summary.png` |
| 24 | 渠道类型 | `supplement/24-channel-kinds.png` |
| 25 | 设置 | `navigation/25-settings.png` |
| 26 | 外观 | `supplement/26-appearance.png` |
| 27 | 密码 | `forms/14-password.png` |
| 28 | 节点离线 | `supplement/28-offline-status.png` |
| 29 | 空列表 | `empty/zh-CN-light-390.png` |
| 30 | 错误页 | `details/30-error.png` |
| 31 | 登录错误 | `details/31-login-error.png` |
| 32 | 密码错误 | `details/32-password-error.png` |
| 33 | 运行时类型 | `supplement/33-runtime-kinds.png` |
| 34 | 飞书 | `forms/05-channel-lark.png` |
| 35 | 企业微信 | `forms/05-channel-wecom.png` |
| 36 | 运行时配置 | `forms/06-runtime-codex.png` |
