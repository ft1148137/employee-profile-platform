# ADR-0017：员工 MCP 传输与个人令牌认证

状态：采纳。对应 NERE-105，延续 ADR-0003 和员工服务层约定。

## 决策

同一 FastAPI 进程在 `/mcp` 提供官方 Python SDK `mcp==2.3.0`（MIT）的 Streamable HTTP 端点。从 `EMPLOYEE_TOOLS` 生成定义，只允许员工工具；角色和范围仍调用 `get_org_principal` 计算。`token_principal` 供后续 OAuth 入口复用。

每次 HTTP 请求校验个人令牌的 SHA-256 哈希、有效期、撤销状态、账号状态、改密时间、员工关联和企业开关，更新最后使用时间。认证失败复用登录尝试表、事务锁和 IP 阈值，不存令牌正文。客户端名称只是自报标识，企业允许列表不是身份安全边界。

部署有 4 个 API worker。SDK 进程内会话无法跨 worker 使用，因此选择 SDK 无状态传输，外层签名 `Mcp-Session-Id` 绑定令牌指纹、初始化客户端名和一小时有效期。各 worker 使用同一部署密钥验证，不增加数据表、共享缓存或代理粘性路由。会话标识不代替 Bearer 认证。只提供员工工具的请求响应，不提供服务端主动通知、恢复事件流或会话 DELETE 撤销；客户端断开即可，吊销个人令牌即时阻止后续请求。支持先 `initialize` 再调用工具的客户端；不实现新协议的无初始化发现入口。

调用的 `via=mcp` 和 `client_name` 由服务端注入。提交仍要求预览修订号，沿用原服务的事务、restricted 自述证据、收件箱通知和撤回逻辑。审计只记工具名、JSON-RPC 请求编号和客户端；参数验证错误不回显输入，未知异常统一脱敏。

## 部署与限制

`install.sh` 选择的两个 HTTPS overlay 都将整个站点路由到 backend；`/mcp` 在前端兜底之前注册。Traefik 未启用 buffering 中间件；显式设置 response forwarding flush interval 为立即刷新，防止后续流式输出受代理缓冲影响。

外部客户端需要能访问并信任证书的 HTTPS 地址。签名会话过期或部署密钥轮换后需要重新连接。OAuth、经理和管理员工具另行实现。
