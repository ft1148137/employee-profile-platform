# M8 协调 Agent 评测

- 日期：2026-11-17
- master 基线提交：`b3de3602c8b167efb9bf3b69f093934b04199b92`
- 模型：chatgpt/gpt-6.1-sol；思考档位：high
- 环境：独立 Docker Compose 项目 `nere741`；数据库 `localhost:15741`；后端端口预留 `18741`。 Pi 开发服务通过 `compose.chatgpt-local.yml` 只读挂载本机授权，监听 `127.0.0.1:20741`。
- 环境变量：`COMPOSE_PROJECT_NAME=nere741`、`POSTGRES_PORT=15741`、`API_PORT=18741`、本任务独立数据库的 `DATABASE_URL`。 真实模型另设 `FASTAPI_ENV=development`、`CHATGPT_LOCAL_AUTH=true`、`PI_URL=http://127.0.0.1:20741`。
- 命令：`uv run python scripts/eval_coordination.py --mode both --model chatgpt/gpt-6.1-sol --thinking high --report ../docs/m8-coordination-eval.md`
- Pi 当前不向后端返回 token 用量；本次只计模型调用次数、工具调用次数和用时。

## 汇总

| 做法 | 已评估/总数 | 违反约束 | 解析失败 | 模型失败 | 快照外人员 | 经理修改均值 | 修改次数分布 | 模型调用均值 | 工具调用均值 | 用时均值(秒) | 新人方案案例数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 纯 Agent | 34/34 | 28/34 (82.4%) | 26 | 2 | 0 | ≥1.25 | 0: 6, 5+: 2, 不可计算: 26 | 2.85 | 2.91 | 33.30 | 1 |
| Agent + 校验器 | 34/34 | 34/34 (100.0%) | 34 | 34 | 0 | — | 不可计算: 34 | 0.00 | 0.94 | 11.88 | 0 |

经理修改规则：从最终方案出发，在回滚的保存点内调用 S3 `revise_option`。先把等级不足、离职或超额人员换成快照中已确认周余量总和最大的合格候选人（余量 = 容量 - 其他工作 - 平台分配）；再按 `outside_window` 改回需求时间；最后每轮降低 10 个百分点投入。最多五次，仍达不到规则引擎最好结论记 `5+`；均值以 5 次代入 `5+`，因此是下界。没有可解析最终方案时，修改次数无法计算。

## 逐案例

表中 `passed` 表示案例运行完成，不能代表模型达到业务指标；解析失败或模型失败计为违反约束。deferred 不计入违反比例。

| 案例 | 做法 | 状态 | 基线 | 最终结论 | 违反 | 修改 | 模型/工具调用 | 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| borrow-01 | agent | passed | conditional | — | 是 | — | 3/3 | 19.46 |
| borrow-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 3.60 |
| borrow-02 | agent | failed:model_unavailable | conditional | — | 是 | — | 0/3 | 132.17 |
| borrow-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.74 |
| delay-01 | agent | passed | conditional | — | 是 | — | 3/3 | 22.87 |
| delay-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.61 |
| delay-02 | agent | passed | conditional | — | 是 | — | 3/3 | 29.02 |
| delay-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 3.74 |
| denied-01 | agent | passed | feasible | — | 是 | — | 3/3 | 28.81 |
| denied-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 5.02 |
| denied-02 | agent | passed | feasible | — | 是 | — | 3/3 | 26.66 |
| denied-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 3.64 |
| departed-01 | agent | passed | infeasible | — | 是 | — | 3/3 | 23.52 |
| departed-01 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 0/1 | 3.70 |
| departed-02 | agent | passed | infeasible | — | 是 | — | 3/3 | 32.28 |
| departed-02 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 0/1 | 5.27 |
| direct-01 | agent | passed | feasible | — | 是 | — | 3/3 | 20.79 |
| direct-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 5.55 |
| direct-02 | agent | passed | feasible | — | 是 | — | 3/3 | 29.18 |
| direct-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 6.80 |
| legacy-01 | agent | passed | conditional | — | 是 | — | 3/3 | 37.26 |
| legacy-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.27 |
| legacy-02 | agent | passed | conditional | — | 是 | — | 3/3 | 25.43 |
| legacy-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.84 |
| low-level-01 | agent | passed | infeasible | — | 是 | — | 3/3 | 29.32 |
| low-level-01 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 0/1 | 4.26 |
| low-level-02 | agent | passed | infeasible | — | 是 | — | 3/3 | 23.22 |
| low-level-02 | agent_validator | failed:model_unavailable | infeasible | — | 是 | — | 0/1 | 4.19 |
| newcomer-low-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 25.94 |
| newcomer-low-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 3.55 |
| newcomer-low-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 20.87 |
| newcomer-low-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 3.67 |
| newcomer-yes-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 24.63 |
| newcomer-yes-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 4.00 |
| newcomer-yes-02 | agent | passed | feasible | — | 是 | — | 3/3 | 31.98 |
| newcomer-yes-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 4.87 |
| over-01 | agent | failed:model_unavailable | conditional | — | 是 | — | 0/0 | 123.62 |
| over-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 3.76 |
| over-02 | agent | passed | conditional | — | 是 | — | 3/3 | 24.37 |
| over-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.79 |
| phased-01 | agent | passed | conditional | — | 是 | — | 3/3 | 31.82 |
| phased-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/0 | 124.56 |
| phased-02 | agent | passed | conditional | feasible | 否 | 0 | 3/3 | 45.69 |
| phased-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.55 |
| reduce-01 | agent | passed | conditional | — | 是 | — | 3/3 | 26.53 |
| reduce-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.04 |
| reduce-02 | agent | passed | conditional | — | 是 | — | 3/3 | 26.15 |
| reduce-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 7.04 |
| small-01 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 26.06 |
| small-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 4.74 |
| small-02 | agent | passed | feasible | — | 是 | — | 3/3 | 24.97 |
| small-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 4.36 |
| stale-case-01 | agent | passed | feasible | — | 是 | — | 3/3 | 22.63 |
| stale-case-01 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/1 | 6.48 |
| stale-case-02 | agent | passed | feasible | feasible | 否 | 0 | 3/3 | 22.65 |
| stale-case-02 | agent_validator | failed:model_unavailable | feasible | — | 是 | — | 0/0 | 123.90 |
| stale-week-01 | agent | passed | insufficient_data | — | 是 | — | 3/3 | 26.57 |
| stale-week-01 | agent_validator | failed:model_unavailable | insufficient_data | — | 是 | — | 0/1 | 6.10 |
| stale-week-02 | agent | passed | insufficient_data | — | 是 | — | 3/3 | 27.62 |
| stale-week-02 | agent_validator | failed:model_unavailable | insufficient_data | — | 是 | — | 0/1 | 8.71 |
| unavailability-01 | agent | passed | conditional | infeasible | 是 | 5+ | 3/3 | 25.64 |
| unavailability-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 4.21 |
| unavailability-02 | agent | passed | conditional | — | 是 | — | 3/3 | 29.11 |
| unavailability-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 5.08 |
| unconfirmed-01 | agent | passed | conditional | insufficient_data | 是 | 5+ | 4/3 | 33.51 |
| unconfirmed-01 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 6.10 |
| unconfirmed-02 | agent | passed | conditional | — | 是 | — | 3/3 | 31.88 |
| unconfirmed-02 | agent_validator | failed:model_unavailable | conditional | — | 是 | — | 0/1 | 5.00 |

## 与 M7 基线对比

M7 的 34 个案例全部通过，出现新人方案的案例数为 2。此表中的违反约束按逐案例规则引擎最好结论比较；出现新人方案数不得少于 2。
纯 Agent：结论不更差要求未满足；新人方案 1 个，数量要求未满足。
Agent + 校验器：结论不更差要求未满足；新人方案 0 个，数量要求未满足。
待重跑：无。

硬指标：每个案例的 `capacity_*`、审计、收件箱及 IM 通知行在评测前后相同；工具结果没有其他部门个人字段。

shortcut: Pi 不回传 token 用量；需要货币成本时升级 Pi 协议。
