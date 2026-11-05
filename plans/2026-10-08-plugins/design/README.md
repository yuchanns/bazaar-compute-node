# server 页面效果图

server 本体、登录与功能模块页面的效果图，实现时以此对照。桌面为 1440×900，手机为 390×844，每页有夜市与晨市两张。

## 页面

| 页面 | 夜市 | 晨市 | 源文件 |
| --- | --- | --- | --- |
| 登录：账号密码 | [夜市](screens/desktop/login-night.png) | [晨市](screens/desktop/login-day.png) | `src/login.html?view=password` |
| 登录：第三方登录 | [夜市](screens/desktop/login-third-night.png) | [晨市](screens/desktop/login-third-day.png) | `src/login.html` |
| 总览 | [夜市](screens/desktop/overview-night.png) | [晨市](screens/desktop/overview-day.png) | `src/overview.html` |
| 智能体 | [夜市](screens/desktop/agent-night.png) | [晨市](screens/desktop/agent-day.png) | `src/agent.html` |
| 会话 | [夜市](screens/desktop/chats-night.png) | [晨市](screens/desktop/chats-day.png) | `src/chats.html` |
| 新建智能体 | [夜市](screens/desktop/new-agent-night.png) | [晨市](screens/desktop/new-agent-day.png) | `src/new-agent.html` |
| 电脑 | [夜市](screens/desktop/computers-night.png) | [晨市](screens/desktop/computers-day.png) | `src/computers.html` |
| 设置：登录方式 | [夜市](screens/desktop/settings-login-night.png) | [晨市](screens/desktop/settings-login-day.png) | `src/settings-login.html` |
| 手机：登录 | [夜市](screens/phone/login-night.png) | [晨市](screens/phone/login-day.png) | `src/mobile.html?screen=login` |
| 手机：第三方登录 | [夜市](screens/phone/login-third-night.png) | [晨市](screens/phone/login-third-day.png) | `src/mobile.html?screen=login-third` |
| 手机：总览 | [夜市](screens/phone/overview-night.png) | [晨市](screens/phone/overview-day.png) | `src/mobile.html?screen=overview` |
| 手机：设置 | [夜市](screens/phone/settings-night.png) | [晨市](screens/phone/settings-day.png) | `src/mobile.html?screen=settings` |
| 手机：外观 | [夜市](screens/phone/appearance-night.png) | [晨市](screens/phone/appearance-day.png) | `src/mobile.html?screen=appearance` |

晨市在地址后加 `theme=day`。

## 源文件

- `tokens.css` 是定稿设计的 token 与组件；`flat.css` 把其中的渐变与发光改为纯色，实现以覆盖后的样子为准。
- `scene.css` 是登录页插画的配色：`--scene-*` 由主题给出，灯笼、棚子与货物取主题的五种主色，暗面与亮面由它们混出。
- `scene.py` 按曲线生成插画的 SVG；修改后运行 `python3 src/build.py`，写回 `login.html` 与 `mobile.html`。
- `awning.js` 画摊位卡片顶上的棚子，与插画中的棚子同一画法；`tents.js` 与 `ui.js` 中的 MARK 是品牌标识。
- `logos.js` 是渠道与运行时的品牌图标，来源与许可写在各条目中。
- `render.sh 页面 输出 [宽 高]` 以 headless Chromium 截取 1 倍图，手机页传 `390 844`。页面从 Google Fonts 取字体，渲染时需要联网。
