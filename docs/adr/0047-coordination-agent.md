# ADR-0047：协调 Agent 的工具与权限边界

状态：采用（NERE-739，2026-11-16）。依据：E27、E30、E31、E32、ADR-0046。

## 背景

网页与飞书共用 Pi 助手；MCP 使用相同的调用人身份。协调助手需要提出候选方案，但真实答复和资源安排仍须人工确认或走原有服务。

## 决策

`planning/tools.py` 登记六个 `EmployeeTool`。`read_coordination_context` 读取需求、时间线和 B 方可见的最新方案摘要；`inspect_capacity` 读取需求窗口的容量；`search_qualified_candidates` 读取符合要求的依据人员、本部门其他员工及新人；`simulate_plan` 创建方案快照或自定方案；`compare_plan_options` 比较同一最新快照的方案；`get_approval_status` 查询需求答复进度。模拟只写 Planning 对象，不修改 Capacity、审计或通知。

| 调用人 | 返回边界 |
| --- | --- |
| B 方代表（`represents_department()`） | 可看候选人姓名、周容量和方案；仍由 `Principal`、`employee_scope_clause()` 与 Planning 服务校验。 |
| A 方和其他有权看该需求的人 | 只看需求状态、结构化时间线、`capacity_reference` 同级的部门汇总；匹配少于 3 人隐藏人数，3 人及以上只给人数，不给个人或负荷分档。方案操作拒绝。 |
| 外企业、无权看需求或模块关闭 | 不返回需求或方案数据。 |

所谓「Agent 用系统身份跨部门检索」由现有 Capacity 服务内部汇总实现，尤其是 `department_reference` 和借调模拟；工具自身只用调用人的 `Principal`，不新增系统权限。A 方时间线以 `get_demand` 的部门级裁剪结果为基础，进一步隐藏 B 方答复与承诺日期变更的自由文本；结构化结论、日期和部门级依据仍可见，以防历史文本出现人名。

网页和飞书用登记表增加 `coordination(function, arguments)` 与 `prepare_plan_proposal(case_id, option_id, text)` 两个 Pi 定义。协调启用时，`get_demand`、`get_demand_basis` 的模型定义由 `coordination` 承接，旧调用路径仍能处理旧会话；最多 29 个定义。MCP 直接登记六个协调工具，不提供提案卡。MCP 原有的 `reply_demand` 直接提交契约不变。

NERE-895 将项目查询登记到同一个 `coordination` 入口。网页和飞书通过 `function` 选择 `list_projects` 或 `get_project`，MCP 分别暴露两个独立只读工具。两种入口都使用当前调用者的 `Principal` 和原有的项目可见范围。

`prepare_plan_proposal` 只接受 B 方可见的最新方案。方案结论映射到答复结论，起止日期从方案取；正文先拒绝快照候选人姓名，再复用 `capacity/assistant.py` 的 E32 校验和 `demand_reply` 确认卡。确认动作仍由 Capacity 服务执行。助手和 MCP 均没有创建分配、修改完成或开始日期、批准延期或负荷调整的工具。

`get_approval_status` 只返回需求状态和需求事件。负荷调整不属于某条需求；待审延期由经理在「需求与容量 → 待我审批」处理，批准且改变承诺日期的延期会出现在 `commitment_changed` 事件中。Planning 不导入 `assignment`、`workload` 或 `extension`。

## 代价与风险

工具输出只负责候选方案，不证明真实资源已经预留；过期快照须重新模拟。模型失败时手工需求与方案页面仍可用。
