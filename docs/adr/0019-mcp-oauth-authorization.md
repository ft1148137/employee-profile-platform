# ADR-0019：MCP OAuth 授权服务器

状态：采纳。对应 NERE-98，延续 ADR-0003、ADR-0017；租户边界和拆分由 NERE-98 技术规划评论确定。

## 决策

复用锁定的 `mcp==2.3.0`（MIT）授权服务器组件，不新增依赖。仅提供授权码 + S256 PKCE、`employee` scope，访问令牌有效一小时，刷新令牌有效三十天并轮换。浏览器界面、员工连接说明和 Inspector 验证在 NERE-111 交付；此 PR 由后端协议测试验证「允许」。

`oauth_client` 是部署级技术元数据，和 `user` 一样不带 `org_id`。动态注册发生在员工登录、选择企业前，不能相信客户端提供的企业标识。这张表没有员工数据。客户端密钥通过既有 `encrypt_secret` 加密保存：SDK 的 `ClientAuthenticator` 需要解密后用 `compare_digest` 比较，不能直接换成哈希。

员工允许后创建租户内 `oauth_grant`，授权码哈希放在授权行；刷新令牌独立保存哈希，以复合外键关联授权。访问令牌复用 `personal_access_token`，通过检查约束要求 `client_kind=oauth` 与关联授权同时存在。授权的企业、账号和员工只从服务端校验后的 Principal 写入，请求体拒绝 `org_id/user_id/employee_id`。网页登录员工用 `?org_id=` 选择当前企业，沿用 `OrgPrincipalDep` 校验成员身份，非成员返回 404；外部客户端的注册、授权和换令牌不选择企业。MCP 和 OAuth 共用账号、改密、员工关联、可见范围、企业开关检查；OAuth 还按注册名称检查企业客户端允许列表。OAuth 的 MCP 会话、来源与允许列表使用授权时的注册名称，避免传输库的 `clientInfo.name` 不同导致合法客户端被拒绝；个人令牌继续用原有初始化名称。注册名称仍由客户端自报，不构成安全边界。

授权请求用 `aud=oauth-consent`、十分钟有效的签名 JWT 传给前端，无待授权请求表。JWT 不是访问令牌，不能兑换或调用 MCP；同意接口仍要求网页登录身份、员工身份和企业准入。

授权码与刷新令牌均通过带有效期、未使用条件的 `UPDATE ... RETURNING` 原子消费。对同一授权加行锁串行化兑换、轮换和撤销，防止并发发放。重放已消费的凭据会持久化整链撤销，然后返回 `invalid_grant`；异常回滚不能撤销这次安全操作。撤销同步更新授权、所有访问令牌和未使用的刷新令牌，所以 `/mcp` 继续走 `token_principal`，不需要另设认证入口。既有员工令牌撤销接口遇到 OAuth 令牌也撤销整链，避免只撤销访问令牌后还能刷新；它与轮换保持相同加锁顺序。刷新时删除同一授权中过期的访问令牌，不删除原始证据。

SDK 2.3.0 的 token handler 不把授权码/刷新请求的 `resource` 传给 provider，因此在 token 路由外校验显式资源只能是配置的站点 `/mcp`，缺省沿用授权绑定的资源。地址从 `FRONTEND_HOST` 生成，不相信 Host 头。元数据同时提供根发现地址和带 `/mcp`、`/oauth` 的规范发现地址。401 challenge 指向受保护资源元数据。SDK 还要求 Basic 客户端在表单重复 `client_id`，外包层在缺省时从 Basic 头解出标识，再由 SDK 校验完整凭据。公共客户端撤销时，外包层补 SDK 要求的空 `client_secret` 字段，仍由 SDK 验证注册的认证方式。

动态注册按请求连接 IP 和一小时窗口限流，使用 PostgreSQL advisory transaction lock 在多个 API worker 之间串行计数，默认十个，可通过 `OAUTH_REGISTER_MAX_PER_IP_HOUR` 配置。不引入 Redis。授权码、刷新令牌和访问令牌只存 SHA-256 哈希；审计只记录允许、拒绝和撤销的对象标识，不写凭据或请求正文。

## 限制

只处理注册、授权、兑换和撤销，不实现 RFC 7592 客户端管理、未使用客户端清理或授权的绝对寿命上限。刷新后令牌指纹发生变化，客户端须重新初始化 MCP 会话。OAuth 的生产入口要求 HTTPS，本机回环 HTTP 可用于开发。安装脚本仍允许通过服务器 IP 使用普通 HTTP 网页；这种模式不能启用 OAuth 协议，发现与授权入口返回 503 并提示配置 HTTPS，不能因为 SDK 校验 issuer 而使整个网站无法启动。浏览器入口未在本 PR 提供，端到端员工使用要等 NERE-111。
