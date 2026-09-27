# Alpine 交互清单

本分支使用本地 Alpine 3.17.4、Focus、Collapse 和 htmx 4.0.0 官方 alpine-compat。Alpine 管理组件编辑状态；htmx 负责服务端 HTML、请求、轮询、URL 和历史恢复。

| 页面或区域 | 实现 | 生命周期与原生 API 用途 |
| --- | --- | --- |
| shell / login / 不可达提示 | `application`、`connection`、`x-if/x-show` | htmx 事件更新 stale/unreachable，stale 时阻止新请求；刷新由 Location 完成 |
| 接入电脑、向导、删除确认、会话抽屉 | `dialog`、`opener`、Focus `x-trap.inert.noscroll` | Alpine 自动解绑 Escape/outside 事件；嵌套 trap 逐层返回焦点 |
| 会话成员 | `conversation`、带 identity key 的 `x-for/x-text` | 在 htmx 更新后读取已加载历史中的身份；头像 HTML 仅来自服务器 identicon |
| 创建及配置 | `agentForm`、`x-model`、带稳定 key 的卡片/环境变量数组 | 服务端白名单 JSON 初始化；失败 morph 保留当前状态，成功从节点读回重新初始化 |
| 向导与摘要 | 响应式 step、有效性和派生 summary | 校验使用原生 checkValidity/reportValidity；Focus 在 `$nextTick` 后定位；摘要只显示凭据状态和变量名称 |
| kinds / model / effort | htmx 片段、模型数组、`$watch` 和状态绑定 | DOMParser 读取现有 options 协议；AbortController 取消自有模型请求，htmx request.abort 取消种类请求；表单内按 kind 去重 |
| profile 标签与加载状态 | `profileTabs`、`:class/x-show` | 只由当前请求清除 loading；完整路由恢复后读取服务器标签状态 |
| 联系人选中 | `selection`、`:class` | 服务器给出初始选中项；点击更新本地高亮；轮询携带当前选中项 |
| 齿轮入口 | `navigation`、`:class` | 嵌在行链接内，需要阻止行的默认行为并通过 htmx.ajax 导航；busy 来自组件状态 |
| 活动卡 | `activity`、鼠标事件、`:style` | 保留原悬停方式；offsetWidth/Height 和视口坐标只用于定位卡片 |
| 文件夹 | `directory`、`x-show/x-collapse`、`:aria-expanded` | 首次展开请求目录，后续复用；失败可以重试；reduced-motion 覆盖动画时长 |
| 聊天历史 | `history`、htmx 请求/更新事件、`$nextTick` | 用 scrollHeight/scrollTop 记录并补偿向前分页；阅读旧消息时保持位置，原已在底部时跟随追加 |
| 登录、密码 | `submission` 和共享绑定、`:disabled` | Alpine/htmx 初始化后开放提交；请求中禁用，完成或失败后恢复 |
| 外观设置 | Alpine change 事件 | 原生 requestSubmit 触发既有 htmx 提交 |
| 代码复制 | `clipboard`、`:class` | Clipboard API 复制；反馈计时器在 destroy 时清理 |
| Markdown 内容 | 原始 HTML 禁用，正文 span 和代码 pre 的 `x-ignore` | 可信复制按钮位于内容边界外；名字/文案通过转义或 x-text 展示 |

保留的 htmx `js:` 表达式只组装请求参数（时区、build、分页上限、当前选中项），触发过滤器区分行链接和齿轮按钮。服务端选择的路由、CSS 响应式层级和浏览器滚动仍各自承担原职责。

自有静态脚本只有组件注册入口 `alpine:init`；模板没有可执行内联 script、hx-on 或原生 on* 处理器。JSON script 仅提供表单初始数据。全局焦点数组、Tab 循环、手动表单事件分发、字符串克隆卡片、DOM 摘要构建和成员 MutationObserver 均已移除。未使用 history-cache 或持久化插件保存编辑数据。
