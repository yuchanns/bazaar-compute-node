# 导航加载反馈

点击导航后立即显示米字形加载提示，响应完成后切换页面。

## 调研结论

页面通过 htmx 4 请求并替换 #main；Alpine application 位于持久 body。
htmx:before:request 提供 detail.ctx.target，finally:request 在成功、失败和取消后清理。
使用 application 的 Set 跟踪目标为 #main 的请求，避免重叠请求提前隐藏提示。
提示放在 shell 中、#main 外，避免页面 morph 删除提示；浮层不拦截点击。
Lucide loader 使用同版本 lucide-static 1.46.0 官方 SVG，八条线按顺时针依次变亮。

## Task 1

- application/connection 记录主区域请求及 finally 清理；shell 添加带本地化名称的 status 提示和 aria-busy。
- 统一已有 loading 图标为 loader，CSS 添加逐线透明度动画及减少动态效果支持。
- 使用隔离真实 BCS 和浏览器，在弱网下验证桌面、手机导航即时反馈、完成/失败/取消清理及并行请求。
- 执行 Ruff、仓库根目录 Pyright 检查，完成后等待 review。

提示仅展示居中的 loader 图标，不添加底框、边框或阴影。

## Task 2

统一正常空状态为淡色 Lucide 图标和短文案：智能体与电脑列表、联系人与审核请求、消息历史、电脑内智能体、状态、动态、技能、工作区与提醒。复用 components.empty，增加可选图标及 id；保留 events-empty 供新事件移除。列表填满可用高度并居中；资料与抽屉使用紧凑间距。离线及错误反馈保留原语义和重试操作。使用现有 users、monitor、message-circle、user-plus、message-square-text、grid-2x2、folder、alarm-clock 图标。真实浏览器检查手机及桌面空列表、模板编译，执行 Ruff 和根目录 Pyright。

## 验证结果

Task 1、Task 2 已完成。真实隔离 BCS + Edge 验证手机/桌面空列表和弱网导航、完成/失败/取消清理、并行请求。全部 HTML 模板使用应用过滤器编译通过；Ruff check/format、根目录 Pyright 和 diff 检查通过。改动保留在主仓库 fix/navigation-loading-empty-states 分支工作区等待 review。
