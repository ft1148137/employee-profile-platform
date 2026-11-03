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

涉及新组件、协议或方案取舍时，随对应 PR 提交 ADR。文件名使用 `NNNN-短标题.md`，连续编号，不重复使用；记录背景、决策及其收益、代价与风险。
