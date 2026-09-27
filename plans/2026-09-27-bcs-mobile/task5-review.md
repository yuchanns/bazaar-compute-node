# Task 5 浏览器验收与截图

2026-09-27，在专用临时目录运行真实 BCS 和 NodeApplication，使用 TestChannel/TestRuntime；浏览器为独立 Edge。真实外部渠道、模型及正式服务均未参与。

## 结果

- 导航 34 项，路由/宽度/主题 64 组。
- 共享表单 46 项，使用 mobile/touch 模拟，包含缩短视口和横屏。
- 详情 63 项，包含长内容、复制、成员/提醒、嵌套弹窗与错误状态。
- 合计 207 项通过。相关现有测试 24 项通过，Ruff、根目录 Pyright（318 文件，零诊断）、diff 检查通过。

发现的壳层重复执行与 autofocus 覆盖打开按钮问题已修复。专门验证多次浏览器历史恢复后反复打开/关闭弹窗，焦点能回到打开按钮。

## 随线程交付的本轮截图

附件 `task5-review-screens.zip` 包含本轮独立生成的截图及此记录：

| 图片 | 实际状态 |
| --- | --- |
| short-viewport.png | 390×400 向导，正文滚动后输入及底部操作可见 |
| landscape.png | 844×390 横屏向导，输入保留 |
| mobile-config-dark.png | 320px 中文深色共享配置 |
| desktop-config.png | 1280px 桌面实际保存配置后 |
| mobile-chat.png | 390px 长 Markdown、表格与代码 |
| mobile-drawer-dark.png | 390px 深色成员与提醒抽屉 |
| nested-confirm.png | 会话信息上打开删除确认 |
| history-dialog.png | 多次历史恢复后打开接入电脑弹窗 |

工作区 `artifacts/bcs-mobile-task5/` 保存 navigation/forms/details 的脚本、截图、JSON 结果及 Pyright 报告。短视口与横屏图片来自移动浏览器模拟，是真实页面渲染截图。

## 待验条件

真机软键盘、地址栏伸缩、安全区和系统旋转需要实际可调试手机。当前主机未发现连接手机，未完成这些验证。Claude Code 可用卡片仍需恢复既有 SSH 环境后验证。

Task 5 浏览器部分已完成；真机项继续待验。取得设备后补验结果应 amend 到同一任务提交。
