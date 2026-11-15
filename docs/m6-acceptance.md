# M6 验收记录

核对基线：`master 22bf64b64b867079c62f3c816c0c6138133a10c4`，2026-11-15。
对应父 issue [NERE-635](mention://issue/01a1de41-cbbf-7e4f-bbd2-863f2e61f30a)，记录任务 NERE-651（S13）。
M6 交付「跨部门需求与容量承诺」；原个人借调方案已作废，借调归 M9。
S1～S12 及拆分任务、S14/S15 修复均已交付，指南由 S11 更新。

本记录核对合并代码、自动测试和结果评论，不重复运行全部业务测试，也不升级 nere497。
功能和权限自动测试已有通过记录；nere497 冒烟仍有隐私不通过、开关部分不通过的原始结论。
S15 已修复姓名泄露，但长期环境复测留在 M6.1；不能把修复合并当作冒烟复测通过。
测试链接均从本文相对定位到仓库文件；评论时间统一用 UTC（北京时间为 UTC+8）。

## 依据与时间口径

| 出处 | 采用内容 |
| --- | --- |
| [NERE-635「M6 规划（改写版）」](mention://issue/01a1de41-cbbf-7e4f-bbd2-863f2e61f30a)，11-15 08:42 | 参考实现对照表、模块边界、负荷口径、任务分工与不做事项；评论 ID `01a1de5f-7609-7389-a67d-83323780c8db` |
| NERE-635，08:49 第 1 阶段核对 | S1/S2 的 PR、14 项检查通过及范围不变 |
| NERE-635，13:00 第 2 阶段核对、13:45 收尾 | #283～#294、#296、#297 的交付与审查；答复人姓名的旧默认决定作废 |
| [NERE-650](mention://issue/01a1de5d-eeff-74e1-bef1-75c9348cf36a)，测试工程师 13:06、13:12 | S12 冒烟逐项结果、截图、隐私不通过及 NERE-657；以下 S12 节按这两条汇总 |
| NERE-650，技术规划 13:06 确认、14:16 核对及 14:17 更正 | 从严采用「A 方不能看到 B 部门任何人的姓名」；S12 done 表示结果齐备，不表示各项均通过；#298 合并后的 master CI 当时尚未全部完成 |
| [NERE-658](mention://issue/01a1df4d-c5bd-7a03-be78-321125c1190c)，13:30、13:38 结果 | S15 服务层统一隐名、自动测试、PR #297 的 14/14 检查通过并合并；nere497 未复测 |
| [NERE-649](mention://issue/01a1de5d-e1d6-7f3f-a6ac-9159a196c425)，[#295](https://github.com/ft1148137/employee-profile-platform/pull/295)、[#298](https://github.com/ft1148137/employee-profile-platform/pull/298) | 管理员、员工、经理及 MCP 指南；后续更正答复人姓名口径 |

S12 的两条测试评论 ID 分别为 `01a1df50-b490-7985-b986-ebad1465f6e8`、
`01a1df56-8708-70a0-b6fe-ca4c4840fdc7`，截图压缩包 `evidence-short.zip` 附在前者。
这些截图属于合成企业测试记录，保留在 issue 附件中，本仓库不复制聊天或业务截图。

## 第 1 阶段：M5 遗留

| 条目 | 结论与 PR | 自动测试 |
| --- | --- | --- |
| S1 NERE-632：CSV 中文表头和状态 | 已交付，[#281](https://github.com/ft1148137/employee-profile-platform/pull/281)。八列依次为工号、能力、等级、判断状态、证据数、最近证据时间、批准人、批准时间；状态为已确认、暂定、存在争议，空等级为未定级；时间按企业时区，JSON 保留英文字段与状态 | [test_m6_profile_export_csv.py](../backend/tests/api/routes/test_m6_profile_export_csv.py)：三种状态、BOM、中文、引号、公式注入防护、时区及 JSON 不变；[m5-profile-export.spec.ts](../frontend/tests/m5-profile-export.spec.ts)：网页导出 |
| S2 NERE-633：覆盖率含下级 | 已交付，[#282](https://github.com/ft1148137/employee-profile-platform/pull/282)。部门行增加「含下级部门」合计；原本部门数和上方全范围合计不变；只统计当前 Principal 范围内在职员工，没有下级时不显示合计 | [test_m6_coverage_descendants.py](../backend/tests/api/routes/test_m6_coverage_descendants.py)：区域、子部门、空部门和店长隔离；[m6-coverage-descendants.spec.ts](../frontend/tests/m6-coverage-descendants.spec.ts)：合计展示和部门过滤 |

## 父 issue 的五条验收标准

| 标准 | 结论与实现 PR | 自动测试文件与断言 |
| --- | --- | --- |
| 1. A 方问参考、提需求，B 方 AI 起草并修改确认，A 方收到答复和依据；全程不泄露个人信息 | 功能已交付：S5a/S5b [#285](https://github.com/ft1148137/employee-profile-platform/pull/285)、[#286](https://github.com/ft1148137/employee-profile-platform/pull/286)，S7 [#288](https://github.com/ft1148137/employee-profile-platform/pull/288)，S9a/S9b [#292](https://github.com/ft1148137/employee-profile-platform/pull/292)、[#294](https://github.com/ft1148137/employee-profile-platform/pull/294)。隐私冒烟曾失败，S15 [#297](https://github.com/ft1148137/employee-profile-platform/pull/297) 已修，M6.1 待复测 | [test_m6_capacity_demand.py](../backend/tests/api/routes/test_m6_capacity_demand.py)、[test_m6_capacity_reply.py](../backend/tests/api/routes/test_m6_capacity_reply.py)：汇总、提交、依据快照、答复/接受/追问和通知；[test_m6_capacity_assistant.py](../backend/tests/api/routes/test_m6_capacity_assistant.py)、[test_m6_capacity_assistant_reply.py](../backend/tests/api/routes/test_m6_capacity_assistant_reply.py)：起草不落业务数据、修改后旧卡失效、本人确认才发送；[test_m6_capacity_responder_privacy.py](../backend/tests/api/routes/test_m6_capacity_responder_privacy.py)：A 方各读取入口及接受返回值均不含答复人姓名 |
| 2. Jira 参考值、经理直接调整、员工申请同意或退回 | 已交付：S3 [#283](https://github.com/ft1148137/employee-profile-platform/pull/283)、S4 [#284](https://github.com/ft1148137/employee-profile-platform/pull/284)、S9b #294。经理不能直接改自己，企业管理员身份本身不授予调整权限 | [test_m6_capacity_workload.py](../backend/tests/api/routes/test_m6_capacity_workload.py)：参考件数、空值、分配比例、审批路由/代理、重复决定、通知及画像不变；[m6-capacity-workload.spec.ts](../frontend/tests/m6-capacity-workload.spec.ts)：申请、退回、同意、直接调整及范围 |
| 3. 员工延期同意或退回、经理改期；推迟承诺须确认并通知 A 方 | 已交付：S6a/S6b [#287](https://github.com/ft1148137/employee-profile-platform/pull/287)、[#289](https://github.com/ft1148137/employee-profile-platform/pull/289)，S8a/S8 [#291](https://github.com/ft1148137/employee-profile-platform/pull/291)、[#293](https://github.com/ft1148137/employee-profile-platform/pull/293)。承诺更新、事件和通知在同一事务完成 | [test_m6_capacity_assignment.py](../backend/tests/api/routes/test_m6_capacity_assignment.py)、[test_m6_capacity_extension.py](../backend/tests/api/routes/test_m6_capacity_extension.py)：权限、审批/退回、并发及事务回滚；[test_m6_capacity_assignment_web.py](../backend/tests/api/routes/test_m6_capacity_assignment_web.py)：新日期和对外说明必填、说明禁带个人信息、A 方通知；[m6-capacity-assignment.spec.ts](../frontend/tests/m6-capacity-assignment.spec.ts)：分配、延期、经理改期与 A 方新日期 |
| 4. 关闭模块不影响画像；关闭模型可用表单走通 | 后端隔离与表单已交付：#283、#284、#288、#293。关闭模块的菜单刷新和助手引导仍部分不通过（NERE-657）；不能记成全部通过 | [test_m6_capacity_architecture.py](../backend/tests/test_m6_capacity_architecture.py)：依赖隔离；[test_m6_capacity_workload.py](../backend/tests/api/routes/test_m6_capacity_workload.py)：默认关闭、关闭后拒绝业务调用；上述三个网页 spec 在新企业未配置模型时分别走通负荷、需求、分配与延期；全量 CI 依据见下一节 |
| 5. 网页、网页助手、飞书、MCP 四入口越权测试通过 | 已有自动测试通过记录：#283～#294、#297；S10 MCP 为 [#290](https://github.com/ft1148137/employee-profile-platform/pull/290)。实际冒烟只抽查部分身份和入口，不等同完整人员回归 | 下表列明每入口覆盖及个人信息断言；MCP 为 [test_m6_capacity_mcp.py](../backend/tests/api/routes/test_m6_capacity_mcp.py)，网页助手/飞书为上述两个 assistant 测试文件，答复人隐名另由 S15 回归覆盖 |

## 四入口权限与 A 方隐私断言

| 入口 | 越权测试覆盖 | 「A 方拿不到 B 部门个人信息」如何断言 |
| --- | --- | --- |
| 网页 / REST | `test_m6_capacity_workload.py`、`test_m6_capacity_demand.py`、`test_m6_capacity_reply.py`：范围外/跨企业 404，非经理调整及改自己被拒，不能冒充 B 方答复或 A 方回应；`test_m6_capacity_assignment_web.py`：A 方不能读分配名单，范围外延期代理不能读取或决定 | demand 测试把 B 方姓名、工号、UUID、`PRIVATE_STATEMENT` 与序列化响应逐个比对，要求不存在；reply 测试排除 `workload`、`confirmed_percent`、`assignments` 等个人字段；S15 privacy 测试要求 A 方 `responder_name is None` 且姓名不在响应全文。网页 `m6-capacity-demand.spec.ts` 断言不显示 B 方姓名和「本部门依据」，时间线只显示「答复人：对方部门经理」；assignment spec 排除分配人名及内部延期原因 |
| 网页助手 | `test_m6_capacity_assistant.py`：伪造企业/员工/来源/部门不出卡，跨用户/跨企业点击 404，伪造字段 422，旧卡或重复确认 409；关闭或权限变化后重查。`test_m6_capacity_assistant_reply.py`：非 B 方、无范围、跨租户、自答、姓名/工号、旧版本均拒绝；负荷和延期只能为本人申请 | `assert_private_absent()` 对读工具结果、确认卡、网页回包以及发给 Pi 的请求排除 B 方 UUID、姓名、工号和 `PRIVATE_PROFILE`；A 方读依据返回错误。S15 对助手 `get_demand` / `list_demands` 同样断言姓名为空且全文不含姓名；reply 测试核对 A 方只收到修改后的部门答复 |
| 飞书助手 | `test_feishu_bound_buttons_and_audit`、`test_feishu_extension_bound_confirmation`（上述两个 assistant 文件）：通过 Feishu 适配器和 Gateway，别人点击按钮、伪造消息 ID 均不写数据，绑定本人确认才提交；审计来源为飞书。工具权限和确认前重查复用网页助手路径 | `test_feishu_bound_buttons_and_audit` 对发出的飞书卡片调用 `assert_private_absent(outgoing, demo)`；S12 实际飞书追问「谁有空、姓名和个人负荷」只返回汇总。飞书没有单独的 S15 姓名读取端到端回归，依靠共用需求投影及助手回归，长期环境修复复测留在 M6.1 |
| MCP | `test_m6_capacity_mcp.py`：PAT 当前身份、跨企业/范围外需求 404 或空列表，A 方不能答复或代他人申请；伪造 org/employee/user/Principal/来源字段被拒；关闭后工具不列出且调用返回与未知工具相同的错误；不暴露分配和审批写工具 | 对 A 方工具结果逐个排除 B 方 UUID、工号、员工姓名；A 方读依据/替 B 方申请延期被拒，个人分配列表为空。旧 MCP 测试曾排除答复经理姓名这一项，不能单靠它证明 S15；`test_m6_capacity_responder_privacy.py` 补测实际 MCP 列表/详情响应，A 方员工和经理均 `responder_name is None`，姓名不在结构化结果及内容中 |

四入口共用服务端 Principal 和容量服务，不因助手或 MCP 获得额外权限。
助手写操作必须由本人点击确认卡；MCP 直接调用写工具，工具说明要求用户明确同意，并记录客户端来源，不能据此声称平台验证了外部客户端的确认 UI。
负荷/延期审批、分配及直接改日期只在网页开放，不在助手和 MCP 增设入口。
负荷调整代理沿用既有审批路由；延期代理还须在 B 部门范围内或可代表 B 方。
#291 修复了范围外代理可读 B 方延期上下文的问题，S14 [#296](https://github.com/ft1148137/employee-profile-platform/pull/296) 保留该限制并补测。

## 模块边界、关闭与无模型路径

[ADR-0045](adr/0045-capacity-module.md) 记录独立目录、七张租户表、默认关闭及只读画像边界。
[test_m6_capacity_architecture.py](../backend/tests/test_m6_capacity_architecture.py) 拦住两种依赖：

1. 画像等允许接入点以外的代码 import `domains.capacity`；非容量表的外键指向容量表也会被拦。
2. 容量模块 import 画像、证据、起草、同步、审批发布等写入路径；保留的例外仅为 `review.routing`、`change_request.schemas`、`source.models`，后者只判断 Jira 连接是否存在。

测试既检查真实目录，也用违规 import 和外键反例证明守卫有效。
模块只通过 M5 `capability.search.find_people` 取能力数据；worker 身份仅用于服务端汇总，用户身份找人范围不变。
模块不写画像、证据或画像请求，也不回写 Jira；Evidence 仍只追加。
demand/reply/workload/MCP 测试比较操作前后的画像或证据，证明本模块操作没有改写它们。
需求时间线 `capacity_demand_event` 只追加，每类业务操作另留审计，正文和凭据不进审计详情。

迁移只有 S3 的 `c641001bc001`，前驱 `b622001dc001`。
[test_m6_capacity_migration.py](../backend/tests/test_m6_capacity_migration.py) 做 downgrade → upgrade → downgrade → upgrade，
检查七张表、含 `org_id` 的外键、部分唯一索引和收件箱约束的恢复；S15 没有新增迁移。

| 验证 | 通过依据与边界 |
| --- | --- |
| 默认关闭下既有画像全量回归 | master `8b20522`（#297 合并提交）的 [Test Backend run 43720000658](https://github.com/ft1148137/employee-profile-platform/actions/runs/43720000658) 已 success：三个分片和 `test-backend` 汇总均通过，合并 coverage 仍要求 90%。工作流跑完整 `backend/tests`，企业默认无设置行即关闭；容量专项测试按需开启，不是强制全部测试始终关闭模块 |
| 关闭后入口及工具不可用 | workload 测试验证无设置行默认关闭、关闭后业务接口 404；assistant/MCP 测试验证工具隐藏及旧调用拒绝；`m6-capacity-workload.spec.ts` 重新打开页面后菜单消失、找人和员工详情仍可打开。S12 另外实测画像、找人、覆盖率和 CSV，已有页面实时更新仍见 NERE-657 |
| 无模型的表单流程 | [m6-capacity-demand.spec.ts](../frontend/tests/m6-capacity-demand.spec.ts) 在新企业未配置模型时问参考、提交、经理答复、追问、接受和撤回；[m6-capacity-assignment.spec.ts](../frontend/tests/m6-capacity-assignment.spec.ts) 覆盖分配、延期同意/退回、推迟承诺通知、经理改期和关闭分配；[m6-capacity-workload.spec.ts](../frontend/tests/m6-capacity-workload.spec.ts) 覆盖负荷申请、审批及直接调整。三者合起来覆盖无模型全流程，不是一条测试包办全部步骤 |
| 网页自动测试 CI | 同一 master `8b20522` 的 [Playwright run 43720000624](https://github.com/ft1148137/employee-profile-platform/actions/runs/43720000624) 已 success；用于核对包括 S15 隐名断言的网页流程 |

核对基线 `22bf64b` 是指南更正 #298 的合并提交。
技术规划 14:17 更正评论记录其 Test Backend、Test Docker Compose 当时仍在运行；
本次编写时核实 [Compose run 43723256461](https://github.com/ft1148137/employee-profile-platform/actions/runs/43723256461) 已 success，
[Backend run 43723256399](https://github.com/ft1148137/employee-profile-platform/actions/runs/43723256399) 随后也已 completed / success。
上表保留包含 S15 修复的 `8b20522` 全量通过记录，基线 `22bf64b` 的这两项 master 检查也已通过；不将 PR 检查冒充 master 检查。

## S12：nere497 冒烟

以下以测试工程师 13:06 原结果和 13:12 补充为准，截图号对应 `evidence-short.zip`。
环境为合成企业禾邻超市，升级至 `e7b6163`，尚未包含 #297/#298。
环境没有大客户部，改用企划与会员部向滨江店提团购备货需求。
这轮只做冒烟；完整人员回归、修复复测在 [M6.1 NERE-636](mention://issue/01a1de41-d19f-7ee1-ac4d-a47c8a2be897)。

| 检查项 | 结论 | 结果与出处 |
| --- | --- | --- |
| 保留数据升级 | 通过 | `upgrade.sh origin/master` 成功；升级前剩余 33 GiB，未重建数据库或企业；数据库、后端、worker、Pi healthy，IM/备份运行中。13:06 结果记录 |
| 管理员开启 | 通过 | 贺长青开启模块，谁可提需求保持「全体员工」（01） |
| 飞书提需求、网页助手答复、A 方接受 | 通过 | 吕可欣拿参考并确认提交；鲁思勉起草、修改说明、确认发送；A 方在详情及飞书看答复和部门依据后接受（04–10、25）。收件箱是通用提醒，不含答复正文 |
| 分配和跨承诺日期延期 | 通过 | 柴宇获分配，申请 11-18 延至 11-20；经理同意并填新承诺日期，A 方详情与飞书显示新日期（11–14、25） |
| 员工负荷申请 | 通过 | 网页助手申请平台外工作 30%，经理网页同意；团队负荷为 130%（含本轮 100% 分配）（15–18） |
| A 方隐私 | 不通过 | A 方时间线显示「答复人：鲁思勉」（09、14）。13:12 依技术规划确认从严记不通过；S15 NERE-658 已修，见下节，M6.1 待复测 |
| 其余隐私抽查 | 通过 | 飞书问滨江店谁有空、姓名和个人负荷只得汇总（19）；A 方需求详情未见被分配员工姓名、工号、个人负荷。这不代表已完成所有身份的抽查 |
| 关闭、画像不受影响、重新开启 | 部分不通过 | 管理员菜单立即消失；员工旧页面要刷新，助手建议进入已关闭页面（20、24、26）。关闭期间画像可开、找人有结果、覆盖率可见，CSV 下载 57 条；重开后需求和延期保留（21–23、27–28）。新 UX issue [NERE-657](mention://issue/01a1df4d-8a91-7968-af9a-3e6a7ef74a8c)，backlog、ux 标签、无指派、无父 issue，尚未修复 |
| S1 CSV | 通过（冒烟） | 中文表头、当前数据的「已确认」、Quick Look 中文正常（30）；数据没有另两种状态，未实测，确定性覆盖见 S1 自动测试 |
| S2 覆盖率 | 通过（冒烟） | 东城区域运营部、南湖店显示含下级合计（02） |
| 收尾 | 已完成 | 模块保持开启，飞书测试绑定已解绑（29）；服务状态同升级后，剩余 28 GiB；没有改产品代码、删长期环境数据或重新部署 |

S12 没有 PR。技术规划在 14:16 核对后将其标为 done，理由是结果与截图齐备且未改代码，
不是撤销隐私或开关的失败结论；14:17 随即更正了 master CI 尚未完成的说法。

## S15：A 方不再显示 B 方答复人姓名

发现经过：S12 在 12:58 向父 issue 报告，A 方时间线出现 B 方答复经理姓名。
旧规划待决策第 3 条允许这一项，与父 issue 和 S12 的隐私验收冲突；
技术规划在 13:03～13:06 明确从严验收，作废旧默认，建 S15 NERE-658。

修复为 [#297](https://github.com/ft1148137/employee-profile-platform/pull/297)，
13:37 合并到 master `8b20522204497d6d423242c8e210d1f8f9bdc128`。
需求服务 `demands_public()` 依据当前 Principal 判断：仅代表目标部门的查看者可得 `responder_name`，
A 方员工和经理均为空。网页、助手、MCP 共用该投影，去掉助手的重复过滤；
网页改为「答复人：对方部门经理」，B 方仍能看到答复人姓名。

S15 结果记录先复现失败再修复，容量模块 101 项、本地相关 Playwright 2 项通过，PR CI 14/14 success。
专用 [test_m6_capacity_responder_privacy.py](../backend/tests/api/routes/test_m6_capacity_responder_privacy.py)
覆盖网页/MCP/助手的列表和详情、A 方接受后的响应、B 方仍可见及跨企业拒绝。
[test_m6_capacity_list_fixes.py](../backend/tests/api/routes/test_m6_capacity_list_fixes.py)
将 A 方姓名断言按新标准改为 null，同时保留分页、顺序、查询次数及隔离断言；
`m6-capacity-demand.spec.ts` 新增替代文案和全页不含答复经理姓名的断言。

经理指南在 #297 同步，S11 后续 [#298](https://github.com/ft1148137/employee-profile-platform/pull/298)
更正员工/经理指南中遗留的「对方可见答复人姓名」表述。
S15 没有升级或操作 nere497，因此 S12 的姓名不通过仍按原始结果保留；复测安排在 M6.1。

## 参考实现与许可证

对照表见上方链接的 NERE-635「M6 规划（改写版）」08:42 评论，
设计约束落在 [ADR-0045](adr/0045-capacity-module.md)。
ERPNext / Frappe HRMS 为 GPL-3.0，Odoo 社区版为 LGPL-3.0；本项目为 Apache-2.0。
M6 只参考设计，没有复制代码、doctype 定义、模板或文案，没有引入这些项目的依赖。

借鉴需求登记的单据和状态、部门与期间的汇总、负责人和截止日期、未关闭指派件数；
不借鉴招聘、编制成本、工时计费、甘特排期、自动派活或技能评分作为事实。
「部门汇总参考 + 经理确认承诺 + 承诺变化通知 A 方」由本项目自行设计。

## 已知限制与后续范围

| 项目 | 当前口径与限制 |
| --- | --- |
| Jira 参考值 | 只统计已同步、未解决、当前指派给关联账号的任务件数；无 Jira 连接或无关联账号为 null，有连接/关联但无未完成任务为 0。件数不是工时，也不换算百分比；同步范围外或长期未更新的任务可能仍计入，见 `capacity/workload.py` 的 shortcut 和 ADR-0045 |
| 经理确认值 | 平台外工作占比加当前平台分配占比；没有确认时保留「未确认」，不当 0。低于 80% 有余量，80%～100% 基本满，高于 100% 超负荷；不是绩效，不做按负荷排名 |
| 小部门推断 | 只有 1～2 人符合能力时，汇总计数仍可能让 A 方推断个人；M6 不做小样本隐藏。M6.1 重点回归，是否隐藏小计数留后续决定，不能称为匿名化保证 |
| 姓名拦截 | 对外答复和承诺说明由服务端拦截 B 部门姓名、工号、UUID；同名或常见词可能误拦，需经理改写；不据此声称能识别所有自由文本个人信息 |
| 开关体验 | NERE-657 尚在 backlog；关闭后的服务端拒绝已测试，旧页面菜单刷新和模型引导体验仍待改 |
| 验证范围 | S12 为合成身份冒烟，未完成完整人员回归；S15 在 nere497 待复测。MCP 有确定性协议测试，本轮没有新增外部客户端或所有入口真实人员实测 |

不在 M6 范围：派活均衡推荐（M7 / NERE-637）、完成反馈回流画像（M8 / NERE-638）、
跨部门借调（M9 / NERE-639）、组织洞察（M10 / NERE-640）；
工时填报、人天排期、排班、成本、计费结算、回写 Jira。
不提前实现上述阶段；本次只记录验收依据，未扩展画像写入或可见范围。
