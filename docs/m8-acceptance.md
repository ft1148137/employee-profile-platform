# M8 验收记录

核对基线：`master 351d82c1f5fdf99acbf2ab74cec09175a545b639`，2026-11-17，已包含 NERE-750 / PR #339。父 issue [NERE-638](mention://issue/01a1de41-dbd3-75f8-9eae-8059c8d7c71e)，记录任务 NERE-742。本任务只改文档；功能测试采用下列已合并 PR 的 CI 证据，本轮未在本地复跑 pytest、Playwright 或真实模型。

**验收结论：文档交付不等于 M8 全部验收通过。** 功能交付与既有越权测试的 CI 通过；真实模型未达到 M7 业务基线，会话隐私还有 NERE-752 未通过项。后续修复和 M8.1 回归以各自的新证据核对。

## 依据与交付

下列 PR 均已合并，核对时每个 PR 的 14 项 checks 均为 `SUCCESS`。表中链接直达相应后端或网页 CI；不是本任务本地运行结果。

| 子 issue | 交付与 PR | CI 证据 |
| --- | --- | --- |
| NERE-736 S1 | [#330](https://github.com/ft1148137/employee-profile-platform/pull/330)：离职固定错误码与随机密码文案检查 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43890271379)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/43890271359) |
| NERE-737 S2 | [#331](https://github.com/ft1148137/employee-profile-platform/pull/331)、[#332](https://github.com/ft1148137/employee-profile-platform/pull/332)：中文标签、当前权限内姓名及方案直达链接 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43895953502)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/43895953048) |
| NERE-738 S3 | [#333](https://github.com/ft1148137/employee-profile-platform/pull/333)：快捷修改、同快照重新校验与差异 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43901250048)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/43901250100) |
| NERE-739 S4 | [#334](https://github.com/ft1148137/employee-profile-platform/pull/334)：协调工具、决策循环、提案确认卡及 ADR-0047 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43903646813) |
| NERE-745 阶段修复 | [#335](https://github.com/ft1148137/employee-profile-platform/pull/335)：降低投入的确认角色、分阶段改期去重 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43905152304) |
| NERE-740 S5 | [#336](https://github.com/ft1148137/employee-profile-platform/pull/336)：需求理解卡、方案卡、按角色显示和手工入口 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43915918475)、[网页](https://github.com/ft1148137/employee-profile-platform/actions/runs/43915918501) |
| NERE-746 F1 | [#337](https://github.com/ft1148137/employee-profile-platform/pull/337)：角色／部门范围收窄后的历史回复与 Pi 状态保护 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/43923802142) |
| NERE-741 S6 | [#338](https://github.com/ft1148137/employee-profile-platform/pull/338)：34 案例双模式评测、真实结果与未知调用计数修订 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44095876730)；[真实模型报告](m8-coordination-eval.md) |
| NERE-750 契约修复 | [#339](https://github.com/ft1148137/employee-profile-platform/pull/339)：模型可见参数契约、合成样本与失败分类 | [后端](https://github.com/ft1148137/employee-profile-platform/actions/runs/44100252004)；[独立验证](m8-coordination-eval.md#nere-750-独立验证2026-11-17) |

## 父 issue 五条验收

测试路径均相对仓库根目录，结果对应上表 CI。功能断言通过不代表真实模型业务效果达标。

| 验收 | PR、证明它的测试文件或 spec | 实际结果与边界 |
| --- | --- | --- |
| 1. 共用 Pi 协调循环，AI 不替人承诺 | #334、#339；[test_m8_coordination_entries.py](../backend/tests/api/routes/test_m8_coordination_entries.py) 的 `test_definitions_and_forbidden_operations`、`test_complete_bot_loop`、`test_bot_privacy_and_proposal_card`；[test_nere750_coordination_contract.py](../backend/tests/api/routes/test_nere750_coordination_contract.py) | CI 通过。网页／飞书复用 `bot.core.turn`，工具以本人 Principal 调服务。模拟只写 Planning，提案先出 `demand_reply` 卡，点确认才答复；没有分配、改分配日期、批准延期的工具。模型定义不超过 29，六个子操作的参数来自既有 schema，首次模拟只需需求 ID。循环测试用假模型，不能证明真实模型达到业务基线 |
| 2. A 方部门级结果、B 方权限内人员，四入口越权 | #331、#334、#336、#337；下表的四入口测试及 [test_m8_session_scope.py](../backend/tests/api/routes/test_m8_session_scope.py) | 既有 CI 通过：跨企业、范围外、A 方读取 B 方方案被拒绝；个人字段不进入 A 方工具结果。**隐私整体验收未通过**：角色不变时员工调出／离职的历史缓存缺口见 NERE-752，不由这些测试覆盖 |
| 3. 自然语言需求、可读方案卡、快捷修改与增量差异 | #331～#333、#335、#336；[test_m8_plan_view.py](../backend/tests/api/routes/test_m8_plan_view.py)、[test_m8_plan_revise.py](../backend/tests/api/routes/test_m8_plan_revise.py)、[test_m8_s1_review_fixes.py](../backend/tests/api/routes/test_m8_s1_review_fixes.py)、[test_m8_coordination_cards.py](../backend/tests/api/routes/test_m8_coordination_cards.py)；[m8-plan-page.spec.ts](../frontend/tests/m8-plan-page.spec.ts)、[m8-plan-revise.spec.ts](../frontend/tests/m8-plan-revise.spec.ts)、[m8-coordination-chat.spec.ts](../frontend/tests/m8-coordination-chat.spec.ts) | CI 通过。需求卡标记待确认字段、最多两个问题；方案卡数值与 OptionPublic 对照。换人、锁定／解锁、改时间、降低投入、分阶段都在同快照生成自定方案，保留原方案及差异。需求方、B 方代表和员工返回内容不同；不是只靠隐藏按钮控权 |
| 4. 模型或工具失败仍可手工完成 | #334、#336；`test_m8_coordination_entries.py` 的 `test_tool_failure_rolls_back_partial_plan`、`test_model_failure_discards_plan`；`test_m8_coordination_cards.py` 的失败、角色链接用例；`m8-coordination-chat.spec.ts` | CI 通过。失败丢弃本轮部分方案，按角色给「我提的需求」或「收到的需求」链接；网页 spec 从失败提示进入无需模型的方案页，再手工确认答复。忙碌提示不误报成手工降级 |
| 5. M7 集比较纯 Agent 与 Agent + 校验器，记录约束、经理修改和调用成本 | #338、#339；[test_coordination_eval.py](../backend/tests/eval/coordination/test_coordination_eval.py)、[test_nere750_coordination_failures.py](../backend/tests/eval/test_nere750_coordination_failures.py)；[评测报告](m8-coordination-eval.md) | 评测入口与合成分类 CI 通过；原真实模型两种模式各 34 案、共 68 条，无 deferred。**业务指标未达到 M7 基线**，详见下文。#339 的 6 个脚本样本不是新的真实模型全量基线 |

第 5 项补充：[F2 修复后重跑](m8-coordination-eval.md#f2-修复后重跑) 追加 NERE-754 的双模式真实模型结果及新旧成绩对比，原成绩和结论保留。

## 四入口隐私与会话保护

| 入口 | 测试位置与断言 | 结果 |
| --- | --- | --- |
| 网页 / REST | `test_m8_plan_view.py::test_case_names_and_assignments_respect_scope`、`test_m8_plan_revise.py::test_revise_requires_same_case_access`；`m8-plan-page.spec.ts` 检查 A 方无入口、无 B 方姓名；`m8-coordination-chat.spec.ts` 检查 A 方无人员方案卡 | #332、#333、#336 CI 通过；本轮未本地复跑 |
| 网页／飞书助手 | `test_m8_coordination_entries.py::test_bot_privacy_and_proposal_card` 和 `test_complete_bot_loop` 均按 `channel=web/feishu` 参数化；检查 A 方无个人字段、越权拒绝、确认前无业务写入；`test_m8_coordination_cards.py::test_feishu_renders_card_fields_and_plan_link` 检查文字与链接渲染 | #334、#336 CI 通过；使用假模型／渲染测试，没有连接真实飞书服务 |
| MCP | `test_m8_coordination_entries.py::test_mcp_scope_and_disabled` 检查本人令牌、其他企业、员工、范围外身份、A 方模拟／比较拒绝及关闭模块后工具消失 | #334 CI 通过；没有放开 MCP 的承诺写工具权限 |
| 新协调工具服务 | [test_m8_coordination_tools.py](../backend/tests/api/routes/test_m8_coordination_tools.py) 的 `test_role_scope_and_readonly`、`test_a_side_hides_historical_reply_prose`；检查调用人身份、部门级裁剪、历史自由文本隐私及业务行不变 | #334 CI 通过；不是授予 Agent 一个跨部门特权身份 |

### NERE-746 已覆盖与 NERE-752 未通过项

PR #337 的 `test_m8_session_scope.py` 覆盖网页／飞书的经理角色撤销、经理管辖部门变更、模块关闭、管理员权限撤销、旧敏感会话、权限不缩小、旧确认卡复核及取消流回滚。历史助手回复隐藏为「你的权限已变化，这条之前的回复已隐藏」，用户消息保留，续聊从空 Pi 状态开始。上述范围内的 CI 通过。

**未通过：[NERE-752](mention://issue/01a1e9e3-84a5-7edd-8382-f12e51bd740d)。** 员工调出经理管辖范围或离职，但经理角色、管辖部门集合及模块开关不变时，旧历史和 Pi 状态仍可能保留该员工信息。`test_m8_session_scope.py` 原有 `move` 是变更经理 RoleGrant，不是员工调出，不能冒充对此缺口的覆盖。

Reviewer 在基线 `351d82c1` 用源码函数的内存复现观察到：当前 `can_view_employee=False`，但历史保留个人字段且 Pi 状态仍被复用。该证据使用合成对象和存储替身，没有运行完整 API／数据库回归；修复、真实持久化回归与合并后复核由 NERE-752 补齐。截至本记录基线，该修复尚未合并及复核，因此不能写成会话隐私全部通过，也不能把文档交付视为 M8 已完成。

## NERE-726、E35(2)(3) 与阶段修复

| 设计输入 | PR 与测试位置 | 结果 |
| --- | --- | --- |
| NERE-726：英文代码、UUID 不可读 | #331、#332；`test_m8_plan_view.py::test_option_labels_cover_all_values`、`test_case_names_and_assignments_respect_scope` 与 `m8-plan-page.spec.ts` | CI 通过。中文类型／结论复用服务端标签，姓名按当前读取范围现查，不写入快照；审批人只列角色。指南按钮名对照 S2、S3、S5 spec |
| E35(2)：离职 409 固定错误码 | #330；[test_m8_departed_code.py](../backend/tests/api/routes/test_m8_departed_code.py)、[m72-departed-assignment.spec.ts](../frontend/tests/m72-departed-assignment.spec.ts) | CI 通过。`detail.code=employee_departed`，提示仍为「该员工已不在职，请刷新后重新选择」并刷新候选；无关「去确认团队负荷」链接不出现；无权人员仍 404 |
| E35(3)：随机密码不误判为英文文案 | #330；[admin-change-set.spec.ts](../frontend/tests/admin-change-set.spec.ts) 的 `reset password requires two clicks` 用例 | 网页 CI 通过。仅把本次随机密码列入该次中文文案检查允许词，密码两次确认及其余断言保留；本任务没有本地执行十轮复跑，不以 CI 一轮替代十轮记录 |
| NERE-745：降低投入与分阶段日期 | #335；`test_m8_s1_review_fixes.py` | CI 通过。自定／修改方案降低投入需需求方同意；同人分阶段改期合并，非等投入阶段拒绝改期，不重复计算人员 |

## 真实模型业务结论与失败证据边界

原真实模型报告基线为 `b3de3602c8b167efb9bf3b69f093934b04199b92`，模型 `chatgpt/gpt-6.1-sol`、high，运行于 2026-11-17。它早于 #339；本记录保留其结果，不声称修复后的真实模型已经达标。

| 指标 | 纯 Agent | Agent + 校验器 |
| --- | --- | --- |
| 已评估 | 34/34 | 34/34 |
| 违反约束 | 28/34（82.4%） | 34/34（100%） |
| 解析失败／模型失败 | 26／2 | 34／34 |
| 经理修改 | 下界 ≥1.25；6 案零次、2 案 5+、26 案不可计算 | 34 案不可计算 |
| 新人方案案例数（M7 要求至少 2） | 1，未达到 | 0，未达到 |
| 模型调用均值 | 3.03，仅 32/34 条计数已知 | 不可取得，0/34 条计数已知 |
| 工具调用／耗时均值 | 2.91 次／33.30 秒 | 0.94 次／11.88 秒 |

两种模式的「结论不更差」和新人数量要求均未满足。`passed` 只表示案例运行完成，解析失败也计为违反约束。失败记录的模型计数未知，不是零调用；工具计数只覆盖已观察到的回调，不能据此宣称失败模式更便宜。Pi 不返回 token，本次不计算货币成本。

#339 已证实修复前模型可见的协调参数说明缺少六个子操作的字段与 required 信息。合成验证对 `direct-01`、`newcomer-yes-01`、`delay-01` 两模式各跑一个脚本样本（共 6 项），并区分参数校验、权限拒绝、服务故障注入、供应商事件、协议解析和最终输出解析。它验证契约和错误路径，不代表完整真实模型新基线。

原始 34 次校验器模式失败的实际参数、异常及触发点，以及纯 Agent 26 次解析失败的逐项原因仍未知。`failed:model_unavailable` 可能由参数、权限或服务异常转成，不能直接归因为供应商故障；可控复现不能补写历史根因。详见[失败分类及已知／未知](m8-coordination-eval.md#最小复现与错误分类)。

## 迁移、权限与本轮本地验证

M8 没有增加 Alembic 迁移、依赖或改变权限模型；沿用 [ADR-0046](adr/0046-agent-planning-capacity.md) 和 [ADR-0047](adr/0047-coordination-agent.md)，模拟不写 Capacity 承诺。对规划起点 `e40c624a` 至本记录基线的迁移目录、`authz/`、`review/routing.py` 差异核对为空；NERE-746 修复缓存按现有权限失效，不是新增授权。证据只追加规则未变。NERE-752 同样要求沿用当前权限函数修缓存，不扩大读取权。

本轮在 NERE-742 工作树已运行：

- `uv sync --all-packages --frozen`、`bun ci`；锁文件未改。
- 后端 `uv run bash scripts/lint.sh`：mypy、ty、ruff 检查与格式均通过。
- 前端 `bunx biome check --no-errors-on-unmatched --files-ignore-unknown=true ./`、`bunx tsc --noEmit`、`bun run build`：通过。
- `uv run prek run --files docs/m8-acceptance.md docs/manager-guide.md docs/employee-guide.md docs/mcp-clients.md docs/README.md`：通过，含 Markdown 的 ruff format、typos、mypy、ty。
- `bash scripts/preflight.sh`：通过，唯一 Alembic head 为 `c696001bc001`；同步、pre-commit、数据库环境检查均通过，pytest 因无受影响目标跳过。未运行数据库 upgrade／downgrade，本 PR 无迁移变更。
- 五份文档的相对文件链接逐一检查存在，`git diff --check` 通过。

PR CI 及合并信息在 NERE-742 结果评论记录；preflight 显式使用本任务端口 `POSTGRES_PORT=15742` 与对应 `DATABASE_URL`，仅文档变更无受影响 pytest 目标，不连接数据库。未启动 Docker、读取本机模型授权、升级长期环境或新增测试；没有删除、跳过、放宽既有测试与 CI。模块开关及授权方式没有变化，管理员指南沿用原说明。

后续 M8.1 必须保留本记录的真实模型未达标与 NERE-752 未通过项，并以修复 PR、当前基线测试和 Reviewer 复核分别更新结论。使用步骤见[经理指南](manager-guide.md#b-方生成读懂和修改协调方案)、[员工指南](employee-guide.md#企业允许时问其他部门并提需求)、[MCP 协调工具](mcp-clients.md#协调工具方案不是承诺)。
