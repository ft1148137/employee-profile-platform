# M9 验收记录

阶段审查基线为 `master 236b68fed78ad969bba73e6fc2b71e3a41b1c778`（2026-11-18，北京时间），包含 C5 / PR #367。父 issue 为 [NERE-639](mention://issue/01a1de41-e0de-76f4-a626-40c9e9b7ec68)，记录任务为 NERE-787。[阶段审查基线检查](https://github.com/ft1148137/employee-profile-platform/commit/236b68fed78ad969bba73e6fc2b71e3a41b1c778/checks)

已有自动化用例的结果见下表。Reviewer 在阶段审查基线发现四项业务缺口与初始画像 Playwright 失败；补证代码基线为 `master 3aea6591a13a6b036f44f58de886e4d138c972ef`（2026-11-18，北京时间），包含全部四项阶段修复（NERE-794～796、NERE-798）。[补证基线检查](https://github.com/ft1148137/employee-profile-platform/commit/3aea6591a13a6b036f44f58de886e4d138c972ef/checks) 四个业务回归及补证基线的五项 master 工作流（含后端三分片与 Playwright 三分片）均已全部通过。真实模型兜底后达标，原推荐仍有未达标案例。发布准备的证据覆盖源码许可和自签 HTTPS，制品再分发核验、公网证书实测和公开发布仍待处理。M9.1 人工回归见文末。

## 依据与交付

第 2 阶段核对前的 14 个前置子 issue 均为 done，16 个关联 PR 均已合并，必需检查通过；阶段审查后补齐的 4 个修复子 issue（NERE-794、NERE-795、NERE-796、NERE-798）也均已 done 且 PR 合并通过。#352、#353、#356、#359 的 Playwright 分片按路径规则跳过，汇总门禁成功。功能测试采用各 PR 与最新 master CI 结果，真实模型与 HTTPS 实测沿用原报告。本轮只核对文档、源码和检查记录，未在本地复跑 pytest、Playwright、Docker、真实飞书或真实模型。

审查基线 `236b68fe` 的合并后 Playwright 在 Chromium shard 2 的初始画像流程失败（由 NERE-796 跟踪）。文档 PR #368 合并提交 `3cbec709` 的 Playwright 汇总通过（浏览器分片按文档路径跳过）、Compose 通过，Backend 分片 3 失败。随后 PR #370（`83522f79`）在验收表补标待修例外；PR #371（`538c7828`）合并后的 master 运行因新提交合入被取消；PR #372（`2e1fc34f`）的 master Playwright 三分片全部实跑通过，同提交 Backend 分片 3 因测试查询未限定需求且无序失败（由 NERE-798 跟踪）。PR #373 合并至 `3aea6591` 后，master 五项 push 工作流全部通过。[236b68fe 失败运行](https://github.com/ft1148137/employee-profile-platform/actions/runs/44240652893) · [3cbec709 汇总](https://github.com/ft1148137/employee-profile-platform/actions/runs/44242736634) · [3cbec709 Backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/44242736679) · [3aea6591 后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122258) · [3aea6591 网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122184) · [3aea6591 Compose](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122213) · [3aea6591 Pi](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122143) · [3aea6591 Zizmor](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122250)

| 子 issue | 交付与 PR | CI 证据 |
| --- | --- | --- |
| NERE-775 S1 | [#355](https://github.com/ft1148137/employee-profile-platform/pull/355) 校验器规则兜底和原方案链接 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44196244618)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44196244704) |
| NERE-776 E1 | [#359](https://github.com/ft1148137/employee-profile-platform/pull/359) 真实模型双模式评测报告 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44203345664)、[预检](https://github.com/ft1148137/employee-profile-platform/actions/runs/44203345739) |
| NERE-777 S2 | [#354](https://github.com/ft1148137/employee-profile-platform/pull/354) 接力锁定与方案页共用夹具 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44193456747)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44193456751) |
| NERE-778 S3 | [#360](https://github.com/ft1148137/employee-profile-platform/pull/360) 工具读取范围与 A 方正文提示 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44204060504) |
| NERE-779 S4 | [#361](https://github.com/ft1148137/employee-profile-platform/pull/361) 飞书发送超时与 worker 并发断言 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44207039572) |
| NERE-780 R1 | [#352](https://github.com/ft1148137/employee-profile-platform/pull/352) 许可证、贡献指南和发布草稿 | [预检](https://github.com/ft1148137/employee-profile-platform/actions/runs/44191657452) |
| NERE-781 R2 | [#353](https://github.com/ft1148137/employee-profile-platform/pull/353)、[#356](https://github.com/ft1148137/employee-profile-platform/pull/356) 自签 HTTPS 实测、项目隔离和 HTTP-01 补交 | [#353 安装](https://github.com/ft1148137/employee-profile-platform/actions/runs/44191197547)、[#356 安装](https://github.com/ft1148137/employee-profile-platform/actions/runs/44194490182) |
| NERE-782 C1 | [#357](https://github.com/ft1148137/employee-profile-platform/pull/357) 方案提议、预占、原子承诺和迁移 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44196995270) |
| NERE-783 C2 | [#358](https://github.com/ft1148137/employee-profile-platform/pull/358) 网页方案协商和冲突提示 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44202651955)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44202652258) |
| NERE-784 C3 | [#363](https://github.com/ft1148137/employee-profile-platform/pull/363) 助手及飞书确认卡、MCP 承诺保护 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44211268436) |
| NERE-785 C4 | [#366](https://github.com/ft1148137/employee-profile-platform/pull/366) 借调审批和匿名分配 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44234087933)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44234087895) |
| NERE-786 C5 | [#367](https://github.com/ft1148137/employee-profile-platform/pull/367) 已生效安排的正式变更 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44239173978)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44239173900) |
| NERE-788 隐私修复 | [#364](https://github.com/ft1148137/employee-profile-platform/pull/364)、[#365](https://github.com/ft1148137/employee-profile-platform/pull/365) 自由文本与升级前会话保护 | [#364 后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44214095349)、[#365 后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44217580596) |
| NERE-789 接力修复 | [#362](https://github.com/ft1148137/employee-profile-platform/pull/362) 全程参与者与接力共存 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44211869389) |
| NERE-794 提议轮次和审批修复 | [#372](https://github.com/ft1148137/employee-profile-platform/pull/372) 普通答复轮次、审批来源和借调跨方拒绝，合并提交 `2e1fc34f` | [后端重跑](https://github.com/ft1148137/employee-profile-platform/actions/runs/44244499661/attempts/2)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44244499627) |
| NERE-795 开始日期修复 | [#369](https://github.com/ft1148137/employee-profile-platform/pull/369) 已承诺段的开始日期走正式变更，合并提交 `65acbc0f` | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44242388416)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44242388420) |
| NERE-796 初始画像浏览器回归 | [#371](https://github.com/ft1148137/employee-profile-platform/pull/371) 假模型按工具分流与重试数据隔离，合并提交 `538c7828` | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44244046880)、[网页重跑](https://github.com/ft1148137/employee-profile-platform/actions/runs/44244047067/attempts/2) |
| NERE-798 MCP 事件来源测试修复 | [#373](https://github.com/ft1148137/employee-profile-platform/pull/373) 限定需求与排序并独立断言 MCP 来源，合并提交 `3aea6591` | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44249884159)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44249884225) |
| NERE-799 降级历史保护（E41／D-024） | [#375](https://github.com/ft1148137/employee-profile-platform/pull/375) `c782001bc001` `downgrade()` 并发锁与全库协商／审批历史阻断，合并提交 `e5c38c38` | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44260062786)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/44260062844)，及本地独立测试库 9 项迁移与降级用例通过（详见下文） |

### 初始画像与主干 CI 补证

NERE-796 复现了部门能力默认值调用 `submit_department_capabilities` 被旧假模型当作画像起草处理、访问不存在的 `records`，以及原重试复用首轮已被占用邮箱而收到 422。#371 按工具名区分部门能力默认值和画像起草，保留原合成邮箱完成 Jira 自动关联，再用每次运行专用邮箱签发账号。修复 PR 的 Chromium shard 2 报告与后续主干实跑中，[initial-profiles.spec.ts](../frontend/tests/initial-profiles.spec.ts) 的「the fake model accepts department defaults without drafting records」与「employee reviews initial profile and updates on desktop and mobile」均实跑通过。[PR Playwright 运行](https://github.com/ft1148137/employee-profile-platform/actions/runs/44244047067/attempts/2)

#371 合并提交 `538c7828` 的 master 运行因新提交合入被取消。[取消的主干 Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/44246037538) 随后提交 `2e1fc34f` 的 master Playwright 三个分片全部实跑并通过，同提交 `Test Backend` 分片 3 因 `test_mcp_can_release_pending_proposal_without_accepting[follow_up]` 读到先前的 `web` 事件而失败（无 `ORDER BY` 且未限定需求，由 NERE-798 跟踪）。[2e1fc34f 主干 Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/44246496911) · [2e1fc34f 主干后端失败](https://github.com/ft1148137/employee-profile-platform/actions/runs/44246496816)

#373 在 [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 为测试辅助查询补齐需求过滤与确定性排序，并在 [test_m9_proposal_entries.py](../backend/tests/api/routes/test_m9_proposal_entries.py) 保留先前 `web` 的 `REPLIED` 事件后按操作类型独立断言 `via == "mcp"`（覆盖 `follow_up` 与 `withdraw`）。合并到补证基线 `3aea6591` 后，master 五项 push 工作流全部通过（`Test Backend` 三分片与汇总、`Playwright Tests` 三分片与汇总、`Test Docker Compose`、`Test Pi research sample`、`Zizmor`）。[3aea6591 主干后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122258) · [3aea6591 主干 Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/44251122184)

## 父 issue 验收逐条对照

| 验收 | 测试文件和用例 | 实际结果与边界 |
| --- | --- | --- |
| 1. 方案提议、接受及审批，保留各方决定和理由 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_reply_with_option_creates_hold`、`test_current_assignment_constraint_rejects_valid_segments`、`test_proposer_cannot_approve_as_requesting_manager`、`test_rejection_and_withdraw_are_idempotent`；[test_m9_proposal_entries.py](../backend/tests/api/routes/test_m9_proposal_entries.py) 的 `test_human_confirms_proposal_then_acceptance`、`test_model_has_no_direct_commitment_tools`、`test_mcp_accept_guard_and_legacy_flow`、`test_mcp_can_release_pending_proposal_without_accepting` | #357、#363、#373 后端 CI 通过。B 方有权代表提交即记 B 方审批，A 方接受后普通方案生效。提议人不能再替 A 方审批，拒绝理由随决定保留。网页或助手确认卡由本人确认，MCP 不能接受待处理的方案提议，追问／撤回可释放待处理提议并独立保留 `web` 与 `mcp` 来源。卡片测试使用合成输入和适配器。历史提议后改走普通答复的轮次兼容、确认卡审批来源见 1a、1b |
| 1a. 历史提议终结后处理本轮普通答复 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_response_uses_new_plain_reply_after_proposal_ends` | 基线 `236b68fe` 上，本轮普通答复仍可能被上一轮终态提议拦住。**#372 修复，后端 CI 五个参数场景通过。** 拒绝、过期或 B 方撤回旧提议后，A 方可接受新普通答复，B 方可手工分配。另验证普通追问和撤回可完成，旧版本重放仍返回 `proposal_decided` |
| 1b. 审批保留实际入口 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_reply_with_option_creates_hold`；[test_m9_proposal_entries.py](../backend/tests/api/routes/test_m9_proposal_entries.py) 的 `test_human_confirms_proposal_then_acceptance` | 基线 `236b68fe` 上，飞书确认的答复事件记为 `feishu`，资源方审批错误记为 `web`。**#372 修复，后端 CI 三个入口通过。** 网页表单、网页助手确认和飞书回调分别记录 `web`、`bot`、`feishu`，资源方审批与同轮答复事件一致 |
| 2. 临时预占及到期释放 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_expired_hold_is_released_without_worker`；[test_m9_proposal_display.py](../backend/tests/api/routes/test_m9_proposal_display.py) 的 `test_weekly_hold_details_expire_and_exclude_together`；[m9-proposal.spec.ts](../frontend/tests/m9-proposal.spec.ts) 的「按方案答复预占，接受后逐段生效并通知员工」 | #357、#358 CI 通过。初次提议的预占有效期为 72 小时，期间计入每周占用，尚无正式分配。到期即从容量计算排除，周期任务补记失败状态。浏览器验证预占显示与转为分配，到期分支由后端覆盖 |
| 3. 最终复核、原子承诺、防重复提交 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_invalid_or_stale_option_never_holds`、`test_recheck_failure_is_persisted`、`test_current_assignment_constraint_rejects_valid_segments`；[test_m9_proposal_concurrency.py](../backend/tests/api/routes/test_m9_proposal_concurrency.py) 的 `test_propose_rechecks_latest_snapshot_after_employee_lock`、`test_accept_competes_with_one_terminal_transition`、`test_accept_rechecks_expiry_after_waiting_for_employee` | #357 后端 CI 通过。旧快照被拦，在职、容量或权限变化可使接受失败，锁内再次检查到期。多段一次生成，每个提议行只生成一条分配。并发和回滚断言见下一节 |
| 4. 资源冲突提示 | [test_m9_proposal_concurrency.py](../backend/tests/api/routes/test_m9_proposal_concurrency.py) 的 `test_competing_proposals_do_not_overbook`；[test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_accept_conflict_hides_unreadable_demand_source`；[m9-proposal.spec.ts](../frontend/tests/m9-proposal.spec.ts) 的「同周争抢给出来源和超出比例，撤回后旧确认显示当前状态」 | #357、#358 CI 通过。争用同一员工同一周返回 409，界面可显示超出比例和可读来源。无权看的来源只显示「其他需求」，不回传人名、员工 ID 或需求原文 |
| 5. 方案被拒后待重新规划 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_rejection_and_withdraw_are_idempotent`、`test_rejection_reason_limit_and_revision`；[m9-proposal.spec.ts](../frontend/tests/m9-proposal.spec.ts) 的「拒绝理由必填，拒绝后可重新规划且释放预占」 | #357、#358 CI 通过。A 方须填拒绝理由，初次提议失败并释放预占，需求回到「待答复」。B 方可点「重新规划」。变更被拒时原承诺继续生效。旧提议终结后的新一轮普通答复见 1a，借调跨方拒绝见 8a。 |
| 6. 审批后通知、审计和分配 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_commit_audit_notifications_and_privacy`；[m9-proposal.spec.ts](../frontend/tests/m9-proposal.spec.ts) 的「按方案答复预占，接受后逐段生效并通知员工」 | #357、#358 CI 通过。答复、接受和分配有审计，A、B 和员工有站内通知，同一员工多段逐段落库。A 方无 B 方个人标识。本项验证站内通知，真实飞书投递未重测。确认卡产生的资源方审批来源见 1b。 |
| 7. 已生效安排走正式变更 | [test_m9_change.py](../backend/tests/api/routes/test_m9_change.py) 的 `test_switch_person_changes_commitment_only_after_acceptance`、`test_changes_replace_only_different_segments`、`test_change_can_add_a_person_then_remove_them`、`test_change_can_end_before_the_original_window`、`test_direct_edits_of_a_committed_segment_require_a_change`；[m9-change.spec.ts](../frontend/tests/m9-change.spec.ts) 的「资源方提交换人，需求方匿名确认后替换原分配」 | #367 CI 通过。B 发提议、A 比较匿名的前后安排后接受。相同段保留，变化段在同一事务取消或新建，包含投入变化。拒绝、过期或复核失败时保留原承诺。延期审批、标记完成、取消离职员工分配及旧手工流程沿用原入口。已承诺段的开始日期调整见 7a。 |
| 7a. 已承诺分配的开始日期须经正式变更 | [test_m9_change.py](../backend/tests/api/routes/test_m9_change.py) 的 `test_committed_start_moves_to_second_week_only_after_acceptance`；[m9-change.spec.ts](../frontend/tests/m9-change.spec.ts) 的「已承诺分配从行内发起变更，旧手工分配仍能直接保存开始日期」 | 基线 `236b68fe` 上，B 方可直接推迟已承诺段的开始日期，提前释放投入。**#369 修复，后端和 Playwright 用例均通过。** 直接 PATCH 返回 409，原段、需求详情与版本、业务事件和周负荷保持原值。A 方接受正式变更后才替换该段并释放第一周投入。网页已承诺行提供「发起开始日期变更」，手工分配仍可直接保存开始日期 |
| 8. 借调须原部门经理和本人同意，拒绝保护及关系不变 | [test_m9_borrow.py](../backend/tests/api/routes/test_m9_borrow.py) 的 `test_borrow_consent_order_and_projections`、`test_decline_is_private_and_leaves_profiles_unchanged`、`test_borrow_failure_releases_holds`；[m9-borrow.spec.ts](../frontend/tests/m9-borrow.spec.ts) 的「借调双方同意后生成匿名分配」「本人不同意后仅本人看得到理由」 | #366 CI 通过。B 提议后须原部门经理选人、本人同意、A 接受，最后一方同意时生成分配。部门归属、直属上级和画像审批关系沿用原关系；后端断言核对原部门与画像审批路由。本人不同意时其他方只看到「借调未达成」，本人理由不进审计详情，画像、证据、找人、覆盖率和导出结果保持原样。拒绝不用于考核，产品没有考核功能。B 方在生效分配里仍看到匿名「借调 1 人」、投入和日期。跨方拒绝见 8a |
| 8a. 借调拒绝遵守一人一方 | [test_m9_borrow.py](../backend/tests/api/routes/test_m9_borrow.py) 的 `test_borrow_party_cannot_also_reject_as_requesting_manager` | 基线 `236b68fe` 上，兼管 A 方的原部门经理在选人后仍可代表 A 方拒绝并释放整单预占。**#372 修复，后端 CI 两个参数场景通过。** 已选人的原部门经理和已同意的员工尝试跨方拒绝均返回 403，审批、预占、事件、提议和需求版本保持原值。独立 A 方仍可拒绝并释放预占 |
| 9. 发布准备 | [许可证核验](licenses.md)、[HTTPS 实测](m9-https-test.md)；[test_https_config.py](../deploy/test_https_config.py) 的 `test_project_isolation_and_streaming`、`test_acme_http_challenge_and_storage`、`test_acme_port_validation`；[贡献指南](../CONTRIBUTING.md)、[发布内容草稿](release-draft.md) | #352、#353、#356 门禁通过。许可证和发布文档按原报告验收；HTTPS 配置测试及浏览器实测采用 R2 本地证据，配置测试尚未接入 CI。发布边界见下文 |

## 重点验收：双方审批、并发与全有或全无

| 场景 | 测试文件和用例 | 实际结果 |
| --- | --- | --- |
| A、B 两个部门都审批后才正式锁定 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_reply_with_option_creates_hold`、`test_current_assignment_constraint_rejects_valid_segments`、`test_proposer_cannot_approve_as_requesting_manager` | #357 后端 CI 通过。B 提议仅预占，员工没有分配；独立 A 方接受后预占转成正式分配，并保留两个审批方 |
| 竞争同一人同周、重复确认 | [test_m9_proposal_concurrency.py](../backend/tests/api/routes/test_m9_proposal_concurrency.py) 的 `test_competing_proposals_do_not_overbook`、`test_accept_competes_with_one_terminal_transition`、`test_accept_and_other_assignment_extension_cannot_overbook` | #357 后端 CI 通过。独立 PostgreSQL 连接下两条冲突提议一条成功、一条 409；并发接受仅一方成功，只生成一次分配。与另一分配的延期竞争也不会超额 |
| 到期与接受竞争 | [test_m9_proposal_concurrency.py](../backend/tests/api/routes/test_m9_proposal_concurrency.py) 的 `test_accept_and_expired_worker_only_fail_once`、`test_accept_rechecks_expiry_after_waiting_for_employee` | #357 后端 CI 通过。过期接受返回 409，无分配，只记一次失败；等待员工锁期间到期同样阻止提交 |
| 部分审批后复核失败，或写到第二段时异常 | [test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_recheck_failure_is_persisted`、`test_unexpected_second_assignment_error_rolls_everything_back` | #357 后端 CI 通过。已有 B 方审批时，复核失败会释放预占、记录失败，分配数为零。注入第二段写入异常时事务全部回滚，保留原待处理提议及原 B 方审批 |
| 借调竞争、变更竞争与替换回滚 | [test_m9_borrow.py](../backend/tests/api/routes/test_m9_borrow.py) 的 `test_two_borrows_cannot_overbook_worker`、`test_each_borrow_line_requires_a_different_person`、`test_borrow_failure_releases_holds`；[test_m9_change.py](../backend/tests/api/routes/test_m9_change.py) 的 `test_changes_replace_only_different_segments`、`test_extension_and_change_compete_for_the_same_segment`、`test_change_from_and_to_a_borrowed_employee` | #366、#367 后端 CI 通过。借调竞争不能超额，一条借调失败释放整单预占。只改第二段、两段并一段均覆盖并发和故障注入，确认最多替换一次，异常后保留原分配。新借调的变更等待原部门和本人同意 |

C1 的迁移唯一 head 为 `c782001bc001`。[test_m9_proposal.py](../backend/tests/api/routes/test_m9_proposal.py) 的 `test_m9_migration_roundtrip_with_seed` 和 `test_downgrade_refuses_multiple_active_segments` 随 #357 后端 CI 通过；NERE-799（E41／D-024）在 `c782001bc001` 的 `downgrade()` 补齐五表 `ACCESS EXCLUSIVE` 并发锁与协商／审批历史阻断后，本地隔离 PostgreSQL 测试库（端口 `15799`）实测以下 9 项迁移／降级用例全部通过：
- `test_m9_migration_roundtrip_with_seed`、`test_downgrade_allows_pre_m9_compatible_records`：空库及含兼容旧数据（无 `proposal_line_id` 关联、含 `commitment_changed` 事件）的库可降级至 `c696001bc001` 并保留旧记录，再重新升级至 `c782001bc001`。
- `test_downgrade_refuses_multiple_active_segments`：保留多段进行中分配阻断与原有诊断，完整保留分配及提议记录。
- `test_downgrade_refuses_single_segment_with_proposal_and_approvals`：单段进行中分配关联提议行并含提议、逐方审批与协商事件时降级报错，另开事务核对迁移版本（`c782001bc001`）、各表记录内容、条数及 `proposal_line_id` 关联完整保留。
- `test_downgrade_refuses_targeted_history_without_active_segments`（`[committed]`、`[proposal_failed]`、`[done_assignment]`、`[pending_proposal]`）：仅含目标事件（`committed` 或 `proposal_failed`）、已结束分配关联提议行、或无分配的提议与审批时均阻断降级并完整保留原记录。
- `test_downgrade_blocks_concurrent_m9_write_before_checking`：并发写入未提交期间，`downgrade()` 阻塞在锁等待上，待写入提交后检出 `committed` 事件并拒绝降级。
D1 与 NERE-799 均未连接或升级长期演示环境。

## 前序事项与阶段修复

| 事项 | 测试或记录 | 实际结果与边界 |
| --- | --- | --- |
| E37(1) 校验器兜底与真实模型 | [test_m9_validator_fallback.py](../backend/tests/api/routes/test_m9_validator_fallback.py) 的 `test_worse_recommendation_keeps_both_choices`、`test_equal_or_better_conclusion_has_no_fallback`；[m9-validator-fallback.spec.ts](../frontend/tests/m9-validator-fallback.spec.ts) 的「较差推荐同时展示规则方案、差别和两个可打开的链接」；[评测报告](m9-coordination-eval.md) | #355 CI 通过。Agent 推荐较差时用同快照补规则方案，标差异并保留两条可点击链接；网页走真实点击，飞书覆盖消息渲染。#359 记录真实模型结果，见下节 |
| E37(2) / D-021 结构化读取和自由文本会话失效 | [test_m9_session_reads.py](../backend/tests/api/routes/test_m9_session_reads.py) 的 `test_employee_change_checks_only_reads_with_conservative_fallback`；[test_m9_coordination_read_privacy.py](../backend/tests/api/routes/test_m9_coordination_read_privacy.py) 的 `test_reply_prose_without_case_invalidates_inherited_context`、`test_upgrade_safeguards_saved_coordination_context`、`test_upgrade_preserves_precise_scope_for_new_structured_reads`、`test_proposal_reads_track_each_returned_employee_when_complete` | #360、#364、#365 后端 CI 通过。结构化工具结果按实际读取人员跟踪。无 Planning case 时，答复人仍可见也不能代表自由文本提及的员工，无法证明完整读取范围则保守失效。旧的无版本标记部分集合也保守失效。网页和飞书覆盖员工调出／离职后的历史隐藏、Pi 状态清空、无工具续聊与待发拦截。更早的完整集合及新格式精确读取按相应规则处理 |
| E37(3) / D-022 A 方隐藏答复条件 | [test_m9_session_reads.py](../backend/tests/api/routes/test_m9_session_reads.py) 的 `test_a_side_gets_hidden_reply_notice_and_details_link`；[test_m9_proposal_entries.py](../backend/tests/api/routes/test_m9_proposal_entries.py) 的 `test_coordination_proposal_projection_and_private_reads`；[test_m9_coordination_read_privacy.py](../backend/tests/api/routes/test_m9_coordination_read_privacy.py) 的 `test_pending_proposal_cannot_reintroduce_hidden_reply` | #360、#363、#364 后端 CI 通过。A 方协调工具提示「没有读取对方答复正文里的条件，请到需求详情查看」并返回详情入口。提议嵌套的 `conditions` 同样不回传隐藏正文，覆盖协调上下文与审批状态工具；网页详情继续按现有规则展示条件 |
| E37(4) / D-023 飞书发送总时限与持锁 | [test_m9_feishu_send_timeout.py](../backend/tests/api/routes/test_m9_feishu_send_timeout.py) 的 `test_send_releases_connection_and_pending_role_change`、`test_committed_role_change_blocks_pending_content` | #361 后端 CI 通过。保留按企业顺序，鉴权和发送共用 10 秒上限，完成、异常或超时后释放连接；已提交的权限变化阻止旧内容发出。测试以合成适配器覆盖交付路径 |
| E37(5) / E38(6) / D-018 接力锁定与接力修复 | [test_m9_relay_phased.py](../backend/tests/api/routes/test_m9_relay_phased.py) 的 `test_simulated_relay_revisions_keep_approval`、`test_validator_recognizes_relay_without_kind`；[test_m9_relay_parallel.py](../backend/tests/api/routes/test_m9_relay_parallel.py) 的 `test_rest_parallel_relay`、`test_relay_keeps_more_severe_verdicts`、`test_rest_short_assignment_starting_at_minimum_date` | #354、#362 后端 CI 通过。从规则模拟器生成两人接力，锁定及再校验后仍要求需求方同意。反例为甲全程参与、乙接前半、丙接后半，修复后识别为分阶段；同样覆盖三段接力、普通全程并行、容量超额及最小日期。未新增按阶段换人的编辑能力 |
| E37(6) / D-019 跨浏览器首次打开重新生成快照 | 父 issue 的 M9 规划与 E37(6) 决定；[m9-validator-fallback.spec.ts](../frontend/tests/m9-validator-fallback.spec.ts) 的「较差推荐同时展示规则方案、差别和两个可打开的链接」 | 按决定搁置通用首次打开行为。#355 通过的浏览器回归只验证带明确方案链接时清除本地状态后仍读取原快照、且不新建快照 |
| E37(7) worker 告警并发与方案页共用夹具 | [test_im_gateway_concurrency.py](../backend/tests/domains/test_im_gateway_concurrency.py) 的 `test_maintenance_pool_wait_does_not_block_loop`；[m8-plan-page.spec.ts](../frontend/tests/m8-plan-page.spec.ts) 的「B 方经理无需模型查看方案，A 方经理看不到入口」及 [m8-plan-revise.spec.ts](../frontend/tests/m8-plan-revise.spec.ts) 的「B 方经理快捷换人并锁定安排」 | #361 后端 CI 通过，验证等待数据库池时事件循环心跳推进、worker 告警完成，且无 `pool_timeout` 或存储错误。#354 网页 CI 通过，两份 spec 复用 [方案夹具](../frontend/tests/utils/plans.ts) |
| M8.2 借调条件文字 | [test_m9_borrow.py](../backend/tests/api/routes/test_m9_borrow.py) 的 `test_borrow_confirmation_card_has_department_conditions` | #366 后端 CI 通过。从借调方案生成并确认答复卡，方案条件和卡片均写明原部门经理与本人同意，无「余量」或内部里程碑文字 |

## 真实模型业务结论

「本轮 **Agent + 校验器（兜底后）结论不比规则引擎差达到 34/34**；兜底后未达标案例：无。原推荐结论为 26/34，纯 Agent 为 16/34。两种做法均出现 2 个新人方案，达到 M7 的数量基线。」原推荐仍有未达标案例。报告分别记录原违反约束（纯 Agent 18/34，Agent + 校验器 11/34）、经理修改、模型和工具调用及耗时，均按原运行口径统计。68 条 `passed` 表示评测运行完成。本轮文档任务未重跑模型。[M9 真实模型评测结论](m9-coordination-eval.md#e1-结论与历史对比)

## 发布准备

- 项目根许可证已更新为 Apache-2.0，模板 MIT 版权全文保留，并有 `NOTICE`。锁文件核验覆盖 614 条包记录，当前分离使用方式下未发现要求项目自身改用 GPL 或 AGPL 的依赖。包括 psycopg-pool 的 LGPL-3.0-only 在内，各非宽松组件仍须满足自己的条款；镜像实际 digest、内部软件和内嵌库的完整清点留待二进制再分发前核验。[依赖许可证核验](licenses.md)
- 自签 HTTPS 实测通过重定向、Bearer 登录／退出、有效收件箱链接、MCP HTTPS、备份／同提交升级和 Traefik 项目隔离。流式接口实测为模型未配置时的 NDJSON final 事件，真实模型多段流留到回归核验。域名证书只核对配置与 HTTP-01 端口约束，尚未验证公网签发、续期和客户端信任。[HTTPS 实测记录](m9-https-test.md)
- 贡献指南、README 全文、仓库简介和建议的 topics 已备好。根目录 README、仓库简介和 topics 等大古决定公开发布时再上。[贡献指南](../CONTRIBUTING.md) · [发布内容草稿](release-draft.md)

## M9.1 回归检查清单

在长期演示环境升级前先备份，记录回归基线、各入口使用的角色以及实际结果。以下项目留给 M9.1 执行。

### E38(8) 带入的六项

- [ ] 构造缺少周确认的需求，方案显示「数据不足」。
- [ ] 补齐周确认后点「重新模拟」，方案恢复可用。
- [ ] 在网页助手方案卡真实点击方案链接，落到对应需求和原方案。
- [ ] 由员工提交一条真实延期申请，有权审批人拒绝，确认分配日期保持原值。
- [ ] 网页原会话与飞书原会话各自验证人员范围收窄后的旧回复隐藏与新回复过滤（两端历史按设计不互通，不做跨端回放）。
- [ ] 造出失效的旧方案，看到「数据已变化，请重新模拟」，并确认旧方案不能继续提交。

### M9 新增

- [ ] B 方按方案答复后只有预占，A 方接受后才锁定并生成分配；核对两方审批、时间线和通知。
- [ ] 两人同时为同一员工同一周发提议；只有一条成功，冲突提示说明超出比例，来源按权限显示。
- [ ] 预占到期，确认该周占用释放且旧提议无法接受。
- [ ] 两个页面重复点「接受方案」及「确认接受」，只生成一组分配。
- [ ] B 方已审批后使一项复核失败，核对整单无半截分配且全部预占释放。
- [ ] A 方填写理由拒绝方案，B 方看到「重新规划」并能进入方案页。
- [ ] 借调分别走同意和不同意：原部门经理选人，员工本人处理，A 方接受。成功后 B 方只看匿名分配；不同意后其他人只看「借调未达成」，本人仍可见自己的理由。
- [ ] B 方发起已生效安排的换人变更，A 方核对匿名的前后安排并接受，确认原分配「已取消」、新分配生效。
- [ ] 在网页和真实飞书查看校验器兜底卡，核对两条方案、差别和各自链接。
- [ ] 分别触发提议到期和最终复核失败，验证「已过期」「复核未通过」及「重新规划」的实际界面。此两条是 C2 未做浏览器 E2E 的分支。
- [ ] 在 HTTPS 部署登录并发送助手消息，确认真实模型分段回复经代理及时显示。
