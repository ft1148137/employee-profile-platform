# M10 验收记录

验收代码基线为 master `8554ca0b4a9292923337969ddfcb633b31c9fdee`（北京时间 2026-11-19），包含 B2 和第一阶段复核的两项修复。范围来自 [NERE-640](mention://issue/01a1de41-e621-7ec2-9080-0a3296dc63ae)，记录任务为 NERE-840。[基线提交](https://github.com/ft1148137/employee-profile-platform/commit/8554ca0b4a9292923337969ddfcb633b31c9fdee)

## 依据与交付

A1～A6、B1、B2 及两项复核修复全部合并到 master，各 PR 最终检查通过。各项行为采用对应 PR 的后端与浏览器 CI 记录。C1 在独立的 `nere840` PostgreSQL（宿主端口 15840）上复跑 `test_m10_project.py` 和 `test_m10_feedback.py`，18 项通过。真实模型评测沿用 B1 报告，长期演示环境的实机结果由 M10.1 补录。

| 子 issue | 交付 | 最终 PR 检查 |
| --- | --- | --- |
| NERE-832 A1 | 项目聚合、多角色与排程影响 | [#383](https://github.com/ft1148137/employee-profile-platform/pull/383/checks) |
| NERE-833 A2 | 角色必需和可选能力 | [#384](https://github.com/ft1148137/employee-profile-platform/pull/384/checks) |
| NERE-834 A3 | 助手项目草稿和按姓名查周负荷 | [#386](https://github.com/ft1148137/employee-profile-platform/pull/386/checks) |
| NERE-835 A4 | 拒绝原因、决策记录和 M9 带入修整 | [#385](https://github.com/ft1148137/employee-profile-platform/pull/385/checks) |
| NERE-836 A5 | 执行跟踪、不可用影响和 IM 链接统一 | [#389](https://github.com/ft1148137/employee-profile-platform/pull/389/checks) |
| NERE-837 A6 | 工作反馈进入画像流程 | [#390](https://github.com/ft1148137/employee-profile-platform/pull/390/checks) |
| NERE-838 B1 | SolverPort、案例评测和承诺变更回归 | [#387](https://github.com/ft1148137/employee-profile-platform/pull/387/checks)、[#388](https://github.com/ft1148137/employee-profile-platform/pull/388/checks) |
| NERE-839 B2 | CP-SAT 生产接入和优化候选 | [#393](https://github.com/ft1148137/employee-profile-platform/pull/393/checks) |
| NERE-841 | 项目提交时校验现行词典 | [#391](https://github.com/ft1148137/employee-profile-platform/pull/391/checks) |
| NERE-842 | 原部门经理可跟踪进行中借调任务 | [#392](https://github.com/ft1148137/employee-profile-platform/pull/392/checks) |

## 多部门、多角色

| 验收项 | 测试文件与用例 | 结果与边界 |
| --- | --- | --- |
| D-026、D-027 项目与角色 | [test_m10_project.py](../backend/tests/api/routes/test_m10_project.py) 的 `test_project_submission_is_atomic`、`test_project_views_keep_department_boundaries`；[m10-project.spec.ts](../frontend/tests/m10-project.spec.ts) 的「需求方创建多角色项目并看到承接方答复和排程影响」；[test_m6_capacity_demand.py](../backend/tests/api/routes/test_m6_capacity_demand.py) 的 `test_reference_privacy_submission_and_profile_invariance` | #383 通过。A 方一次提交三个角色，B 方从既有需求入口处理本部门角色。角色、事件与通知一起提交，失败没有半个项目。项目和角色详情按部门投影，A 方看不到 B 方姓名、员工 ID 或个人负荷，无关人员访问返回 404。每个角色一个目标部门，原单部门需求入口仍可用，项目层无独立状态、编辑或撤回动作。 |
| D-029 分阶段与影响 | [test_m10_project.py](../backend/tests/api/routes/test_m10_project.py) 的 `test_project_schedule_and_downstream_impacts`、`test_project_analysis_rejects_external_and_cyclic_graphs`、`test_project_rejects_invalid_roles`；[m10-project.spec.ts](../frontend/tests/m10-project.spec.ts) 的「需求方创建多角色项目并看到承接方答复和排程影响」 | #383 通过。前置角色完成后下游才开始；跨项目、成环或无效的窗口被拒绝。用例中「暂时不能」或部分答复提示下游影响并得到项目预计推迟 6 天，承诺较晚时为 8 天。推迟按日历天估算，项目时间表只展示预计影响。 |
| D-028 多能力及现行词典 | [test_m10_role_capabilities.py](../backend/tests/api/routes/test_m10_role_capabilities.py) 的 `test_required_capabilities_filter_and_optional_capabilities_break_ties`、`test_validator_distinguishes_unmet_and_unconfirmed_capabilities`、`test_multi_capability_role_requires_a_published_primary_level`、`test_extra_capability_edit_stales_snapshot_and_preserves_visibility`；[test_m10_project.py](../backend/tests/api/routes/test_m10_project.py) 的 `test_project_rejects_stale_extra_capability`、`test_project_rejects_invalid_roles` | #383、#384、#391 通过。每个角色最多五项额外能力，不重复；必需能力必须达标，缺判断为「未确认／数据不足」，可选能力只在余量相同时排序。最后一个角色的可选能力或最低等级在提交前被移除时，共用创建服务返回 422，项目、需求、事件、依赖和通知均未写入。 |
| 助手起草、网页确认 | [test_m10_project_draft.py](../backend/tests/api/routes/test_m10_project_draft.py) 的 `test_project_draft_requires_web_confirmation`（`web`、`feishu`）；[m10-project-draft.spec.ts](../frontend/tests/m10-project-draft.spec.ts) 的「从网页助手核对项目草稿并创建项目需求」 | #386 通过。草稿无写入，过期草稿和无权用户不能确认，网页本人确认才创建。飞书发送草稿摘要和网页链接，测试使用模拟模型；真实飞书跳转留给 M10.1。 |

## 执行跟踪与历史

| 验收项 | 测试文件与用例 | 结果与边界 |
| --- | --- | --- |
| D-031 拒绝分类和历史投影 | [test_m10_reject_reasons.py](../backend/tests/api/routes/test_m10_reject_reasons.py) 的 `test_rejection_reasons_in_api`、`test_borrow_rejection_reason`、`test_rejection_reasons_in_confirmation_cards`；[test_m10_decision_history.py](../backend/tests/api/routes/test_m10_decision_history.py) 的 `test_history_keeps_superseded_decisions_and_respects_scope`；[m10-decision-history.spec.ts](../frontend/tests/m10-decision-history.spec.ts) 的「需求方选择拒绝原因并在决策记录查看」 | #385 通过。新拒绝和「暂时不能」答复必须选固定分类，选「其他」须说明；旧记录显示「未分类」。旧提议、逐方审批和事件仍可查，方案快照只给有权 B 方，员工拒绝理由仅本人可见。外部助手仍受原有操作权限约束。 |
| D-030 任务与状态通知 | [test_m10_tracking.py](../backend/tests/api/routes/test_m10_tracking.py) 的 `test_permissions_and_requester_projection`、`test_tracking_status`、`test_jira_sync_upgrades_legacy_state_and_notifies_once`；[m10-tracking.spec.ts](../frontend/tests/m10-tracking.spec.ts) 的「员工关联已完成的任务，经理看到可以结束分配」；[test_m6_capacity_architecture.py](../backend/tests/test_m6_capacity_architecture.py) 的 `test_worker_tracking_entrypoint` | #389 通过。按编号或标题手工关联已同步的 Jira 或已导入的 CSV 任务。关联修改、同步和每日维护重算五种状态。Jira 分类补齐后状态变化发一次通知，重复同步相同状态无新增提醒。A 方只看部门级风险；任务完成后由经理处理分配。平台不回写 Jira，也不分析工时偏差。 |
| 借调跟踪范围 | [test_m10_tracking.py](../backend/tests/api/routes/test_m10_tracking.py) 的 `test_borrowed_tasks_visible_to_owner_and_lender`；[m9-borrow.spec.ts](../frontend/tests/m9-borrow.spec.ts) 的「借出经理在借调页跟踪任务，借入经理仅看匿名进度」 | #392 通过。员工本人和仍有管辖权的原部门经理可查看及维护任务关联，原部门从「借调请求 → 进行中的借调分配」进入。借入方只看匿名状态，任务标题与关联入口不外露；跨企业访问、调岗或离职后不再有旧权限。 |
| 不可用影响 | [test_m10_unavailability_impact.py](../backend/tests/api/routes/test_m10_unavailability_impact.py) 的 `test_unavailability_notifies_managers_without_changing_commitments` | #389 通过。通知列出相交天数、受影响的下游角色和预计推迟天数，附方案入口。原分配与承诺未改；需要换人或调期时，由经理评估并走正式变更及审批。 |

## 画像回流

| 验收项 | 测试文件与用例 | 结果与边界 |
| --- | --- | --- |
| D-032 工作反馈提交和权限 | [test_m10_feedback.py](../backend/tests/api/routes/test_m10_feedback.py) 的 `test_permissions_and_concurrent_feedback`、`test_borrowed_feedback_scope`；[m10-feedback.spec.ts](../frontend/tests/m10-feedback.spec.ts) 的「经理写工作反馈，员工过目后由经理发布」 | #390 通过。已完成分配可由有员工管辖权的经理或管理员写 20～500 字事实反馈及相关能力；原部门经理可从「已结束的借调分配」操作。不得给自己写，每分配最多一条有效反馈，并发提交也只保存一条。反馈正文作为受限证据追加，A 方及无员工管辖权的借入方看不到正文。 |
| 本人过目、发布与找人 | [test_m10_feedback.py](../backend/tests/api/routes/test_m10_feedback.py) 的 `test_feedback_publication_and_search`（两种审批策略）；[m10-feedback.spec.ts](../frontend/tests/m10-feedback.spec.ts) 的「经理写工作反馈，员工过目后由经理发布」 | #390 通过。已有判断追加证据，新判断可由员工过目时定级；常规请求须有权经理批准才发布，企业关闭该能力的经理确认时，本人提交后按 E21 发布为「暂定」。发布后新人可按能力找到，反馈来源不计入证据数、最近日期、日期筛选或同级排序。增量起草仍按同步来源筛选其待过目请求。 |

## 求解器与迁移

| 验收项 | 测试文件与用例 | 结果与边界 |
| --- | --- | --- |
| B1 离线多项目多角色评测与匿名回放（D-033） | [test_solver.py](../backend/tests/eval/planning/contention/test_solver.py) 的 `test_case_coverage`、`test_contention`、`test_export_saved_replay`、`test_export_omits_untrusted_text`、`test_extended_commitment_leaves_capacity_for_overlapping_role` | #387、#388 通过。离线评测包括 12 个多角色争夺案例和 2 个从合成企业持久化快照匿名导出的单角色回放。#388 修复了结束日期延长的承诺变更提前结束并占用重叠角色容量的问题。该次复跑沿用原 14 案的 Agent 结果，CP-SAT 严格胜出 4/12（33.3%，门槛 30%），逐案硬约束违反均为 0，中位用时 0.0748 秒（门槛 10 秒）。14 案累计各配齐 82 个角色，CP-SAT 总延期 85 个工作日，规则和 Agent 均为 185 个工作日。[逐案数据及 #388 复跑](m10-solver-eval.md) |
| B2 生产单需求授权快照与优化候选 | [test_m10_solver.py](../backend/tests/api/routes/test_m10_solver.py) 的 `test_optimized_option_matches_b1_replay`、`test_optimized_option_uses_m9_reply_and_accept`、`test_optimized_option_failure_keeps_rule_options`；[m8-plan-page.spec.ts](../frontend/tests/m8-plan-page.spec.ts) 的「B 方经理无需模型查看方案，A 方经理看不到入口」<br><br>**#397 修复回归**。[test_replayed_commitment_blocks_late_optimization](../backend/tests/eval/planning/contention/test_solver.py#L70)、[test_optimized_option_rejects_work_after_commitment](../backend/tests/api/routes/test_m10_solver.py#L189)、[test_optimized_option_matches_b1_replay](../backend/tests/api/routes/test_m10_solver.py#L78)、[test_optimized_option_uses_m9_reply_and_accept](../backend/tests/api/routes/test_m10_solver.py#L115)、[test_optimized_option_preserves_real_handoff](../backend/tests/api/routes/test_m10_solver.py#L164) | #393 完成生产接入。生产按一个需求的授权快照生成候选，回归核对两条单角色回放和 M9 答复及接受流程。求解限时 10 秒，异常或超时后保留规则方案。生成候选时没有预占或分配。OR-Tools 9.15.6755 随后端运行环境安装。[ADR 0054](adr/0054-solver-port.md) · [依赖核验](licenses.md)<br><br>**#393 问题记录**。Reviewer 用[不可用日期匿名快照](../backend/tests/eval/planning/contention/replay-unavailability-01.json)复现了两项行为。原承诺 11 月 29 日结束的变更可生成 12 月 6 日结束的候选，实际提交仍须走 M9 延期审批。同一人从 11 月 23 日至 12 月 6 日连续投入 50% 时，候选会误拆成两段，要求分阶段确认，接受后按段生成两条分配。[NERE-843](mention://issue/01a1f07c-444f-797f-9206-2deaae6d80ed)<br><br>**#397 修复结果**。已合并至 master（`49c7a7f2`），后端和浏览器回归通过。原承诺 11 月 29 日结束时，求解器和独立复核均挡住 12 月 6 日结束的优化候选，延期继续走 M9 审批。初次答复时，同一人从 11 月 23 日至 12 月 6 日连续投入 50% 只需需求方接受新日期，接受后生成一条连续分配。真实人员交接和经理显式选择分阶段交付仍须需求方确认分阶段。[#397 CI](https://github.com/ft1148137/employee-profile-platform/pull/397/checks) |
| 迁移与数据保全 | [test_m10_project_migration.py](../backend/tests/test_m10_project_migration.py) 的 `test_m10_migration_roundtrip`、`test_m10_downgrade_preserves_new_data`、`test_m10_downgrade_waits_for_in_flight_reason`；[test_m10_tracking_migration.py](../backend/tests/test_m10_tracking_migration.py) 的 `test_tracking_migration_roundtrip`；[test_m10_feedback.py](../backend/tests/api/routes/test_m10_feedback.py) 的 `test_feedback_migration`；[test_m10_solver_migration.py](../backend/tests/test_m10_solver_migration.py) 的 `test_optimized_kind_roundtrip` | #383、#389、#390、#393 通过。迁移依次为项目、跟踪、反馈、优化方案，基线唯一 head 为 `c839001bc001`。兼容数据可升降级往返；存在项目／原因、任务关联、反馈请求或优化方案等相应新数据时阻止降级并保留记录。 |

## M9／M9.2 带入事项

| 验收项 | 测试文件与用例 | 结果与边界 |
| --- | --- | --- |
| E42(3) 承诺服务职责拆分 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_recheck_failure_is_persisted`、`test_unexpected_second_assignment_error_rolls_everything_back`；[test_m9_proposal_concurrency.py](../backend/tests/api/routes/test_m9_proposal_concurrency.py) 的 `test_accept_competes_with_one_terminal_transition`、`test_accept_rechecks_expiry_after_waiting_for_employee`；[test_m9_change.py](../backend/tests/api/routes/test_m9_change.py) 的 `test_changes_replace_only_different_segments`、`test_change_from_and_to_a_borrowed_employee` | #385 通过。承诺流程拆出借调审批检查、锁内复核与分段写入。竞争只产生一次终态，锁内到期可拦截，分段写入失败整单回滚，借调仍须相应批准。 |
| E44 / D-025 匿名序号 | [test_m10_decision_history.py](../backend/tests/api/routes/test_m10_decision_history.py) 的 `test_participant_numbers_only_reused_for_one_to_one`；[m9-change.spec.ts](../frontend/tests/m9-change.spec.ts) 的「三人同比例换掉一人时，A方变更前后两栏序号对齐并标出替换项」 | #385 通过。一出一进沿用被换者的匿名序号；多人进出时分别标记移除与新增，保留未换人员的序号。多人场景的网页实机留到 M10.1。 |
| E44 预占定时器、IM 链接 | [m9-proposal.spec.ts](../frontend/tests/m9-proposal.spec.ts) 的「预占跨过到期时刻后按钮消失且状态自动更新为已过期」「已打开的确认框跨过预占到期时刻后关闭并阻止提交」；[test_m82_feishu_plan_link.py](../backend/tests/domains/test_m82_feishu_plan_link.py) 的 `test_plan_link_through_delivery`、`test_feishu_fallback_plan_and_capacity_cards_keep_both_clickable_links` | #385、#389 通过。72 小时预占用单次定时，组件卸载时清理；到期保持原拦截行为。IM 发送统一用链接列表，方案和回退方案仍各自可打开。 |
| E44 助手按姓名查下属周负荷、E38(8) 历史权限 | [test_m10_manager_workload_tool.py](../backend/tests/api/routes/test_m10_manager_workload_tool.py) 的 `test_named_workload_respects_scope_and_module`；[test_m9_coordination_read_privacy.py](../backend/tests/api/routes/test_m9_coordination_read_privacy.py) 的 `test_upgrade_preserves_precise_scope_for_new_structured_reads`、`test_pending_proposal_cannot_reintroduce_hidden_reply` | #386 通过网页与飞书共用的工具测试。普通助手原先缺少已授权的团队只读入口；接入后按当前管辖范围读取，同名提示补部门，管辖外和不存在均返回统一提示。M9 隐私回归保留；两端各自原会话的调岗实机截图仍待 M10.1。 |

## 留给技术规划的推荐方案

- **决策记录查询（Mika）**。沿用本次权限投影，等演示环境出现长决策历史或有延迟数据时再做批量读取。若立项，保留旧提议、逐方审批和受限字段的回归。
- **未关联任务的首次跟踪通知（大古或 Mika）**。建议首次计算状态为「未关联任务」时静默初始化。M10.1 先核对升级后是否集中出现这类提醒，再决定是否单开修复。后续真实状态变化继续提醒。
- **反馈审批（大古）**。建议沿用已交付的 E21 企业规则，保留「本人提交后暂定发布」和「经理批准后发布」两条路径。若决定工作反馈一律走经理批准，单独调整此来源的规则和回归。
- **部门级组织洞察（D-034，Mika）**。建议维持当前范围。若 M10.1 证实部门经理需要跨项目的能力缺口或需求流向，再以部门级汇总单独立项。

## M10.1 回归检查清单

以下均待长期演示环境实测。记录操作日期、使用角色、实际结果及脱敏截图链接，使用合成人员、部门和任务。

- [ ] 升级前完成备份并记录备份标识、升级前后版本和回归基线。记录网页、网页助手、飞书及 Jira 测试入口分别使用的角色，包括 A 方、各目标部门经理、原部门经理、员工及管理员。
- [ ] A 方从网页新建一个有三个角色的项目，再从网页助手起草另一个项目，核对草稿确认前无项目，确认后可打开项目。飞书起草时沿链接到网页核对并提交。让各目标部门分别答复本部门角色，A 方核对阶段、最早开始、预计日期与影响提示，且看不到 B 方人名。
- [ ] 让一个前置角色选「暂时不能」并选择拒绝原因。需求详情「决策记录」显示分类，项目页提示下游角色受影响；从 A 方页面复核部门级展示。
- [ ] 员工在「我的分配」关联 Jira 任务。改变任务状态并同步，核对跟踪发生变化、经理收到一次提醒，相同数据再次同步无重复通知。让原部门经理在「借调请求 → 进行中的借调分配」查阅或维护关联，借入方仅见匿名进度。
- [ ] 员工登记与进行中分配重叠的不可用日期。经理收到影响通知，核对天数、项目下游及方案页入口，再比较登记前后的分配人员、投入、起止日期和承诺，均保持原值。
- [ ] 经理为已完成分配写反馈，员工打开「我的画像」的待处理请求，查看反馈并提交。有经理确认要求的能力由经理批准，再用「按能力找人」查到该员工。另用免确认能力核对本人提交后为「暂定」，并检查反馈来源未改变证据数和最近证据日期。
- [ ] 经理在网页助手与飞书分别按姓名询问管辖内员工的周负荷，再询问管辖外员工。两端对管辖外均给出「不在你的管辖范围或没有找到这个人」，不披露个人数据。
- [ ] **E38(8) 第 5 条**。调岗前分别在网页和飞书打开各自会话，询问将调出员工的周负荷，留存两张基线截图。调岗并确认管辖范围收窄后，回到**同一个网页原会话**核对旧受限回复隐藏，再追问并截图；回到**同一个飞书原会话**，核对续聊和新回复按当前权限过滤并截图，记录此前实际发到飞书的消息显示情况。留存网页前／后与飞书前／后四张实机截图。两端历史各自保存，不做跨端回放。
- [ ] **D-025**。分别为一出一进、多人进出创建变更，均从 A 方打开「安排变更提议」查看。前者前后共用匿名序号并标出替换，后者分别标「移除」「新增」且保留未换人员的序号，两种均不显示人员姓名。
- [ ] B 方在方案页核对「优化方案」及条件，确认仅生成方案时没有新增预占、审批或分配。由有权用户按原流程答复并确认，再核对预占与执行安排。
