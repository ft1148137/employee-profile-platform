# M7 验收记录

核对基线：`master 1c5baaac`，2026-11-16。父 issue [NERE-637](mention://issue/01a1de41-d6a8-706b-a294-93a5d96a48e4)，记录任务 NERE-699。时间以本地执行日为准，周从企业时区的周一开始；旧值迁移按原记录 `updated_at` 所在 UTC 周。本记录基于合成数据，在独立 Compose 项目 `nere699`、PostgreSQL `localhost:15699` 实测；网页服务使用 `localhost:18699`。截图只附在 NERE-699 评论，不提交仓库。

## 依据与交付

| 子 issue | 交付与 PR |
| --- | --- |
| NERE-691 S1 | [#310](https://github.com/ft1148137/employee-profile-platform/pull/310)：[ADR-0046](adr/0046-agent-planning-capacity.md)，明确 Agent / Planning / Capacity 的职责、写入与隐私边界 |
| NERE-692 S2 | [#311](https://github.com/ft1148137/employee-profile-platform/pull/311)：E32 小部门答复正文负荷词校验 |
| NERE-693 S3a | [#312](https://github.com/ft1148137/employee-profile-platform/pull/312)：周容量、旧值迁移、按周查看及经理确认 |
| NERE-694 S3b | [#314](https://github.com/ft1148137/employee-profile-platform/pull/314)：按周申请修正、不可用日期 |
| NERE-695 S4 与 NERE-705 修复 | [#313](https://github.com/ft1148137/employee-profile-platform/pull/313)、[#315](https://github.com/ft1148137/employee-profile-platform/pull/315)：分配写入校验、并发、过去周处理 |
| NERE-701 S4b | [#319](https://github.com/ft1148137/employee-profile-platform/pull/319)：E29(4) 离职员工分配取消 |
| NERE-696 S5a、NERE-697 S5b | [#317](https://github.com/ft1148137/employee-profile-platform/pull/317)、[#318](https://github.com/ft1148137/employee-profile-platform/pull/318)：Planning 对象、快照、校验、方案模拟 |
| NERE-698 S6 | [#320](https://github.com/ft1148137/employee-profile-platform/pull/320)：评测集与[基线](m7-planning-baseline.md) |
| NERE-699 文档更正 | [#321](https://github.com/ft1148137/employee-profile-platform/pull/321)：验收命令和负荷措辞更正 |
| NERE-714 范围修复 | [#322](https://github.com/ft1148137/employee-profile-platform/pull/322)：Planning 历史快照按当前员工范围保护 |

上述 M7 交付已合并至核对基线；#321、#322 是后续合并的更正与范围修复。本任务只更新验收和使用文档，不修改代码或测试。以下命令在 `backend/` 执行，事先设置 `COMPOSE_PROJECT_NAME=nere699`、`POSTGRES_PORT=15699`、`API_PORT=18699`，且 `DATABASE_URL` 指向 `localhost:15699/app`。测试均使用 `--no-cov`，全量覆盖率交给 CI。

## 父 issue 三条验收

| 验收 | 本地命令与结果 | 自动断言 |
| --- | --- | --- |
| 1. 同人同周并发不超额 | `for run in 1 2 3; do uv run pytest tests/api/routes/test_m7_assignment_checks.py::test_concurrent_commitments_cannot_overbook -q --no-cov -n 2 --dist loadgroup; done`：三轮均通过，每轮 20 轮 × 两种 `extension_race` = 40 个参数用例 | [test_m7_assignment_checks.py](../backend/tests/api/routes/test_m7_assignment_checks.py) 以两个线程和独立会话竞争同一员工的容量，断言不会同时写成超额承诺；[test_m7_assignment_past_weeks.py](../backend/tests/api/routes/test_m7_assignment_past_weeks.py) 检查过去周跳过、未来周仍校验 |
| 2. 无数据返回「未确认」 | 接口：下述组合回归命令包含 `test_m7_weekly_capacity.py::test_missing_weeks_are_unconfirmed`，该节点通过。页面：`PLAYWRIGHT_BASE_URL=http://127.0.0.1:18699 bun x playwright test --workers=1 --reporter=line tests/m6-capacity-workload.spec.ts tests/m6-capacity-assignment.spec.ts`（在 `frontend/` 执行）：含登录准备共 `3 passed`；截图 `team.png`、`mine-mobile.png`、`assignment-mobile.png` 仅附在 NERE-699 结果评论。助手与 MCP 的临时实测命令与原测试边界见下文 | [test_m7_weekly_capacity.py](../backend/tests/api/routes/test_m7_weekly_capacity.py) 断言 8 周 `status=unconfirmed`、`band=unconfirmed`、`last_confirmed=null`，且不出现「有余量」或「有空」；页面断言 8 周显示「未确认」、无「有余量」，确认 4 周后余下 4 周仍未确认；未确认周创建分配返回引导确认的错误 |
| 3. 同需求可比较多方案，且不改真实分配 | 下述组合回归命令包含 Validator、Simulator、Readonly 和 34 个评测案例，相关节点全部通过；`uv run python scripts/eval_planning.py --report ../m7-699-baseline-check.md`：36 个测试通过（其中 34 个 YAML 案例）；生成报告为 34/34、均值 2.24、新人 2 案，和仓库基线同口径，临时报告已删除 | [test_m7_planning_simulator.py](../backend/tests/api/routes/test_m7_planning_simulator.py) 检查同一快照的直接、新人、延期、缩小范围、分阶段和借调方案；[test_m7_planning_validator.py](../backend/tests/api/routes/test_m7_planning_validator.py) 检查四种结论、过期和权限；[test_m7_planning_readonly.py](../backend/tests/api/routes/test_m7_planning_readonly.py) 在创建、读取和生成方案前后比较 Capacity 业务行及审计行，确认不写真实分配 |

### 后端组合回归的实测命令

下列命令在 `backend/` 执行，首轮 `141 passed, 2 errors`。两项错误均出在 Pi 本地依赖准备，见文末；其余节点通过。

```bash
uv run pytest \
  tests/api/routes/test_m7_weekly_capacity.py::test_missing_weeks_are_unconfirmed \
  tests/api/routes/test_m7_planning_validator.py \
  tests/api/routes/test_m7_planning_simulator.py \
  tests/api/routes/test_m7_planning_readonly.py \
  tests/eval/planning/test_planning_eval.py \
  tests/api/routes/test_m7_reply_load_words.py \
  tests/api/routes/test_m7_departed_cancel.py \
  tests/api/routes/test_m7_assignment_past_weeks.py \
  tests/api/routes/test_m7_capacity_corrections.py \
  tests/api/routes/test_m7_unavailability.py \
  tests/test_m7_weekly_capacity_migration.py \
  tests/test_m7_planning_migration.py \
  tests/api/routes/test_m6_capacity_assistant.py \
  tests/api/routes/test_m6_capacity_mcp.py \
  tests/api/routes/test_m6_capacity_responder_privacy.py \
  -q --no-cov -n 2 --dist loadgroup
```

### 助手与 MCP 的实际返回

已有 [test_m6_capacity_assistant.py::test_read_results_scope_and_definitions](../backend/tests/api/routes/test_m6_capacity_assistant.py) 和 [test_m6_capacity_mcp.py::test_flow_privacy_and_audit](../backend/tests/api/routes/test_m6_capacity_mcp.py) 都调用了无 `CapacityWeek` 记录用户的 `get_my_workload`，但只断言权限和隐私，**没有显式断言** `band` 或 `weeks[*].status`。本次用这两个文件已有的 `case`、`capacity` 合成 fixture，在未提交的临时探针中分别执行 `tools.call(db, a, store.create_session(db, a), [], "get_my_workload", {})` 和 `use(client, headers["a"], "get_my_workload", {})`；命令为 `uv run pytest tests/api/routes/test_m7_699_probe.py -q -s --no-cov -n 0 -o addopts='' --disable-warnings --tb=short`。结果 `2 passed`；两种入口均返回 `band='unconfirmed'`、8 个 `weeks[*].status='unconfirmed'`、`last_confirmed=None`。探针执行后已删除，不属于本 PR 的测试改动。另有 [test_m7_unavailability.py](../backend/tests/api/routes/test_m7_unavailability.py) 验证 A 方不能读不可用日期及相关 MCP 工具。

## 评测、E32 与 E29(4)

[评测集](../backend/tests/eval/planning/) 含 34 个 YAML 案例、16 类，每类至少 2 例；[test_planning_eval.py](../backend/tests/eval/planning/test_planning_eval.py) 检查类别覆盖、方案结论及 Capacity 表不变。[基线](m7-planning-baseline.md) 记录 34/34 通过、平均每案 2.24 个方案、新人方案出现于 2 案；结论分布为 `feasible=14`、`conditional=40`、`infeasible=18`、`insufficient_data=4`。这是规则引擎自测，不是 M8 Agent 的质量结论。

- **E32**：[test_m7_reply_load_words.py](../backend/tests/api/routes/test_m7_reply_load_words.py) 覆盖少于 3 人时网页/REST、网页与飞书共用助手出卡、MCP `reply_demand` 的个人负荷词拦截；共享 `demand.validate_reply_text()`。还检查 `bot/prompts.py` 和 `mcp/server.py` 的引导语。3 人及以上、无关的「未确认」等边界保留。
- **E29(4)**：[test_m7_departed_cancel.py::test_departed_assignment_read_and_cancel](../backend/tests/api/routes/test_m7_departed_cancel.py) 断言 B 方有权经理可读进行中的离职分配并仅可取消，返回 `DepartedDemandAssignment` 固定「已离职员工」，隐藏员工 ID、工号、画像；A 方、子部门经理仍为 404。[test_m6_capacity_assignment_web.py](../backend/tests/api/routes/test_m6_capacity_assignment_web.py) 与 [test_m7_assignment_checks.py](../backend/tests/api/routes/test_m7_assignment_checks.py) 保留范围和离职写入限制；`m6-capacity-assignment.spec.ts` 检查离职卡片仅有「取消分配」。

## 四入口隐私回归

| 入口 | M7 新增断言 | 保留的 M6/M6.2 断言 |
| --- | --- | --- |
| 网页 / REST | S2 少于 3 人时答复正文禁写个人负荷词；S3b A 方不可读 B 方不可用日期；S4b 离职分配固定隐名且 A 方 404；S5a Planning A 方 404 | 需求、答复、分配列表与对外时间线不含 B 方姓名、工号、画像和个人负荷；[test_m6_capacity_responder_privacy.py](../backend/tests/api/routes/test_m6_capacity_responder_privacy.py) 对 A 方答复人姓名断言为空 |
| 网页助手 | S2 `assistant.check_demand_reply()` 出卡前执行相同答复正文拦截 | [test_m6_capacity_assistant.py](../backend/tests/api/routes/test_m6_capacity_assistant.py) 与答复测试仍检查身份伪造、卡片确认和 B 方个人字段不进入 A 方结果 |
| 飞书助手 | S2 复用网页助手校验与提示，禁止少于 3 人时的负荷词答复；本地复跑飞书确认用例 | 已有绑定本人点击卡片和对外卡片不含 B 方个人信息的断言继续保留 |
| MCP | S2 `reply_demand` 与提示语同样拦截；S3b A 方不可读不可用日期且工具列表不暴露登记/删除 | [test_m6_capacity_mcp.py](../backend/tests/api/routes/test_m6_capacity_mcp.py) 的当前令牌身份、跨企业/部门越权及 B 方个人信息排除；[test_m6_capacity_responder_privacy.py](../backend/tests/api/routes/test_m6_capacity_responder_privacy.py) 的答复人隐名 |

M7 的 Planning 测试还对 A 方读取方案返回 404、借调小部门隐藏人数及 `employee_id` 做断言；M7 未开放 Planning 的 MCP 工具。此次只改文档，`git diff` 未删除、跳过或放宽任何既有测试；新增隐私断言来自上述已合并 PR，不是本 PR 新增。

## 迁移与本地验证边界

- `uv run alembic heads`：仅 `c696001bc001 (head)`。在 `nere699` 数据库执行 `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`：通过，实际降级 `c696001bc001 → c693001bc001` 再升级。
- [test_m7_weekly_capacity_migration.py::test_weekly_migration_roundtrip](../backend/tests/test_m7_weekly_capacity_migration.py) 检查 `c641001bc001 ↔ c693001bc001 ↔ head` 的旧负荷迁移、约束及降级重建；[test_m7_planning_migration.py::test_planning_migration_roundtrip](../backend/tests/test_m7_planning_migration.py) 检查 `c693001bc001 ↔ c696001bc001 ↔ head` 的 Planning 表往返。本地运行 `uv run pytest tests/test_m7_weekly_capacity_migration.py tests/test_m7_planning_migration.py -q --no-cov -n 2 --dist loadgroup -o addopts='' --disable-warnings --tb=short`：`2 passed`。
- 相关测试首轮合计 `141 passed, 2 errors`；两项错误发生在助手用例的 Pi 服务准备阶段，本机尚未安装 `pi/package-lock.json` 锁定的 npm 依赖（缺 `@earendil-works/pi-agent-core`）。执行 `cd pi && npm ci --ignore-scripts --no-audit --no-fund` 后，仅重跑两项报错用例，`2 passed`。这是本地依赖准备错误，未修改产品或测试代码。
- 本任务文档检查、前端检查及 Docker 清理后的剩余空间在 NERE-699 的结果评论中列明；完整后端/网页/Compose/安装 CI 由 PR 门禁执行。本次未升级长期环境 `nere497`。
