# OIDC 恢复页面与独立会话时长

用户要求恢复登录后回到原页面，并按 OIDC 登录方式配置有效期，默认 10 分钟。

## 已确认的实现依据

- `gate._to_login` 当前没有携带原 URL；`OIDCLoginPages.callback` 固定跳 `/agents`。
- htmx 提供 `HX-Current-URL`；自有 `poll.js` 使用 fetch，需要补发浏览器当前 URL。
- OIDC 临时 nonce、PKCE verifier、提供方 ID、回调地址、恢复身份及返回地址保存在进程内存 OrderedDict，随机 state 为键；有效期 600 秒、容量上限 1024 条，每次发起淘汰过期项和最旧未使用项，回调同步校验有效期、提供方及浏览器绑定后 pop。重启丢弃未完成登录，由用户重新发起。按用户最终确认保留原有短期 HttpOnly cookie 绑定浏览器，内存事务保存其哈希。入口通过 Pydantic 校验：使用现有依赖 yarl.URL 解析及编码，拒绝协议和 authority，通过 pathlib.PurePosixPath 检查单根绝对路径，返回 URL 序列化结果以保留查询参数、锚点和安全编码。参考：https://yarl.aio-libs.org/en/stable/api/ 。
- `Sessions` 当前统一使用服务配置的有效期。改为签名 cookie 内记录到期时间，OIDC 签发时使用提供方分钟数，浏览器 Max-Age 同步使用该值。
- 提供方表单使用现有 Pydantic、模板和中英文文案；分钟数为正整数，默认 10。配置修改从下一次登录或恢复生效。
- 新增 v07 migration 保存提供方时长并删除临时事务表，保留全部已发布迁移；删除对应存储接口和实现。

## 任务

- [x] 完成返回地址、提供方时长、签名会话、配置界面与持久化；使用真实 Keycloak 和浏览器验证自然过期、普通访问及后台轮询恢复到原页面、配置保存和会话时长；运行 Ruff、相关测试及根目录 Pyright 后提交本地结果供 review。

## 验证结果

- 服务端测试：57 项通过。
- 真实 Keycloak 浏览器验证：提供方一分钟有效期自然到期；服务端拒绝过期会话；应用重启后重新发起恢复；普通访问恢复原路径与查询参数；后台轮询恢复电脑页面及查询参数、锚点。
- 真实 Dex 浏览器验证：桌面和手机配置页面保存 25 分钟，重新加载保留，实际登录 cookie 使用对应有效期；内存缓存容量限制调整后再次通过。
- 全仓 Ruff 检查及格式检查、根目录 Pyright、diff 检查通过。
- 本地改动留在 `fix/20260929-oidc-resume-session`，待 review。

会话 cookie 新增签名到期时间；升级后旧会话需要重新认证一次，OIDC 可使用已有恢复提示自动发起。
