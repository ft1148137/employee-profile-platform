# 架构决策记录

| ADR | 决策 | 状态 |
| --- | --- | --- |
| [0001](0001-tenancy-and-core-objects.md) | 租户边界、核心对象、证据追加和画像快照 | M0 已采用 |
| [0002](0002-worker-and-compose-layout.md) | PostgreSQL Worker 与 Compose 分工 | 已采用 |
| [0003](0003-authz-and-tenant-context.md) | 租户上下文与基础角色权限 | 已采用 |
| [0004](0004-login-and-deployment-scope.md) | 登录与部署范围 | 已采用 |
| [0005](0005-login-session-and-rate-limits.md) | 登录会话与限流 | 已采用 |
| [0006](0006-manager-descendant-scope.md) | 部门经理的管辖范围包含下级部门 | 已采用 |
| [0007](0007-mock-jira-service.md) | 数据集驱动的模拟 Jira | 已采用 |
| [0008](0008-model-sdk-and-stored-credentials.md) | 模型 SDK 与已保存凭据 | 已采用 |
| [0009](0009-profile-change-requests.md) | 画像请求与正式判断分离、审核状态推算及迁移 | 已采用 |
| [0010](0010-source-record-ingestion.md) | 来源记录、未关联账号与证据粒度 | 已采用 |
| [0011](0011-chatgpt-subscription-sessions.md) | ChatGPT 订阅会话与开发登录隔离 | 已采用 |
| [0012](0012-resumable-source-sync.md) | 同步入队、分页事务和断点恢复 | 已采用 |

| [0013](0013-model-initial-drafting.md) | 模型起草白名单、输出校验和限流续跑事务 | 已采用 |
| [0014](0014-pi-agent.md) | Pi 调用边界、订阅凭据与会话并发 | 已采用 |

涉及新组件、协议或方案取舍时，随对应 PR 提交 ADR。文件名使用 `NNNN-短标题.md`，连续编号，不重复使用；记录背景、决策及其收益、代价与风险。

- [ADR-0015：邮箱登录和旧账号迁移](0015-email-login.md)
- [ADR-0016：能力词典换版与历史判断](0016-capability-dictionary-versions.md)
- [ADR-0017：员工 MCP 传输与个人令牌认证](0017-mcp-transport-and-token-auth.md)

- [ADR-0019：MCP OAuth 授权服务器](0019-mcp-oauth-authorization.md)
- [ADR-0021：ChatGPT 订阅支持目录外模型 ID](0021-codex-model-ids.md)
- [ADR-0023：审批路由、临时读取权限与原子发布](0023-review-and-publish.md)
- [ADR-0026：限时代理审批](0026-review-delegation.md)
- [ADR-0027：CI 隔离并行与异步自动合并](0027-ci-and-asynchronous-merge.md)
- [ADR-0028：管理助手只读功能登记表](0028-admin-function-registry.md)
- [ADR-0029：管理助手会话与共享对话准入](0029-admin-assistant-conversations.md)
- [ADR-0030：管理助手的普通变更集](0030-admin-change-sets.md)
- [ADR-0034：管理助手的文件预览与模型摘要](0034-admin-assistant-file-previews.md)
