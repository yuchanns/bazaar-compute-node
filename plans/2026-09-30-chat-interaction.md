# 聊天交互和资料展示调整

## 需求

点击跳到底部时平滑滚动；重复点击已打开的聊天时保持当前页面；提醒系统消息省略群目标；聊天资料省略目标 ID。

## 调研与实现

- [MDN Element.scrollTo](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollTo) 支持 `smooth` 和 `instant`。`history.html` 的按钮内联调用原生滚动，用户设置减少动态效果时使用 instant。初次进入、消息追加和上翻历史现有定位继续由 `history` 组件控制。
- [Alpine x-on](https://alpinejs.dev/directives/on) 的 capture 修饰符先于冒泡监听执行。现有 `agentLink` 已使用此方式阻止请求。`chat.html` 提供实际打开的 thread；联系人点击在 capture 阶段比较该 thread，相同时阻止普通点击，保留 Ctrl/Meta/Shift/Alt 打开链接的浏览器行为。以实际详情判断，加载失败后可重试；切换其他聊天和手机返回后重新进入正常请求。
- `serialize_message` 已输出 `system_message_kind` 和 `canonical_target`。`history._turns` 只在提醒消息展示正文中去掉首个精确的 ` — canonical_target — ` 段，既有提醒也直接获得简洁显示。正文其余部分保留提醒标题、类型和下次时间。
- `profile.html` 的副标题保留 Channel 和聊天类型，去掉目标 ID。

## 验收任务

- [x] 完成四处调整，在独立配置、临时数据库和 TestChannel 节点的真实桌面/手机浏览器检查平滑滚动、减少动态效果、重复点击、切换和返回、提醒与资料展示；通过 Ruff、根目录 Pyright 和相关既有测试后提交 review。

验收通过：1366 与 390 像素宽度的原生滚动均记录到中间动画帧并到达底部；减少动态效果时立即到达底部；重复点击保留详情 DOM 与滚动位置；切换、断网失败后的重试、手机返回与重开正常。真实提醒及桌面/手机资料截图已检查。既有联系人与 review 测试 4 项通过，Ruff、根目录 Pyright、diff 检查通过。隔离节点与浏览器已关闭。
