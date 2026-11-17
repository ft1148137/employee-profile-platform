# M8 协调 Agent 评测

- 日期：2026-11-17
- master 基线提交：`b3de3602c8b167efb9bf3b69f093934b04199b92`
- 模型：chatgpt/gpt-6.1-sol；思考档位：high
- 环境：独立 Docker Compose 项目 `nere741`；数据库 `localhost:15741`；后端端口预留 `18741`。 Pi 开发服务通过 `compose.chatgpt-local.yml` 只读挂载本机授权，监听 `127.0.0.1:20741`。
- 环境变量：`COMPOSE_PROJECT_NAME=nere741`、`POSTGRES_PORT=15741`、`API_PORT=18741`、本任务独立数据库的 `DATABASE_URL`。 真实模型另设 `FASTAPI_ENV=development`、`CHATGPT_LOCAL_AUTH=true`、`PI_URL=http://127.0.0.1:20741`。
- 命令：`uv run python scripts/eval_coordination.py --mode both --model chatgpt/gpt-6.1-sol --thinking high --report ../docs/m8-coordination-eval.md`
- Pi 当前不向后端返回 token 用量；本次只计模型调用次数、工具调用次数和用时。
- 模型调用次数未取得成功 TurnResult 时记为不可取得（旧结果中失败状态的 0 同样按缺失处理）；仅对已知计数取均值，括号显示已知/已评估数量。全组未知显示 —，不能据此比较成本高低。工具次数和用时仍按实际记录统计。
- 旧失败记录的 0 不是实际零调用；工具次数只覆盖回调已观察到的调用。模型调用均值不能直接作成本优劣比较，failed:model_unavailable 也可能由参数校验、权限或服务异常触发，不能直接归因为供应商故障。
- 统计修订：保留 2026-11-17 本轮 68 条真实模型结果，仅将失败状态的旧 0 计数按缺失处理；没有重新运行模型或改动其他指标。

## 汇总

| 做法 | 已评估/总数 | 违反约束 | 解析失败 | 模型失败 | 快照外人员 | 经理修改均值 | 修改次数分布 | 模型调用均值 | 工具调用均值 | 用时均值(秒) | 新人方案案例数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 纯 Agent | 34/34 | 28/34 (82.4%) | 26 | 2 | 0 | ≥1.25 | 0: 6, 5+: 2, 不可计算: 26 | 3.03 (32/34) | 2.91 | 33.30 | 1 |
| Agent + 校验器 | 34/34 | 34/34 (100.0%) | 34 | 34 | 0 | — | 不可计算: 34 | — (0/34) | 0.94 | 11.88 | 0 |

经理修改规则：从最终方案出发，在回滚的保存点内调用 S3 `revise_option`。先把等级不足、离职或超额人员换成快照中已确认周余量总和最大的合格候选人（余量 = 容量 - 其他工作 - 平台分配）；再按 `outside_window` 改回需求时间；最后每轮降低 10 个百分点投入。最多五次，仍达不到规则引擎最好结论记 `5+`；均值以 5 次代入 `5+`，因此是下界。没有可解析最终方案时，修改次数无法计算。

## 逐案例

表中 `passed` 表示案例运行完成，不能代表模型达到业务指标；解析失败或模型失败计为违反约束。deferred 不计入违反比例。

| 案例 | 做法 | 状态 | 基线 | 最终结论 | 违反 | 修改 | 模型/工具调用 | 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| borrow-01 | agent | passed | conditional | — | 是 | — | 3/3 | 19.46 |
| borrow-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 3.60 |
| borrow-02 | agent | failed:model_unavailable | conditional | — | 是 | — | 不可取得/3 | 132.17 |
| borrow-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.74 |
| delay-01 | agent | passed | conditional | — | 是 | — | 3/3 | 22.87 |
| delay-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.61 |
| delay-02 | agent | passed | conditional | — | 是 | — | 3/3 | 29.02 |
| delay-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 3.74 |
| denied-01 | agent | passed | feasible | — | 是 | — | 3/3 | 28.81 |
| denied-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 5.02 |
| denied-02 | agent | passed | feasible | — | 是 | — | 3/3 | 26.66 |
| denied-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 3.64 |
| departed-01 | agent | passed | infeasible | — | 是 | — | 3/3 | 23.52 |
| departed-01 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 不可取得/1 | 3.70 |
| departed-02 | agent | passed | infeasible | — | 是 | — | 3/3 | 32.28 |
| departed-02 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 不可取得/1 | 5.27 |
| direct-01 | agent | passed | feasible | — | 是 | — | 3/3 | 20.79 |
| direct-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 5.55 |
| direct-02 | agent | passed | feasible | — | 是 | — | 3/3 | 29.18 |
| direct-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 6.80 |
| legacy-01 | agent | passed | conditional | — | 是 | — | 3/3 | 37.26 |
| legacy-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.27 |
| legacy-02 | agent | passed | conditional | — | 是 | — | 3/3 | 25.43 |
| legacy-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.84 |
| low-level-01 | agent | passed | infeasible | — | 是 | — | 3/3 | 29.32 |
| low-level-01 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 不可取得/1 | 4.26 |
| low-level-02 | agent | passed | infeasible | — | 是 | — | 3/3 | 23.22 |
| low-level-02 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 不可取得/1 | 4.19 |
| newcomer-low-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 25.94 |
| newcomer-low-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 3.55 |
| newcomer-low-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 20.87 |
| newcomer-low-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 3.67 |
| newcomer-yes-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 24.63 |
| newcomer-yes-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 4.00 |
| newcomer-yes-02 | agent | passed | feasible | — | 是 | — | 3/3 | 31.98 |
| newcomer-yes-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 4.87 |
| over-01 | agent | failed:model_unavailable | conditional | — | 是 | — | 不可取得/0 | 123.62 |
| over-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 3.76 |
| over-02 | agent | passed | conditional | — | 是 | — | 3/3 | 24.37 |
| over-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.79 |
| phased-01 | agent | passed | conditional | — | 是 | — | 3/3 | 31.82 |
| phased-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/0 | 124.56 |
| phased-02 | agent | passed | conditional | feasible | 否 | 0 | 3/3 | 45.69 |
| phased-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.55 |
| reduce-01 | agent | passed | conditional | — | 是 | — | 3/3 | 26.53 |
| reduce-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.04 |
| reduce-02 | agent | passed | conditional | — | 是 | — | 3/3 | 26.15 |
| reduce-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 7.04 |
| small-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 26.06 |
| small-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 4.74 |
| small-02 | agent | passed | feasible | — | 是 | — | 3/3 | 24.97 |
| small-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 4.36 |
| stale-case-01 | agent | passed | feasible | — | 是 | — | 3/3 | 22.63 |
| stale-case-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/1 | 6.48 |
| stale-case-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 22.65 |
| stale-case-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 不可取得/0 | 123.90 |
| stale-week-01 | agent | passed | insufficient_data | — | 是 | — | 3/3 | 26.57 |
| stale-week-01 | agent_validator | failed:model_unavailable | insufficient_data | — | 是 | — | 不可取得/1 | 6.10 |
| stale-week-02 | agent | passed | insufficient_data | — | 是 | — | 3/3 | 27.62 |
| stale-week-02 | agent_validator | failed:model_unavailable | insufficient_data | — | 是 | — | 不可取得/1 | 8.71 |
| unavailability-01 | agent | passed | conditional | infeasible | 是 | 5+ | 3/3 | 25.64 |
| unavailability-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 4.21 |
| unavailability-02 | agent | passed | conditional | — | 是 | — | 3/3 | 29.11 |
| unavailability-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 5.08 |
| unconfirmed-01 | agent | passed | conditional | insufficient_data | 是 | 5+ | 4/3 | 33.51 |
| unconfirmed-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 6.10 |
| unconfirmed-02 | agent | passed | conditional | — | 是 | — | 3/3 | 31.88 |
| unconfirmed-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 不可取得/1 | 5.00 |

## 与 M7 基线对比

M7 的 34 个案例全部通过，出现新人方案的案例数为 2。此表中的违反约束按逐案例规则引擎最好结论比较；出现新人方案数不得少于 2。
纯 Agent：结论不更差要求未满足；新人方案 1 个，数量要求未满足。
Agent + 校验器：结论不更差要求未满足；新人方案 0 个，数量要求未满足。
待重跑：无。

硬指标：每个案例的 `capacity_*`、审计、收件箱及 IM 通知行在评测前后相同；工具结果没有其他部门个人字段。

shortcut: Pi 不回传 token 用量；需要货币成本时升级 Pi 协议。

## NERE-750 独立验证（2026-11-17）

以上是 PR #338 在 2026-11-17 的原始真实模型成绩及其计数修订，本节不替换它们，也不是完整 34 案例的新基线。

- 修复提交：`0b943fb761df8dde2f8b552812f53685ed57251f`；后续测试适配 PR #338 将未知模型计数改为 `None` 的修订。
- 模型：`synthetic` / `off`；使用脚本模型及 HTTP MockTransport，没有调用真实供应商，也没有读取本机授权。
- 环境：`COMPOSE_PROJECT_NAME=nere750`，独立 PostgreSQL `127.0.0.1:15750`，后端预留 `18750`；只启动数据库。
- 入口：复用 `tests/eval/coordination/test_coordination_eval.py::evaluate()` 的原案例造数、评测协议、`bot.core.turn`、结果解析、业务行不变及跨部门隐私断言。错误链测试继续走真实 `pi_client.run_turn`，只替换 HTTP 传输和合成模型配置。
- 样本：`direct-01`、`newcomer-yes-01`、本轮同样失败的 `delay-01`。两种模式各 3 个脚本样本，6 项测试均得到可解析结果；新人案例的规则基线包含新人方案，验证器模式仍选择新人。原评测的纯 Agent 脚本按候选顺序选人，不保证选中新人；本测试不把这种脚本选择当成业务达标。脚本结果不代表真实模型达标。
- 专用测试：`backend/tests/api/routes/test_nere750_coordination_contract.py`（模型可见参数、工具上限、首次模拟、自定方案和比较）；`backend/tests/eval/test_nere750_coordination_failures.py`（6 项样本检查和下列 6 项分类检查）。执行：`cd backend && uv run pytest tests/api/routes/test_nere750_coordination_contract.py tests/eval/test_nere750_coordination_failures.py --no-cov -x -q`，使用本任务的 `DATABASE_URL`、`POSTGRES_PORT=15750`。

### 最小复现与错误分类

参数只保留形状，不保存员工正文、会话或凭据。表中 `UUID` 表示本地合成数据的 UUID；分类断言和异常观察只存在于本任务测试，生产失败策略不变。

| 分类 / 样本 | 合成输入或故障 | 原始类别与触发位置 | 原评测结果 |
| --- | --- | --- | --- |
| 参数校验 / direct-01 | `coordination({function: simulate_plan, arguments: {}})` | `ValidationError`，`bot/tools.py:588` 的输入模型校验；错误位置 `demand_id`，类型 `missing` | `failed:model_unavailable`，模型计数不可取得 |
| 权限拒绝 / newcomer-yes-01 | `{demand_id: UUID}`；在测试中将服务调用的 Principal 管辖部门收窄为空 | `CapacityNotFound`（404），`capacity/demand.py:359` 的服务端读取范围检查；不改变数据库权限 | 同上 |
| 服务异常 / delay-01 | `{demand_id: UUID}`；测试 handler 抛出合成 `RuntimeError` | `bot/tools.py:592` 调用服务时，随后进入通用异常分支；这是故障注入，不是已发现的生产服务缺陷 | 同上 |
| Pi / 供应商 / direct-01 | Pi 返回 `upstream_unavailable` 事件 | `ModelServiceError`，`model/pi_client.py::_error()` | 同上；此样本才明确模拟供应商异常 |
| Pi / 协议 / direct-01 | Pi 返回不可解析的 NDJSON | `ModelUnavailable`，`model/pi_client.py::run_turn()` 的协议解析错误分支 | 同上；与供应商事件分开 |
| 最终输出解析 / delay-01，纯 Agent | 成功 TurnResult，文本为「没有最终方案」 | `parse_option()` 返回 `None`，没有模型异常；已知模型调用 1 次 | `status=passed`、`parse_failure=true`、违反约束；passed 仅表示运行完成 |

前三类分别有不同的起点，但都会经过 `bot/tools.py:654-657` 的通用 error、`bot/core.py:94-99` 的 `ModelUnavailable(code=coordination_tool)`，再由 `model/pi_client.py:224-236` 将回调失败转为 `ModelUnavailable(code=unavailable)`。测试核对 Pi 收到的工具失败结果只有「平台工具调用失败」及 `is_error=true`；原始异常不进入响应。这解释了为何不能从通用失败文案反推根因。

### 已证实与仍未知

已证实：修复前向模型公开的 CoordinationInput 允许空 `arguments`，缺少六个子操作的字段和 required 说明；专用测试先失败后通过。修复直接从现有 `COORDINATION_TOOLS.input_model.model_json_schema()` 生成模型可见说明，包含嵌套 assignees 类型、UUID、日期和投入上下限；首次只传 demand_id，自定方案须先做首次模拟；工具总数仍不超过 29。不改 Pi 协议、失败策略、权限、系统提示词和案例。

仍未知：原始 34 次 agent_validator 失败各自的实际参数、异常类别和触发点，以及纯 Agent 的 26 次解析失败各自的原因。原记录没有这些证据，本次可控复现不能补写其历史根因。没有重跑完整真实评测，没有把 pytest 通过写成业务基线通过；本次也没有确认出需要扩大生产修改范围的新缺陷。后续真实样本或全量评测由父 issue 决定。
