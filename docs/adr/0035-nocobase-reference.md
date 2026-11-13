# ADR-0035：借鉴 NocoBase 架构的范围

正式画像写入规则已被 E21（M4.3）修改，见 [ADR-0040](0040-direct-profile-edits.md)；下文保留历史决定。

状态：已接受（2026-11-10，大古）。决策来源：NERE-6 E19；本 ADR 和两项小修由 NERE-296 交付，后续功能安排在 M4.2（NERE-295）。

## 背景与边界

合并 [NERE-290 第一轮草案](https://multica.ai/api/attachments/01a1c597-2461-75c9-8114-23e2b62dd651/download) 和 [NERE-292 第二轮报告](https://multica.ai/api/attachments/01a1c609-dc69-70f6-a4f7-d6b2712e85e7/download)，以第二轮第 3 部分修正第一轮，并按大古最终决定调整里程碑和范围。两轮分别对照 master `aaeb3ec`、`227c0a5`；本文的仓库引用已按 master `a72ab46`（含 NERE-293）重新核对，以文件和符号定位，移除报告中的旧行号。

不引入 NocoBase，不改成其插件，不按它重做架构。**只借鉴设计，不复制代码、提示词、界面文案或插件实现。** 正式画像仍只能通过批准的请求修改；员工、经理、管理员和 Pi 的权限边界不变；证据只追加，画像版本冻结判断和证据引用，不做整库回滚。

## NocoBase 事实与来源

以下是两轮调研在 2026-11-10 查看官方文档、在线源码得到的版本快照，不是本项目引入的依赖或兼容性承诺。v2 文档在 `nocode.docs.nocobase.com`，v3 文档在 `docs.nocobase.com`，二者不能混用。

- **版本：v3 仍为 Beta。** 当时最新三条 v3 release 为 `2026-11-09.1/2/3`，标记 Pre-release；稳定版 Latest 为 v2.2.41。来源：[releases](https://github.com/nocobase/nocobase/releases)、[v3 入门](https://docs.nocobase.com/getting-started/develop-your-first-app/)。
- **商业版范围要区分 v2 / v3。** v2 外部 PostgreSQL/MySQL、REST API 数据源为 Standard 及以上，Version Control、Migration Manager、Record History 为 Professional 及以上；Backup Manager 为社区版。v3 定价页把连接已有数据库列入社区版核心能力，商业插件包括高级审批、企业 SSO，不能照搬 v2 的商业边界。来源：[外部 PostgreSQL](https://nocode.docs.nocobase.com/data-sources/external/postgresql)、[REST API](https://nocode.docs.nocobase.com/data-sources/external/rest-api)、[Version Control](https://nocode.docs.nocobase.com/plugins/@nocobase/plugin-version-control/)、[Migration Manager](https://nocode.docs.nocobase.com/ops-management/migration-manager/)、[备份](https://nocode.docs.nocobase.com/plugins/@nocobase/plugin-backups/)、[v3 定价](https://www.nocobase.com/pricing/)。
- **许可证不是纯 Apache-2.0。** 根目录协议（2026-02-24 更新）引用 Apache-2.0，但附加条款优先，要求保留品牌标识，并限制面向公众提供低代码、无代码或 AI 平台 SaaS/PaaS。官方公告称从 AGPL-3.0 改为 Apache-2.0，但调研读到的 `data-source-manager`、ACL、工作流和插件文件头仍含 AGPL-3.0 / 商业双重许可声明；商业插件也不按 Apache-2.0 授权。本项目不将这些实现并入 Apache-2.0 仓库。来源：[LICENSE.txt](https://raw.githubusercontent.com/nocobase/nocobase/main/LICENSE.txt)、[许可页](https://www.nocobase.com/license/)、[调整公告](https://www.nocobase.com/blog/pricing-adjustment-202602/)、[DataSource 源码](https://raw.githubusercontent.com/nocobase/nocobase/main/packages/core/data-source-manager/src/data-source.ts)。

## 设计对照

| 领域 | NocoBase 的设计及来源 | 本项目现状及判断 |
| --- | --- | --- |
| 数据模型 | [Data Modeling Skill](https://nocode.docs.nocobase.com/ai-builder/data-modeling) 可操作表和字段；版本恢复可能覆盖配置和数据 | `backend/app/domains/capability/models.py` 的词典版本和定义、`profile/models.py` 的 `ProfileVersion.schema_version_id` 已实现元数据约束。`capability/service.py::save_version` 直接发布新的 active 版本，没有可持续编辑的草稿；不让 Pi 直接改字段或词典 |
| 工具与 Skill | [Tool](https://nocode.docs.nocobase.com/ai-employees/dev/ai-employee/define-tool) 有参数及 ASK / ALLOW，[Skill](https://nocode.docs.nocobase.com/ai-employees/dev/ai-employee/define-skill) 是操作说明，[AI Employee](https://nocode.docs.nocobase.com/ai-employees/dev/ai-employee/) 组合角色、说明和工具 | 员工、经理 tuple 在 `backend/app/domains/employee_tools.py`、`manager_tools.py`，机器人、MCP、IM 共用。管理员另有 `admin_assistant/registry.py`，含风险、快照、撤销和密钥输入，身份与确认语义不同，不合并登记表 |
| 数据源 | [DataSourceManager](https://nocode.docs.nocobase.com/api/data-source-manager/data-source) 为外部库提供 collection、resource、ACL | `backend/app/connectors/jira/client.py::JiraClient` 已合并 Cloud / DC 的共同逻辑；CSV 是上传文件，两者转成 `work/ingest.py::SourceRecord`。本项目只需单向产出证据，不需要外部库通用 CRUD |
| MCP | [MCP](https://nocode.docs.nocobase.com/ai/mcp/) 用 API Key / OAuth 按持有人及授权范围开放操作 | `backend/app/mcp/server.py`、`domains/access_token/` 已按当前用户授权；沿用 ADR-0017、0019，不增加通用资源写入权限 |
| 工作流 | v2 有触发器、节点和执行版本；[审批插件](https://nocode.docs.nocobase.com/plugins/@nocobase/plugin-workflow-approval/) 为商业版；[更新节点](https://raw.githubusercontent.com/nocobase/nocobase/main/packages/plugins/%40nocobase/plugin-workflow/src/server/instructions/UpdateInstruction.ts) 直接写 repository。[v3](https://docs.nocobase.com/faq/from-v2/) 回到代码定义流程 | `backend/app/domains/review/` 的路由、审批规则及代理，`inbox/reminders.py` 的提醒和 `source/schedule.py` 的同步周期已允许管理员调参数。流程留在代码；不引入可绕过权限写库的节点 |
| 权限 | [角色并集](https://nocode.docs.nocobase.com/users-permissions/acl/union/) 合并操作、数据范围及字段；[ACL](https://raw.githubusercontent.com/nocobase/nocobase/main/packages/core/acl/src/acl.ts) 叠加服务端条件 | `backend/app/authz/deps.py::get_org_principal` 统一计算身份和部门子树，`policies.py` 统一范围和 restricted 正文权限；独立管理员身份不能读取受限正文。不做角色编辑器或企业自写过滤条件 |
| 插件 | v2 [生命周期](https://nocode.docs.nocobase.com/plugin-development/server/plugin) 扩展数据库、API 和页面，同进程没有安全沙箱；[v3](https://docs.nocobase.com/core/framework/runtime/plugins/) 改为代码中显式注册 | 现有扩展点为 `connectors/`、`backend/app/domains/im/adapters/`、`templates/capabilities/`。只接受提 PR 扩展；不提供运行时上传包、entry points 或 Pi 动态工具注册 |
| 工作属性 | collection / 字段设计器可增加任意字段 | `backend/app/domains/org/models.py::Employee` 及 `org/roster/schemas.py` 的名单六列是固定的；未知列会警告。增加固定工作属性即可，不引入自由字段设计器 |
| 提示词 | [v2 Skill](https://nocode.docs.nocobase.com/ai-employees/features/skills) 为预置；[v3 Studio Skill](https://docs.nocobase.com/studio/project/skills/) 的版本管理面向开发 agent | `backend/app/domains/bot/prompts.py` 已采用固定规则加企业提问指南，`admin_assistant/prompts.py` 和 `drafting/model.py` 的安全规则固定；外部导入说明在 `templates/profile-import/SKILL.md`。企业补充说明不能改变工具、身份、权限和输出校验 |

## 决策

### A：起草时提议新增能力——做，M4.2（NERE-295）

起草提议和存储已实现（S11a，NERE-315，输出协议见 ADR-0013）；管理员处理已实现（S11b，NERE-324）：首页待办、词典编辑器预填与忽略、管理助手查询与可撤销忽略；仅保存词典新版本时在同一事务中接受同名 open 建议，已有判断不变。

当前 `backend/app/domains/drafting/model.py::draft` 严格校验能力 key、等级和证据引用，词典外 key 会失败，最多重试三次；不能通过放宽 `items` 来解决词典缺口。接受独立的「建议新增能力」输出，初始和增量起草共用，Pi 只提议，不直接加入词典，不据此给员工下判断。

沿用第一轮的最小设计：每人最多三条建议，仅含名称、分类、说明、原因及至少两条本批工作证据引用；不带等级和判断。独立 `capability_proposal` 表按企业记录 open / accepted / dismissed，同名 open 合并并追加引用，最多十条 work_record 证据；管理员可忽略，已忽略同名不再提示。只引用工作记录，不把 restricted 自述交给管理员。

管理员待办和词典编辑器展示建议。「加入词典」预填编辑内容，由管理员补等级说明，再走现有预览和版本保存；不假设现在已有持久词典草稿。管理助手的只读、忽略功能和现有词典确认卡共用服务层。词典发布不修改已有画像，后续判断仍走正常请求和审批。

此功能超出 v0.2，已由 E19 接受；后续实现需迁移，并更新 ADR-0013 的起草输出协议和评测。拒绝「允许词典外 key 直接进入判断」方案。

### B：统一工具登记——不做

员工和经理工具已经一处定义、多入口复用。管理助手面向管理员，有不同的权限、变更集、二次确认、密钥和撤销语义，统一格式只会混合这些边界。机器人 `backend/app/domains/bot/reviews.py` 仅转发查询审批工具，有意不把审批决定交给 Pi。

真正的重复是 `get_today` 同时登记在 `backend/app/domains/admin_assistant/registry.py` 和直接工具 `tools.py`。记为遗留，下次修改该工具模块时处理，本 PR 不扩大范围。

### C、R4：适配器接口和外部推送——一起推迟到第三个数据源

现在只有 Jira 远程源和 CSV 上传，CSV 没有远程游标或测试连接，不为它实现空方法。第三个数据源立项时再统一「测试连接、按游标取一页 SourceRecord 并返回下一游标」，并同时设计外部 HTTP 推送；后者可作为第三个数据源的接入方式。

届时一起检查 `backend/app/domains/work/ingest.py` 的来源约束、`source/schemas.py` 的 kind、`source/sync.py` 的分支和游标、`source/schedule.py` 的筛选、管理助手登记和页面、`connectors/README.md`。外部账号随 `SourceRecord.people` 带出，关联仍由 `source/linking.py` 做，写入不能因邮箱或姓名相同而自动建立关联（ADR-0010）。

单纯适配器抽取不需要迁移；推送接口涉及连接级写入令牌、限流和迁移，应单独设计。它只能写某来源的待关联工作证据，不能改正式画像，也不能给外部代码进程内权限。暂以 CSV 导入作为替代。

### D：资质证书——推迟，不建表，也不预留空表

模拟企业说明资质有效期是独立问题。例如 `examples/test-enterprises/muan-hospital/manager_reviews.yaml` 中 MA003 的 `cardiac_intervention` 判断把资质前提混进能力等级；`examples/test-enterprises/README.md` 说明证书在 HR 档案 `roster.yaml`，不是现有六列名单导入数据。不能据此把准入或排班判断引入画像。

**这偏离 v0.2 §5.4「资质证书第一版只预留表结构」，已由大古在 E19 确认。** 不以「现有模型已覆盖全部 v0.2」为理由，也不以空表承诺尚未设计的接口。

将来接收证书数据时，设计为**员工名下单独的资质记录，可选关联一个能力 key，`ProfileClaim` 不动**。资质是外部签发、有编号和有效期的事实，能力是基于证据的判断；两者不能用同一个等级表达。第一轮的 `CapabilityDefinition.value_kind` / `ProfileClaim.valid_until` 方案作废。再次立项时再明确来源、确认、过期展示和检索边界。

### R1：员工档案三个固定工作属性——做，M4.2

只提供岗位序列、工作地点、入职日期（显示为入职年限），三个可空字段，默认关闭、企业逐项开启。管理员通过名单维护，不进入画像版本，不交给 Pi 起草，可见范围沿用 `employee_scope_clause`。涉及一次迁移、名单可选列和网页展示；为后续按能力找人保留必要筛选依据。

这是 E19 接受的 v0.2 §5.1 扩展，并明确「不默认采集私人资料」的边界：不提供企业自由命名字段，不收手机号、住址、证件号、健康等私人资料。语言和行业经验需要证据及判断，放在能力词典；资质按 D 单独设计。

### R3：起草补充说明——做，M4.2，先完成 NERE-267 真实模型基线

沿用固定规则加企业补充说明的结构，说明最多 2000 字、按版本追加，`generated_by` 记录版本。企业只能解释本企业的工单与能力，不得改变工具、权限、身份或输出格式；后端仍严格校验。管理助手不开放完整系统提示词编辑。

实现需要设置保存、版本记录、迁移及起草输入调整。用同一数据比较有无说明两次结果，误导性证据被起草的比例不能上升；没有 NERE-267 的真实模型基线时不宣称质量改善。A 的评测也要覆盖有无补充说明。此扩展超出 v0.2 §4.5 仅有提问指南的范围，已由 E19 接受。

NERE-317 落地为企业范围的 `drafting_note`：以企业和版本为复合主键，保存时锁企业行再追加，
正文无变化不追加也不审计。清空及撤销都追加版本；撤销复用管理助手变更集的正文快照。
每名员工开始初始或增量起草时读取最新版本，固定规则后附固定边界说明及正文；
`generated_by.drafting_note_version` 记录实际使用的版本，无正文时为 null。
审计保留前后全文，助手沿用公共审计上下文记录来源、会话和变更集。
输出协议及校验不变。真实模型对比见 [评测记录](../m4.2-drafting-note-eval.md)。

### E：新增的其他借鉴只有 1-1、1-6、1-8

| 项 | 决策与范围 |
| --- | --- |
| 1-1 正式画像唯一写入路径架构测试 | 本 PR 完成，见下节；仅静态测试，不改审批权限或发布协议 |
| 1-6 插件开发指南 | 从 M5 提前到 M4.2。写明数据源、IM 入口、能力模板三种提 PR 扩展方式、接口、安全边界和必带测试；一并核对 IM 的 Platform 与 ADAPTERS 登记一致性。不做运行时插件 |
| 1-8 提问指南审计 | 本 PR 完成，见下节；网页与管理助手共用前后快照 |

其余模块没有新增采纳项：区块和页面仍用固定组件；审计仍按业务事件显式追加；词典已有导入导出；备份仍用于运维恢复，不作为证据或画像纠错入口；个人令牌、中文界面和多租户部署沿用现有设计。

### 明确不做或推迟的其余建议

| 建议 | 最终决定及原因 |
| --- | --- |
| 1-2 工作流引擎、管理员画流程 | 不做；流程留在代码，仅开放已有参数，不让配置产生新的正式画像写入路径 |
| 1-5 角色编辑器、自由配置权限 | 不做；身份、部门子树和 restricted 正文规则由同一个 Principal 及服务端策略决定 |
| 1-7 运行时插件 | 不做；同进程插件可读凭据、伪造身份、给 Pi 加工具，使用者无法判断其可信性 |
| 1-9 编辑完整系统提示词或 Skill | 不做；固定提示词包含安全和产品红线，补充说明不能改工具白名单 |
| R2 可配置审批路由 | v0.2 不做；维持固定路由和限时代理。不在本 PR 增加审批人配置或防自批数据库触发器 |
| R5 通讯录同步 | v0.2 不做；维持名单维护，调岗和离职会影响审批及读取范围，不能自动套用同步结果 |
| R6 SSO | v0.2 不做；邮箱密码及预建账号不变 |
| 1-3 待确认问题类型、数量可配置 | 推迟，等企业或测试小队反馈；不提前增加设置列 |
| 1-4 CSV 重新导入触发增量起草 | 推迟；CSV 天然满足「合并后证据不全为 Jira」，必须先单独明确自动补证据规则，不能直接套用现有 E2 分支 |
| 1-10 通知幂等键 | 推迟；`backend/app/domains/im/notifications.py` 已有发送后崩溃可能重复的 shortcut，等平台支持，不在本 PR 处理 |

## 本 PR 的两项约束

### 正式画像写入路径

`backend/tests/domains/test_profile_write_paths.py` 用标准库 `ast` 扫描 `backend/app` 的调用节点，注释、字符串和类定义不计为调用：

- `ProfileClaim(...)`、`ProfileVersion(...)` 只允许在 `domains/review/publish.py` 和 `seed/`。
- `publish(...)` 只允许在 `domains/review/service.py`（经理决定）、`domains/review/policy.py`（规则自动发布）、`domains/drafting/incremental.py`（增量补证据）。
- 允许名单写在测试里；新增路径必须先修改 ADR，在 PR 中说明理由。失败信息为「正式画像只能由批准的请求修改，新增写入路径需要先改 ADR」，并列出文件、行号和调用。

这是静态架构约束，不代替 `can_review`、防自批、租户过滤、事务及数据库约束，也不覆盖所有可能的 ORM 动态构造或原始 SQL。现有审批和权限测试仍需保留。

### 提问指南审计

`backend/app/domains/bot/settings.py::save_settings` 在企业行锁内读取旧设置，保存时追加 `bot_settings.updated`，包含 `before` / `after` 的完整指南和保留天数，保留原有顶层 `retention_days`。有效设置不变时不追加审计，与 `drafting/update_settings.py` 的现有约定一致。

网页 `backend/app/api/routes/bot_settings.py` 和管理助手 `update_bot_settings` 调同一个服务；助手的变更集前后快照与审计一致，审计额外保留来源、会话和变更集 ID。事件跟随设置事务提交或回滚，不记录聊天原文，不改变管理员授权或审计读取权限。

## 收益、代价和后续

本次没有新依赖和数据库迁移。两项小修使新增写入路径能被 CI 拦住，并让企业提示说明的变化可追溯；无变化重复保存不再制造设置审计事件。指南原文进入已有审计记录，仍只按现有审计权限读取。

后续 A 会增加起草输出及管理员待办噪声，靠数量上限、同名合并和忽略控制；R1 的字段一旦存入数据便有迁移成本；R3 的质量风险要由真实模型评测约束。M4.2 分别落实这些功能和插件指南，不把本 ADR 当作它们已实现的证明。C、D、R4 保留重新决策条件，B 及明确不做项不提前搭空架构。
