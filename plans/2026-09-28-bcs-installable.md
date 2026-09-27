# BCS 桌面与主屏幕安装

让用户通过浏览器安装 Bazaar，以应用图标启动独立窗口，继续在线使用现有 BCS 页面。

## 调研与实现

- [MDN 安装要求](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)：HTTPS（开发时 localhost 可用），manifest 提供 name、192/512 PNG icons、start_url 和 display。Service Worker 是离线能力的实现工具，安装本身使用 manifest 即可。
- [Apple Safari Web App](https://support.apple.com/en-ie/104996)：Mac Safari 支持添加到程序坞；iOS 使用分享菜单添加到主屏幕。提供 180px apple-touch-icon；manifest 的 standalone 指定独立窗口。
- 当前 `shell.html` 与 `login.html` 都只引用 favicon；`/static/` 已允许未登录访问，Starlette StaticFiles 提供资源。因此清单和图标全部放入现有 static 目录，由两个页面共同引用。
- `static/app.webmanifest` 使用稳定 `id: /`、`start_url: /`、`scope: /`、`name/short_name: Bazaar`、`display: standalone`，启动交给现有根路由及登录逻辑。所有路径使用站点绝对路径，避免清单位于 static 目录导致作用域或启动路径错误。
- 主题色采用既有侧栏色 `#3b1a1a`，启动背景采用画布色 `#f4f1ec`。两个 head 引用 manifest、apple-touch-icon 和 theme-color；登录页 viewport 加入 viewport-fit=cover，使用已有 safe-area 样式。
- 192/512 PNG 和 180px Apple 图标从现有 favicon.svg 栅格化，保留 Bazaar 招牌。使用不透明画布色背景，把 32 单位原图居中放在 48 单位画布中；图案位于 maskable 安全区域。清单两个 PNG 标注 `purpose: any maskable`。

## Task 1 — 完成安装元数据、图标与浏览器验证

1. 添加清单及三种尺寸图标，修改两个入口 head。
2. 用临时数据库和隔离的真实 BCS HTTP 服务，在 Edge 中检查登录页、登录后页面的 manifest 解析结果、图标解码尺寸、公开资源 MIME 和启动重定向。
3. 使用浏览器 CDP 检查 manifest/installability，记录浏览器实际反馈；使用打包结果确认静态资源被收入 wheel。
4. 运行 Ruff、仓库根目录 Pyright 与 diff 检查，提交推送，报告结果供 review。真实 iOS/Android/macOS 的系统安装操作需要对应设备验收，报告中明确验证范围。

### Task 1 结果

完成清单、192/512 PNG、180px Apple 图标及两个页面入口。使用临时数据库的真实 BCS 服务和 Edge 独立持久化配置验证：登录前后 `Page.getAppManifest` 正常解析，`Page.getInstallabilityErrors` 均返回空列表；三种图标解码尺寸正确，清单返回 `application/manifest+json`，根启动路径按登录状态进入登录页或智能体列表。wheel 构建包含四个新增静态资源。Ruff、根目录 Pyright 和 diff 检查通过。系统级安装及真实 iOS/Android/macOS 设备体验待设备验收。
