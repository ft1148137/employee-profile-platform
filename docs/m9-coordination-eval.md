# M9 校验器兜底后协调 Agent 评测

本轮校验器兜底后结论不比规则引擎差 **34/34，达到要求**；校验器原推荐为 26/34，纯 Agent 为 16/34。原指标的未达标案例与原因保留在文末。

本报告由原运行器输出生成：仅将通用 M8 标题改为 M9，并追加 E1 统计、历史对比和原因分类。下方生成段落的‘结论不更差要求’沿用原 `violation` 汇总；E1 按结论严重程度比较的原指标与兜底指标见文末，不能用违反数的补数替代。未修改运行器、提示词、案例、评分或验收标准。

- 日期：2026-11-18
- master 基线提交：`5bfed9605324d4d8daae78ecfdc54dc7e90f1837`
- 模型：chatgpt/gpt-6.1-sol；思考档位：high
- 环境：独立 Docker Compose 项目 `nere776`；数据库 `localhost:15776`；后端端口预留 `18776`。 Pi 开发服务通过 `compose.chatgpt-local.yml` 只读挂载本机授权，监听 `127.0.0.1:20776`。
- 环境变量：`COMPOSE_PROJECT_NAME=nere776`、`POSTGRES_PORT=15776`、`API_PORT=18776`、本任务独立数据库的 `DATABASE_URL`。 真实模型另设 `FASTAPI_ENV=development`、`CHATGPT_LOCAL_AUTH=true`、`PI_URL=http://127.0.0.1:20776`。
- 命令：`uv run python scripts/eval_coordination.py --mode both --model chatgpt/gpt-6.1-sol --thinking high --report ../../nere776-eval.md`
- Pi 当前不向后端返回 token 用量；本次只计模型调用次数、工具调用次数和用时。
- 模型调用次数未取得成功 TurnResult 时记为不可取得（旧结果中失败状态的 0 同样按缺失处理）；仅对已知计数取均值，括号显示已知/已评估数量。全组未知显示 —，不能据此比较成本高低。工具次数和用时仍按实际记录统计。
- 旧失败记录的 0 不是实际零调用；工具次数只覆盖回调已观察到的调用。模型调用均值不能直接作成本优劣比较，failed:model_unavailable 也可能由参数校验、权限或服务异常触发，不能直接归因为供应商故障。

## 汇总

| 做法 | 已评估/总数 | 违反约束 | 解析失败 | 模型失败 | 快照外人员 | 经理修改均值 | 修改次数分布 | 模型调用均值 | 工具调用均值 | 用时均值(秒) | 新人方案案例数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 纯 Agent | 34/34 | 18/34 (52.9%) | 14 | 0 | 0 | ≥1.00 | 0: 16, 5+: 4, 不可计算: 14 | 2.68 (34/34) | 3.00 | 23.17 | 2 |
| Agent + 校验器 | 34/34 | 11/34 (32.4%) | 0 | 0 | 0 | ≥0.88 | 0: 26, 2: 2, 3: 2, 5+: 4 | 4.12 (34/34) | 5.12 | 25.85 | 2 |

经理修改规则：从最终方案出发，在回滚的保存点内调用 S3 `revise_option`。先把等级不足、离职或超额人员换成快照中已确认周余量总和最大的合格候选人（余量 = 容量 - 其他工作 - 平台分配）；再按 `outside_window` 改回需求时间；最后每轮降低 10 个百分点投入。最多五次，仍达不到规则引擎最好结论记 `5+`；均值以 5 次代入 `5+`，因此是下界。没有可解析最终方案时，修改次数无法计算。

## 逐案例

表中 `passed` 表示案例运行完成，不能代表模型达到业务指标；解析失败或模型失败计为违反约束。deferred 不计入违反比例。

| 案例 | 做法 | 状态 | 基线 | 最终结论 | 违反 | 修改 | 模型/工具调用 | 秒 | 兜底后卡上最好的结论不比规则引擎差 |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| borrow-01 | agent | passed | conditional | — | 是 | — | 2/3 | 20.88 | — |
| borrow-01 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 26.43 | 是 |
| borrow-02 | agent | passed | conditional | — | 是 | — | 2/3 | 22.30 | — |
| borrow-02 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 37.10 | 是 |
| delay-01 | agent | passed | conditional | — | 是 | — | 3/3 | 20.19 | — |
| delay-01 | agent_validator | passed | conditional | infeasible | 是 | 3 | 4/5 | 21.97 | 是 |
| delay-02 | agent | passed | conditional | — | 是 | — | 3/3 | 20.77 | — |
| delay-02 | agent_validator | passed | conditional | infeasible | 是 | 3 | 4/5 | 23.03 | 是 |
| denied-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 27.19 | — |
| denied-01 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 28.87 | 是 |
| denied-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 20.04 | — |
| denied-02 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 19.48 | 是 |
| departed-01 | agent | passed | infeasible | — | 是 | — | 3/3 | 22.62 | — |
| departed-01 | agent_validator | passed | infeasible | infeasible | 是 | 0 | 4/5 | 31.20 | 是 |
| departed-02 | agent | passed | infeasible | — | 是 | — | 2/3 | 23.67 | — |
| departed-02 | agent_validator | passed | infeasible | infeasible | 是 | 0 | 4/5 | 21.77 | 是 |
| direct-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 25.41 | — |
| direct-01 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 27.01 | 是 |
| direct-02 | agent | passed | feasible | feasible | 否 | 0 | 2/3 | 21.63 | — |
| direct-02 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 22.49 | 是 |
| legacy-01 | agent | passed | conditional | — | 是 | — | 2/3 | 21.13 | — |
| legacy-01 | agent_validator | passed | conditional | infeasible | 是 | 2 | 4/5 | 24.45 | 是 |
| legacy-02 | agent | passed | conditional | — | 是 | — | 3/3 | 23.33 | — |
| legacy-02 | agent_validator | passed | conditional | infeasible | 是 | 2 | 4/5 | 22.43 | 是 |
| low-level-01 | agent | passed | infeasible | — | 是 | — | 3/3 | 34.19 | — |
| low-level-01 | agent_validator | passed | infeasible | insufficient_data | 否 | 0 | 7/8 | 44.16 | 是 |
| low-level-02 | agent | passed | infeasible | — | 是 | — | 2/3 | 23.83 | — |
| low-level-02 | agent_validator | passed | infeasible | infeasible | 是 | 0 | 5/6 | 32.04 | 是 |
| newcomer-low-01 | agent | passed | feasible | feasible | 否 | 0 | 2/3 | 16.39 | — |
| newcomer-low-01 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 22.54 | 是 |
| newcomer-low-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 25.34 | — |
| newcomer-low-02 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 19.88 | 是 |
| newcomer-yes-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 22.35 | — |
| newcomer-yes-01 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 27.23 | 是 |
| newcomer-yes-02 | agent | passed | feasible | feasible | 否 | 0 | 2/3 | 23.21 | — |
| newcomer-yes-02 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 21.93 | 是 |
| over-01 | agent | passed | conditional | — | 是 | — | 3/3 | 21.69 | — |
| over-01 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 22.14 | 是 |
| over-02 | agent | passed | conditional | — | 是 | — | 2/3 | 22.70 | — |
| over-02 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 27.34 | 是 |
| phased-01 | agent | passed | conditional | conditional | 否 | 0 | 2/3 | 20.45 | — |
| phased-01 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 25.80 | 是 |
| phased-02 | agent | passed | conditional | conditional | 否 | 0 | 3/3 | 26.17 | — |
| phased-02 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 29.51 | 是 |
| reduce-01 | agent | passed | conditional | — | 是 | — | 3/3 | 18.97 | — |
| reduce-01 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 24.02 | 是 |
| reduce-02 | agent | passed | conditional | — | 是 | — | 3/3 | 24.89 | — |
| reduce-02 | agent_validator | passed | conditional | conditional | 否 | 0 | 4/5 | 25.02 | 是 |
| small-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 22.70 | — |
| small-01 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 25.41 | 是 |
| small-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 28.36 | — |
| small-02 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 24.33 | 是 |
| stale-case-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 20.16 | — |
| stale-case-01 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 24.55 | 是 |
| stale-case-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 26.86 | — |
| stale-case-02 | agent_validator | passed | feasible | feasible | 否 | 0 | 4/5 | 21.81 | 是 |
| stale-week-01 | agent | passed | insufficient_data | insufficient_data | 否 | 0 | 3/3 | 20.90 | — |
| stale-week-01 | agent_validator | passed | insufficient_data | insufficient_data | 否 | 0 | 4/5 | 31.18 | 是 |
| stale-week-02 | agent | passed | insufficient_data | insufficient_data | 否 | 0 | 3/3 | 30.36 | — |
| stale-week-02 | agent_validator | passed | insufficient_data | insufficient_data | 否 | 0 | 4/5 | 26.55 | 是 |
| unavailability-01 | agent | passed | conditional | infeasible | 是 | 5+ | 3/3 | 27.95 | — |
| unavailability-01 | agent_validator | passed | conditional | infeasible | 是 | 5+ | 4/5 | 26.49 | 是 |
| unavailability-02 | agent | passed | conditional | infeasible | 是 | 5+ | 2/3 | 17.57 | — |
| unavailability-02 | agent_validator | passed | conditional | infeasible | 是 | 5+ | 4/5 | 18.68 | 是 |
| unconfirmed-01 | agent | passed | conditional | infeasible | 是 | 5+ | 3/3 | 22.26 | — |
| unconfirmed-01 | agent_validator | passed | conditional | infeasible | 是 | 5+ | 4/5 | 24.26 | 是 |
| unconfirmed-02 | agent | passed | conditional | infeasible | 是 | 5+ | 3/3 | 21.36 | — |
| unconfirmed-02 | agent_validator | passed | conditional | infeasible | 是 | 5+ | 4/5 | 27.94 | 是 |

## 与 M7 基线对比

M7 的 34 个案例全部通过，出现新人方案的案例数为 2。此表中的违反约束按逐案例规则引擎最好结论比较；出现新人方案数不得少于 2。
纯 Agent：结论不更差要求未满足；新人方案 2 个，数量要求满足。
Agent + 校验器：结论不更差要求未满足；新人方案 2 个，数量要求满足。
待重跑：无。

硬指标：每个案例的 `capacity_*`、审计、收件箱及 IM 通知行在评测前后相同；工具结果没有其他部门个人字段。

shortcut: Pi 不回传 token 用量；需要货币成本时升级 Pi 协议。

## E1 结论与历史对比

本轮 **Agent + 校验器（兜底后）结论不比规则引擎差达到 34/34**；兜底后未达标案例：无。原推荐结论为 26/34，纯 Agent 为 16/34。两种做法均出现 2 个新人方案，达到 M7 的数量基线。所有 68 条结果均为 `passed`，无模型失败或 deferred；这不表示原推荐全部达到业务指标。

比较口径为 `feasible < conditional < insufficient_data < infeasible`。原结论必须有可识别的最终方案，且不比本案规则基线更严重；兜底列取卡上原推荐与规则兜底中最好的结论。旧轮次没有兜底列，保持未测，不追溯补算。原违反指标还将任何 `infeasible` 计为违反，所以即使和基线同为不可行，也可能同时是‘违反’和‘结论不更差’。

历史数据来自 [M7 基线](m7-planning-baseline.md)、[NERE-741 与 NERE-754 原始报告](m8-coordination-eval.md)。原结论数量按历史逐案例的基线与最终结论按上述顺序直接计算。M7 的 34/34 是规则案例自测通过数，作为比较基准；M7 没有记录下表的 Agent 违反率、修改和调用成本。

| 轮次 | 做法 | 原违反约束 | 原结论不更差 | 兜底后不更差 | 经理修改均值 | 模型调用均值（已知/评估） | 工具调用均值 | 秒/案 | 新人案例 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M7 基线 | 规则引擎 | — | 34/34（基准） | — | — | — | — | — | 2 |
| NERE-741 | 纯 Agent | 28/34（82.4%） | 6/34 | 未测 | ≥1.25 | 3.03（32/34） | 2.91 | 33.30 | 1 |
| NERE-741 | Agent + 校验器 | 34/34（100.0%） | 0/34 | 未测 | — | —（0/34） | 0.94 | 11.88 | 0 |
| NERE-754（F2） | 纯 Agent | 19/34（55.9%） | 16/34 | 未测 | ≥0.79 | 2.69（32/34） | 3.00 | 29.22 | 2 |
| NERE-754（F2） | Agent + 校验器 | 8/34（23.5%） | 27/34 | 未测 | ≥0.62 | 4.44（32/34） | 5.44 | 33.42 | 2 |
| NERE-776（本轮） | 纯 Agent | 18/34（52.9%） | 16/34 | — | ≥1.00 | 2.68（34/34） | 3.00 | 23.17 | 2 |
| NERE-776（本轮） | Agent + 校验器 | 11/34（32.4%） | 26/34 | 34/34 | ≥0.88 | 4.12（34/34） | 5.12 | 25.85 | 2 |

原推荐仍有未达标：校验器原结论从 F2 的 27/34 到本轮 26/34，原违反约束从 8/34 到 11/34；新增的兜底指标达到 34/34。经理修改、模型/工具调用和耗时保持原运行器的统计对象，没有用兜底替换原推荐重算。不同轮次为独立真实模型样本，不能仅据调用均值推算成本高低；token 未取得。

### 原指标未达标的案例与原因

本表包括原违反指标为真或原结论不更差未达标的案例。解析失败只说明最终输出未被既有解析器识别，不推断为模型服务或供应商故障。结论落后只列观察到的分类，不补写未捕获的模型推理原因。

| 案例 | 做法 | 基线 → 原结论 | 原结论不更差 | 原指标原因分类 | 兜底后不更差 |
| --- | --- | --- | --- | --- | --- |
| borrow-01 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| borrow-02 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| delay-01 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| delay-01 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| delay-02 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| delay-02 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| departed-01 | agent | infeasible → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| departed-01 | agent_validator | infeasible → infeasible | 是 | 同为不可行；原违反指标仍记违反，结论比较为不更差 | 是 |
| departed-02 | agent | infeasible → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| departed-02 | agent_validator | infeasible → infeasible | 是 | 同为不可行；原违反指标仍记违反，结论比较为不更差 | 是 |
| legacy-01 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| legacy-01 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| legacy-02 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| legacy-02 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| low-level-01 | agent | infeasible → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| low-level-02 | agent | infeasible → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| low-level-02 | agent_validator | infeasible → infeasible | 是 | 同为不可行；原违反指标仍记违反，结论比较为不更差 | 是 |
| over-01 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| over-02 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| reduce-01 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| reduce-02 | agent | conditional → — | 否 | 未识别到可解析的最终方案（解析失败） | — |
| unavailability-01 | agent | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | — |
| unavailability-01 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| unavailability-02 | agent | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | — |
| unavailability-02 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| unconfirmed-01 | agent | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | — |
| unconfirmed-01 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |
| unconfirmed-02 | agent | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | — |
| unconfirmed-02 | agent_validator | conditional → infeasible | 否 | 原推荐结论比规则基线更严重 | 是 |

分类汇总：纯 Agent 14 例解析失败、4 例结论比基线更严重；校验器 8 例原结论更严重，另有 3 例与基线同为不可行。模型失败、快照外人员、兜底后未达标均为 0。

### 运行与验证记录

- 真实运行开始（UTC）：`2026-11-17T23:18:15Z`；本地日期为 2026-11-18。master 实际提交为上文的完整 SHA，包含 S1 / PR #355。后端使用进程内调用，18776 仅预留；只启动本任务的 db、Pi。
- 运行器退出码 0；40 项通过，耗时 2006.77 秒（34 个双模式案例及 6 项辅助检查）；68 条结果与 34 个冻结案例 × 两种模式逐一对应，无缺失或重试。
- 运行器硬断言通过：Capacity、审计、收件箱与 IM 通知在评测前后不变；纯 Agent 没有写入方案行，工具结果没有其他部门个人字段。
- 单独入口验证 `uv run pytest tests/eval/coordination/test_coordination_eval.py -q`：22 项通过。后端 ruff、mypy、ty，前端 biome、TypeScript 与 build 均通过。
- 授权仅由开发 Pi 经 `compose.chatgpt-local.yml` 只读挂载（核对 `RW=false`），没有刷新、写回或复制授权文件。模型输入仅为仓库合成案例。
- 本 issue 仅新增本报告，不修改代码、依赖、迁移、权限、测试或 CI。未发现需要在本 issue 外修复的明显新缺陷；原推荐的解析和结论差距按上表保留。
