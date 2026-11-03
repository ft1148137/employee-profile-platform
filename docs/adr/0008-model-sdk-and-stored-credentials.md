# ADR-0008：模型 SDK 与已保存凭据

状态：采用（NERE-55，D5 11-03 修订）。

模型调用统一经过 `app.domains.model.client.chat_json`，使用 LiteLLM SDK
1.106.6；不启用代理、回调和消息日志。SDK 延迟导入，远程价格表和 Anthropic
配置读取在导入前关闭。Ruff TID251 限制其他业务模块直接导入 SDK。升级必须
单独提交并经人工审阅，精确版本和制品哈希保存在 uv.lock，Dependabot 不自动升级。

选择此版本是因为它是 D5 指定的候选维护版本，包含 1.106.5 的修复；避开
1.82.7、1.82.8 和 1.107.0。核对了官方
[事故公告](https://docs.litellm.ai/blog/security-incident-oct-21-2026)和
[版本说明](https://docs.litellm.ai/release_notes/v1.106.6/v1-106-6)，并检查实际 wheel
和安装文件没有 `.pth`、`sitecustomize.py`、`usercustomize.py`。这不替代以后的升级审查。

Jira 和模型共用 Fernet 加密工具，用 HKDF-SHA256 从 SECRET_KEY 派生密钥，
固定用途标识为 `employee-profile/stored-credentials/v1`。不增加部署密钥。
更换 SECRET_KEY 后原凭据不可恢复，管理员须重新填写。密文只在内部读取；
公共设置只返回 has_api_key。设置接口的校验错误删除输入内容，避免 FastAPI
默认错误响应回显密钥。审计只记配置和服务主机名，不记录密钥或完整 URL。

客户端自己控制重试；输出始终经过调用方 Pydantic 模型验证。鉴权及配置错误
立即失败；限流保留 Retry-After 对应时间供起草任务暂停使用。关闭模型时不调用 SDK。
本地模型及兼容服务允许访问内网；仅企业管理员可以修改设置。ChatGPT 订阅连接
由 NERE-69 实现，本任务拒绝该类型的写入。

NERE-56 的 Jira 连接也沿用上述加密和校验错误处理。每个企业最多一个 Jira
连接；Cloud 和 Data Center 适配器共享标准化结果，只在认证、请求和分页上分开，
供后续 Worker 按页同步。仅请求约定的最小字段，description 由管理员选择。
允许内网站点及上下文路径，不跟随重定向；更换部署类型、认证方式或规范化后的
站点地址必须重新填写凭据，避免旧凭据发往新地址。更换部署类型或地址清空同步
水位。连接测试先取当前账号，再搜索一条；错误只返回固定中文说明，不透传上游
响应正文。注入的 HTTP 客户端由调用方关闭，自建客户端由适配器关闭。
