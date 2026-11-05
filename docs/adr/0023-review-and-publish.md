# ADR-0023：审批路由、临时读取权限与原子发布

状态：采用（NERE-117）；2026-11-05。

## 审批人身份与路由

审批人是企业成员 OrgMembership，允许没有员工身份的管理员参与兜底审批。Principal 不增加字段，按企业和 user_id 对应成员；人工决定记录 reviewer_membership_id。acting_for_membership_id 仅预留给后续代理审批。policy、sync 决定不记录人工成员；本次人工 decide 服务拒绝使用这两个入口。

reviewers_for(session, org_id, subject_employee_id) 动态返回成员列表，始终排除请求对象本人。优先选择 ReportingLine 的当前直属上级，但他必须有覆盖员工部门（包括下级部门）的部门经理授权。否则从员工部门沿父部门逐级寻找，取最近一级的全部经理；仍为空则交给全部企业管理员。部门经理提交自己的请求同样排除自己；没有可用审批人时保留 pending_review，管理员列表返回不可决定的条目和原因。

有员工身份的审批人收到 review_requested；没有员工身份的管理员通过待审批列表及其数量获取任务，不改变员工收件箱模型。提交和管理员批量送审共用路由；决定、撤回时关闭该请求的待办通知。

## 审批期间的读取

review_access 是唯一临时权限入口：同企业、不是请求对象本人、请求处于 pending_review，且当前用户对应的成员在动态路由结果中。证据访问仍经过原有 Principal 员工范围校验；只有请求引用的证据可以额外读取 restricted 正文。请求里的 owner_statement、objection 同样受此例外保护。

待审批详情只允许当前审批人读取。非审批人不得使用审批接口；原有员工和画像接口继续按原权限读取。决定或撤回后例外立即失效；管理员无部门经理授权时，恢复为只能读取 restricted 的脱敏摘要。经理原先就有的部门范围权限不受影响。请求的依据来源和关系不因例外而改变。

## 决定与事务

decide(principal, session, request_id, revision, decisions, comment, via, client_name) 与其他服务一样不 commit，由网页或后续 MCP、IM 适配器在成功后统一提交。网页固定 via=web，不接收客户端指定的身份、企业、入口或客户端字段。所有输入禁止额外字段。

先对企业行加共享锁，和词典换版的排他锁互斥，保证校验至发布之间启用词典保持一致；不同员工的审批可同时持有共享锁。随后锁请求并刷新 ORM 缓存，修订号不符或不再待审批返回 409；再检查动态路由并锁员工行，串行化同一员工的发布。与词典保存保持企业 → 请求的锁顺序。请求对象是当前员工时始终 403。决定必须恰好覆盖全部尚未决定的项，拒绝和退回必须有非空白原因；缺失、重复或不能批准的项被选择批准均返回 422。当前词典缺少能力 key、当前判断与目标不同，或等级已从词典移除时不能批准；仍可退回或拒绝。

同一事务写决定与逐项结果，有批准项就调用 publish。有退回项则另建 correction 来源的 draft，parent_request_id 指回原请求，复制退回项与证据引用，decision_reason 保留退回原因；保留解释、不确定性、自述与异议。原请求有批准项则 approved，否则有退回项则 changes_requested，否则 rejected；状态变化增加 revision 并写 decided_at。

整体意见与非空逐项原因合并追加一条 restricted manager_review 证据，provenance 记录 request_id、decision_id、via；整体意见关联所有项，否则关联有原因的项，关系是 context。不会把审批意见当作支持依据来提高判断状态。证据正文只追加，决定、发布、草稿、证据、审计和通知共同提交或回滚。

## 发布契约

publish(session, request, items, decision) 供后续审批规则和增量同步复用，调用方负责权限检查、验证目标与词典、锁请求和员工，并确保 items 确为本次批准项。本次只能经 decide 调用。后续自动发布必须实现各自明确允许的授权规则。

按能力 key 映射到当前启用词典的定义，每项追加 ProfileClaim，以 supersedes_claim_id 指向旧判断。add、modify 使用改后陈述和等级；人工批准标为 confirmed。支持证据非空且全来自 jira 时为 tentative；context 和 contradicts 不参与“只有 Jira”判断。retract 追加 unknown 判断，等级清空且不带证据；attach_evidence 保留原判断陈述、等级、状态和置信度，合并原有与新增引用，仍受只有 Jira 最多暂定的上限约束。

每次发布冻结一个新的 ProfileVersion，revision 递增，记录当前词典、上一版本和发布者（actor_type、user_id、via、request_id、decision_id），通过既有 build_profile_snapshot 冻结当前判断和证据引用，不复制 Evidence.content。ChangeItem 回填 published_claim_id；旧判断和旧版本均不修改。

## 迁移与取舍

一个迁移新增 review_decision、请求父指针及决定时间、逐项结果及发布指针，并扩展通知种类；引用全部带 org_id 复合外键。降级移除本阶段新增的列、决定表和两种新通知，保留已发布画像和追加的经理意见证据。禁止把降级当作撤销已经发布的业务结果。

复用现有 Principal、员工可见范围、append_evidence、状态转换、通知和画像快照；不添加依赖，不另建入口专属发布逻辑。此阶段不提供网页、代理审批、审批规则、申诉、MCP 或 IM 适配器。
