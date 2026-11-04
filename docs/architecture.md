# 架构与扩展位置

M3 支持管理员接 Jira/CSV、确认账号对应，再用已启用的 Pi 模型起草待本人过目的请求；没有启用模型时不起草。员工通过表单、JSON、MCP 和内置机器人提交候选。FastAPI 提供 Web/API，React 构建产物由同一进程托管；所有业务数据和任务队列共用 PostgreSQL。演示企业仍由合成种子建立；经理审批与正式发布入口在 M4。

## 进程与模块

```mermaid
flowchart LR
    browser[浏览器 / React] --> backend
    subgraph backend[backend · Web/API]
        routes[app/api/routes] --> authz[app/authz · Principal 与策略]
        routes --> domains[app/domains · 组织/工作/能力/证据/画像/审计]
        domains --> authz
    end
    prestart[prestart · 一次性迁移/初始账号/示例数据] --> db
    domains --> db[(db · PostgreSQL 18)]
    worker[worker · app/worker · Procrastinate] --> db
    worker --> authz
    worker --> domains
    db --- queue[Procrastinate 队列表 · 不列入业务 ER 图]
```

`compose.yml` 定义 db、prestart、backend、worker、pi。Pi 健康后 backend / worker 才启动；Pi 仅在内部网络接受带服务令牌的请求。db 健康后 prestart 执行 Alembic、初始化账号和演示导入，成功退出后才启动 backend / worker。两者复用后端镜像；默认只有 Web/API 的 `127.0.0.1:8000` 发布到宿主机。开发服务在 `compose.dev.yml` 中显式选择，HTTPS 部署见 [deploy/README.md](../deploy/README.md)。

路由负责 HTTP 参数和响应，`app/authz` 负责服务端租户身份解析与权限策略，`app/domains/*` 放模型、响应 Schema 和领域服务。`app/seed` 校验并幂等导入合成数据。Worker 执行 `system.ping`、`sources.sync_run` 和 `profiles.draft_initial`；业务任务经 `tenant_task(org_id, body)` 校验租户，查询仍显式限定租户。队列迁移和健康检查限制见 [ADR-0002](adr/0002-worker-and-compose-layout.md)。

## 核心对象与实际表

下图及后面的 M2 增量图对应 `backend/app/domains/*/models.py`、模板 `app/models.py` 和 Alembic；登录限流表 `login_attempt`、初始化令牌表 `setup_token` 和向导状态表 `setup_step_state` 不列入此图。各框只列身份、关键引用和主要业务字段。`TenantRecord` 表共同具有 `id`、`created_at`、`org_id`；Organization 自身就是租户，AuditEvent 的 `org_id` 可空以表示平台事件。

```mermaid
erDiagram
    organization {
        uuid id PK
        string slug UK
        string name
        int roster_revision
    }
    user {
        uuid id PK
        string email UK
        boolean is_superuser
    }
    department {
        uuid id PK
        uuid org_id FK
        uuid parent_department_id FK
        string name
    }
    employee {
        uuid id PK
        uuid org_id FK
        uuid department_id FK
        string employee_code
        string display_name
    }
    roster_import {
        uuid id PK
        uuid org_id FK
        string status
        string filename
        string file_sha256
        jsonb rows
        jsonb column_mapping
        jsonb issues
        jsonb summary
        int base_revision
        uuid uploaded_by_user_id FK
        uuid applied_by_user_id FK
        datetime applied_at
    }
    reporting_line {
        uuid id PK
        uuid org_id FK
        uuid employee_id FK
        uuid manager_employee_id FK
        datetime valid_from
        datetime valid_to
        string source
        uuid roster_import_id FK
    }
    external_identity {
        uuid id PK
        uuid org_id FK
        uuid employee_id FK
        string source
        string external_id
    }
    org_membership {
        uuid id PK
        uuid org_id FK
        uuid user_id FK
        uuid employee_id FK
    }
    role_grant {
        uuid id PK
        uuid org_id FK
        uuid membership_id FK
        uuid department_id FK
        uuid granted_by_user_id FK
        string role
    }
    task {
        uuid id PK
        uuid org_id FK
        string source
        string external_key
        jsonb attributes
    }
    work_event {
        uuid id PK
        uuid org_id FK
        uuid actor_employee_id FK
        uuid task_id FK
        string action
        datetime occurred_at
    }
    capability_schema_version {
        uuid id PK
        uuid org_id FK
        int version
        string status
    }
    capability_definition {
        uuid id PK
        uuid org_id FK
        uuid schema_version_id FK
        string key
        int position
        jsonb level_rubric
    }
    evidence {
        uuid id PK
        uuid org_id FK
        uuid subject_employee_id FK
        uuid event_id FK
        uuid supersedes_evidence_id FK
        string kind
        string visibility
        string status
        jsonb content
        jsonb provenance
        string content_hash
    }
    profile_claim {
        uuid id PK
        uuid org_id FK
        uuid subject_employee_id FK
        uuid capability_definition_id FK
        uuid supersedes_claim_id FK
        string assessment_status
        string confidence
        jsonb generated_by
    }
    claim_evidence {
        uuid org_id FK
        uuid claim_id PK,FK
        uuid evidence_id PK,FK
        string relation
    }
    profile_version {
        uuid id PK
        uuid org_id FK
        uuid subject_employee_id FK
        uuid schema_version_id FK
        uuid supersedes_version_id FK
        int revision
        jsonb snapshot
        jsonb published_by
    }
    profile_progress {
        uuid org_id PK,FK
        uuid employee_id PK,FK
        string fill_status
        string inference_status
        string evidence_quality
    }
    audit_event {
        uuid id PK
        uuid org_id FK
        string actor_type
        string actor_id
        string action
        string target_type
        string target_id
        jsonb details
    }
    organization ||--o{ department : owns
    organization ||--o{ employee : owns
    organization ||--o{ roster_import : owns
    organization ||--o{ reporting_line : owns
    user ||--o{ roster_import : uploads
    user |o--o{ roster_import : applies
    employee ||--o{ reporting_line : reports
    employee ||--o{ reporting_line : manages
    roster_import |o--o{ reporting_line : sources
    organization ||--o{ external_identity : owns
    organization ||--o{ org_membership : owns
    organization ||--o{ role_grant : owns
    organization ||--o{ task : owns
    organization ||--o{ work_event : owns
    organization ||--o{ capability_schema_version : owns
    organization ||--o{ capability_definition : owns
    organization ||--o{ evidence : owns
    organization ||--o{ profile_claim : owns
    organization ||--o{ claim_evidence : owns
    organization ||--o{ profile_version : owns
    organization ||--o{ profile_progress : owns
    organization |o--o{ audit_event : scopes
    department |o--o{ department : parent
    department |o--o{ employee : contains
    employee ||--o{ external_identity : identifies
    user ||--o{ org_membership : joins
    employee |o--o| org_membership : links
    org_membership ||--o{ role_grant : receives
    department |o--o{ role_grant : scopes
    user |o--o{ role_grant : grants
    employee ||--o{ work_event : performs
    task |o--o{ work_event : relates
    capability_schema_version ||--o{ capability_definition : defines
    employee ||--o{ evidence : subject
    work_event |o--o{ evidence : source
    evidence |o--o{ evidence : supersedes
    employee ||--o{ profile_claim : subject
    capability_definition ||--o{ profile_claim : assesses
    profile_claim |o--o{ profile_claim : supersedes
    profile_claim ||--o{ claim_evidence : cites
    evidence ||--o{ claim_evidence : supports_or_contradicts
    employee ||--o{ profile_version : publishes
    capability_schema_version ||--o{ profile_version : freezes_schema
    profile_version |o--o{ profile_version : supersedes
    employee ||--o| profile_progress : tracks
```

业务引用使用 `(org_id, x_id) → (org_id, id)` 复合外键，避免跨租户串联；登录账号引用为普通 `user.id` 外键。`audit_event.actor_id/target_id` 和 `profile_version.published_by` 是归因信息，不是外键。`org_membership.employee_id` 可空，非空时在组织内唯一；同名员工靠显式 external_identity 关联，不能按姓名合并。

部门按 `(org_id, parent_department_id, name)` 唯一，使用 `NULLS NOT DISTINCT`，同名顶层部门仍冲突，不同上级下可同名。`reporting_line` 禁止自我汇报，每名员工最多一条 `valid_to IS NULL` 的当前记录。`roster_import` 保存解析后的行、列映射和预览版本，不保存原文件；`organization.roster_revision` 初始为 0。只读的 `compute_roster_diff` 用固定 5 次查询生成变化、部门树、提醒和阻断原因，缺少可选列时保留现值。上传、确认导入和版本号递增由后续 S6b 实现。

能力词典每个组织最多一个 active 版本；定义的 `key` 在词典版本内唯一，`position` 是 S5 加入的展示顺序。Claim 指向具体版本的能力定义，判断状态与审核状态分开，信心标签不能视为概率。画像按词典补齐无 Claim 的未知项。

Evidence 追加服务在 `app/domains/evidence/service.py`，以组织、本人、来源、来源引用和内容哈希保证幂等。原始正文和归因由数据库触发器保护；只允许 active 转为 retracted/superseded，状态变化同事务追加审计，更正正文追加新 Evidence 并指向原记录。ProfileVersion 冻结判断字段和证据引用，不复制受限正文；读历史时仍检查当前权限。详见 [ADR-0001](adr/0001-tenancy-and-core-objects.md) 和 [ADR-0010](adr/0010-source-record-ingestion.md)。

## M2 请求、来源与起草

```mermaid
erDiagram
    organization ||--o{ source_connection : configures
    source_connection ||--o{ sync_run : executes
    organization ||--o| model_setting : configures
    organization ||--o| drafting_setting : excludes_capabilities
    employee ||--o{ profile_change_request : subject
    profile_version o|--o{ profile_change_request : base_version
    profile_change_request ||--o{ change_item : proposes
    capability_definition ||--o{ change_item : defines
    profile_claim o|--o{ change_item : target
    change_item ||--o{ change_item_evidence : cites
    evidence ||--o{ change_item_evidence : references
    source_connection {
        uuid org_id FK
        string kind
        jsonb config
        string secret_ciphertext
        datetime synced_until
    }
    sync_run {
        uuid org_id FK
        string status
        jsonb cursor
        jsonb counts
    }
    profile_change_request {
        uuid org_id FK
        string origin
        string status
        jsonb generated_by
    }
    change_item {
        uuid org_id FK
        string op
        jsonb after
        string explanation
    }
```

所有业务引用带租户复合外键。ExternalIdentity 的员工关联可空；账号关联后按已存 Task 重新推导证据，解除时撤销。部门/员工各存自动起草开关；敏感能力 key 放 DraftingSetting。ProfileClaim 只保留正式判断，审核进度从请求/争议/版本推算，不再存 review_status，见 [ADR-0009](adr/0009-profile-change-requests.md)。

```mermaid
flowchart LR
    A[管理员保存连接或上传文件] --> B[SyncRun 排队]
    B --> C[Worker 按页写 Task/WorkEvent/外部账号]
    C --> D[提交计数与游标，失败按断点重试]
    D --> E[管理员确认对应，追加本人 Evidence]
    E --> F[drafting_scope 过滤部门/员工/能力]
    F --> G[经验编号合并，Pi 模型起草]
    G --> H[输出校验，支持证据重算统计]
    H --> I[pending_owner 请求，员工过目与编辑]
    I --> K[pending_review 请求，批准前可撤回]
    G --> J[用量上限：记录恢复时间，排队续跑]
    J --> F
```

同步每页事务提交，同一来源连接的同步串行；起草每人事务提交，同企业起草串行，一人失败不影响其他人。ChatGPT 令牌按企业加锁，API 与 worker 共用数据库加密凭据。输入只含白名单 standard 工作记录，模型不直接写 Claim。详见 [ADR-0012](adr/0012-resumable-source-sync.md)、[ADR-0013](adr/0013-model-initial-drafting.md)。权限仍沿用下表。

## 租户与权限

M3 的表单、JSON、MCP 和机器人统一调用 `domains/change_request/employee.py`，员工工具定义在 `domains/employee_tools.py`。MCP 由 OAuth / 个人令牌解析本人 Principal，每次请求重新检查企业开关、账号和授权；不接收客户端传入的员工或企业身份。提交写入来源、客户端与收件箱通知，自述经证据服务追加，撤回追加撤销记录。

机器人分网页入口（`api/routes/bot.py`）和对话核心（`domains/bot`）。核心负责身份、会话、限频、工具和带 revision 的预览确认卡片，供后续 IM 入口复用。Pi 通过受认证的内部流式协议调用平台提供的工具，不装载执行命令或文件工具；模型不能调用提交工具。只有员工对已保存且未过期的预览卡片执行确认，核心才提交。原文默认不保存，保留开关最多 30 天，Worker 清理过期原文。

Pi SDK 固定 0.73.1（MIT），模型输入和工具输出经过投影校验；起草与对话共用企业模型、加密凭据及额度暂停状态。生产订阅刷新在企业锁内完成，本机授权仅通过开发覆盖文件挂载给 Pi，access token 用后删除，不刷新、不写回。协议与权限设计见 [ADR-0014](adr/0014-pi-agent.md)、[OAuth ADR](adr/0019-mcp-oauth-authorization.md)。

`/api/v1/orgs/{org_id}/...` 的 Principal 由服务端通过当前用户、成员资格和角色授予解析，客户端不能指定自己的角色。员工不是登录账号；平台 `is_superuser` 也不自动授予任何组织权限。

| 角色 | 员工、画像、进度和 standard 证据 | restricted 自述与经理评价正文 |
| --- | --- | --- |
| 企业管理员 | 本组织全部 | 仅元数据；如另有本人/经理身份才按该身份读正文 |
| 部门经理 | 管辖部门（含全部下级部门）及本人 | 管辖部门（含全部下级部门）及本人 |
| 员工 | 本人 | 本人 |
| 系统 Worker | 任务所属租户 | 任务所属租户 |

角色叠加，部门经理的管辖范围包含获授权部门及其全部下级部门，每次请求从当前部门结构计算；名单重新导入、部门调整后自动生效。列表、搜索和计数在 SQL 中用同一个可见范围条件；无成员资格或员工不可见统一返回 404。受限证据保留元数据并标记 `redacted=true`，详情不返回正文。数据库复合外键负责引用完整性，应用授权负责读取范围；尚未开启 RLS。详见 [ADR-0003](adr/0003-authz-and-tenant-context.md) 和 [ADR-0006](adr/0006-manager-descendant-scope.md)。

## 三类 Slot

| Slot | 代码位置与边界 | 当前状态 |
| --- | --- | --- |
| Source Slot | `backend/app/connectors/` 接来源；`app/domains/work` 承接 Task/WorkEvent，证据服务追加 Evidence | Cloud/DC 两个适配器和 CSV/Excel，共用同步与身份推导；seed 仅导入合成样例 |
| Profile Slot | `app/domains/drafting` 使用 Evidence 与版本化 capability 产生请求，批准后冻结 ProfileVersion | 起草生成 pending_owner；M3 员工提交 pending_review；经理审批在 M4 |
| Interaction Slot | 后续连接待确认问题、人类回答及统一 Review 流程，回答回到证据/候选判断 | M3 已有 PendingQuestion、InboxItem 与员工回答；自动提问在 M4 |

来源插件只提交事实和证据，不直接发布画像；模型只产生候选改动项。Pi 模型层可替换、可完全关闭，使用输入白名单并记录提供方、模型、提示词及 Schema 版本。

## P1–P6 扩展位置（尚未实现）

以下只描述未来接入点，不构成现有表、API 或调配承诺；实施前需独立规划与 ADR。

| 扩展 | 未来接入对象或接口 |
| --- | --- |
| P1 需求登记 | 新需求对象关联 Organization、Department 和所需 CapabilityDefinition 版本；需要时关联 Task，不把需求混入 Evidence |
| P2 资源容量 | Employee 关联独立的时间/容量记录；不从画像等级推导空闲时间 |
| P3 跨部门流程 | Organization、Department、Employee 关联独立申请/确认记录，复用服务端 Principal、授权和 AuditEvent |
| P4 匹配优化 | 读取已确认的 ProfileVersion、需求与容量，输出可解释候选方案；不修改原始 Evidence 或自动形成考核结论 |
| P5 反馈重规划 | 执行结果进入 Task/WorkEvent，经 Source Slot 追加 Evidence，再经 Profile/Review 流程更新画像；方案调整另留版本和审计 |
| P6 组织洞察 | 基于授权范围内的 ProfileProgress、词典和发布版本聚合；总体进度不能成为读取自述正文的权限 |
