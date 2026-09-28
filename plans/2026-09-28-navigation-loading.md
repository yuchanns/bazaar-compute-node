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

## 验证结果

Task 1 已完成。真实隔离 BCS + Edge 验证弱网导航、完成/失败/取消清理、并行请求；Ruff check/format、根目录 Pyright 和 diff 检查通过。
