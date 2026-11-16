# M8 协调 Agent 评测

- 日期：2026-11-17
- master 基线提交：`93dda4c109ee6a1d3ccef9a82041d6fb32fead32`
- 模型：chatgpt/gpt-6.1-sol；思考档位：high
- 环境：独立 Docker Compose 项目 `nere741`；数据库 `localhost:15741`；后端端口预留 `18741`。 Pi 开发服务通过 `compose.chatgpt-local.yml` 只读挂载本机授权，监听 `127.0.0.1:20741`。
- 环境变量：`COMPOSE_PROJECT_NAME=nere741`、`POSTGRES_PORT=15741`、`API_PORT=18741`、本任务独立数据库的 `DATABASE_URL`。 真实模型另设 `FASTAPI_ENV=development`、`CHATGPT_LOCAL_AUTH=true`、`PI_URL=http://127.0.0.1:20741`。
- 命令：`uv run python scripts/eval_coordination.py --mode both --model chatgpt/gpt-6.1-sol --thinking high --report ../docs/m8-coordination-eval.md`
- Pi 当前不向后端返回 token 用量；本次只计模型调用次数、工具调用次数和用时。

## 汇总

| 做法 | 完成/总数 | 违反约束 | 解析失败 | 快照外人员 | 经理修改均值 | 修改次数分布 | 模型调用均值 | 工具调用均值 | 用时均值(秒) | 新人方案案例数 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 纯 Agent | 34/34 | 34/34 (100.0%) | 34 | 0 | — | 不可计算: 34 | 4.21 | 3.59 | 35.96 | 0 |
| Agent + 校验器 | 34/34 | 34/34 (100.0%) | 34 | 0 | — | 不可计算: 34 | 3.21 | 2.21 | 19.08 | 0 |

经理修改规则：从最终方案出发，在回滚的保存点内调用 S3 `revise_option`。先把等级不足、离职或超额人员换成快照中已确认周余量总和最大的合格候选人（余量 = 容量 - 其他工作 - 平台分配）；再按 `outside_window` 改回需求时间；最后每轮降低 10 个百分点投入。最多五次，仍达不到规则引擎最好结论记 `5+`；均值以 5 次代入 `5+`，因此是下界。没有可解析最终方案时，修改次数无法计算。

## 逐案例

表中 `passed` 表示案例运行完成，不能代表模型达到业务指标；解析失败计为违反约束。

| 案例 | 做法 | 状态 | 基线 | 最终结论 | 违反 | 修改 | 模型/工具调用 | 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| borrow-01 | agent | passed | conditional | — | 是 | — | 4/3 | 31.16 |
| borrow-01 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 16.06 |
| borrow-02 | agent | passed | conditional | — | 是 | — | 4/3 | 26.84 |
| borrow-02 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 16.80 |
| delay-01 | agent | passed | conditional | — | 是 | — | 4/4 | 31.86 |
| delay-01 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 21.88 |
| delay-02 | agent | passed | conditional | — | 是 | — | 3/3 | 28.91 |
| delay-02 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 17.73 |
| denied-01 | agent | passed | feasible | — | 是 | — | 4/4 | 40.29 |
| denied-01 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 27.51 |
| denied-02 | agent | passed | feasible | — | 是 | — | 4/3 | 33.48 |
| denied-02 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 18.46 |
| departed-01 | agent | passed | infeasible | — | 是 | — | 4/3 | 41.34 |
| departed-01 | agent_validator | passed | infeasible | — | 是 | — | 3/2 | 15.36 |
| departed-02 | agent | passed | infeasible | — | 是 | — | 4/4 | 29.26 |
| departed-02 | agent_validator | passed | infeasible | — | 是 | — | 3/2 | 18.03 |
| direct-01 | agent | passed | feasible | — | 是 | — | 4/3 | 38.63 |
| direct-01 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 21.60 |
| direct-02 | agent | passed | feasible | — | 是 | — | 4/4 | 40.39 |
| direct-02 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 20.55 |
| legacy-01 | agent | passed | conditional | — | 是 | — | 4/4 | 39.70 |
| legacy-01 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 17.13 |
| legacy-02 | agent | passed | conditional | — | 是 | — | 4/4 | 40.93 |
| legacy-02 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 14.98 |
| low-level-01 | agent | passed | infeasible | — | 是 | — | 4/3 | 30.94 |
| low-level-01 | agent_validator | passed | infeasible | — | 是 | — | 4/3 | 24.40 |
| low-level-02 | agent | passed | infeasible | — | 是 | — | 5/4 | 34.13 |
| low-level-02 | agent_validator | passed | infeasible | — | 是 | — | 4/3 | 21.12 |
| newcomer-low-01 | agent | passed | feasible | — | 是 | — | 5/4 | 33.76 |
| newcomer-low-01 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 13.54 |
| newcomer-low-02 | agent | passed | feasible | — | 是 | — | 5/4 | 36.56 |
| newcomer-low-02 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 16.75 |
| newcomer-yes-01 | agent | passed | feasible | — | 是 | — | 5/4 | 39.04 |
| newcomer-yes-01 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 18.61 |
| newcomer-yes-02 | agent | passed | feasible | — | 是 | — | 4/3 | 26.33 |
| newcomer-yes-02 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 14.04 |
| over-01 | agent | passed | conditional | — | 是 | — | 3/3 | 30.07 |
| over-01 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 26.86 |
| over-02 | agent | passed | conditional | — | 是 | — | 4/3 | 21.71 |
| over-02 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 21.35 |
| phased-01 | agent | passed | conditional | — | 是 | — | 5/5 | 37.76 |
| phased-01 | agent_validator | passed | conditional | — | 是 | — | 4/3 | 26.11 |
| phased-02 | agent | passed | conditional | — | 是 | — | 4/3 | 36.27 |
| phased-02 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 17.11 |
| reduce-01 | agent | passed | conditional | — | 是 | — | 3/2 | 19.63 |
| reduce-01 | agent_validator | passed | conditional | — | 是 | — | 4/3 | 17.66 |
| reduce-02 | agent | passed | conditional | — | 是 | — | 6/5 | 55.23 |
| reduce-02 | agent_validator | passed | conditional | — | 是 | — | 4/3 | 24.04 |
| small-01 | agent | passed | feasible | — | 是 | — | 4/3 | 35.27 |
| small-01 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 15.97 |
| small-02 | agent | passed | feasible | — | 是 | — | 4/4 | 38.53 |
| small-02 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 16.93 |
| stale-case-01 | agent | passed | feasible | — | 是 | — | 4/3 | 36.35 |
| stale-case-01 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 26.59 |
| stale-case-02 | agent | passed | feasible | — | 是 | — | 4/4 | 38.23 |
| stale-case-02 | agent_validator | passed | feasible | — | 是 | — | 3/2 | 14.99 |
| stale-week-01 | agent | passed | insufficient_data | — | 是 | — | 5/4 | 46.05 |
| stale-week-01 | agent_validator | passed | insufficient_data | — | 是 | — | 3/2 | 15.94 |
| stale-week-02 | agent | passed | insufficient_data | — | 是 | — | 5/4 | 50.57 |
| stale-week-02 | agent_validator | passed | insufficient_data | — | 是 | — | 3/2 | 19.24 |
| unavailability-01 | agent | passed | conditional | — | 是 | — | 4/3 | 46.50 |
| unavailability-01 | agent_validator | passed | conditional | — | 是 | — | 4/3 | 20.07 |
| unavailability-02 | agent | passed | conditional | — | 是 | — | 4/4 | 30.82 |
| unavailability-02 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 18.50 |
| unconfirmed-01 | agent | passed | conditional | — | 是 | — | 4/4 | 34.66 |
| unconfirmed-01 | agent_validator | passed | conditional | — | 是 | — | 3/2 | 12.14 |
| unconfirmed-02 | agent | passed | conditional | — | 是 | — | 5/4 | 41.53 |
| unconfirmed-02 | agent_validator | passed | conditional | — | 是 | — | 4/3 | 20.67 |

## 与 M7 基线对比

M7 的 34 个案例全部通过，出现新人方案的案例数为 2。此表中的违反约束按逐案例规则引擎最好结论比较；出现新人方案数不得少于 2。
纯 Agent：结论不更差要求未满足；新人方案 0 个，数量要求未满足。
Agent + 校验器：结论不更差要求未满足；新人方案 0 个，数量要求未满足。
待重跑：无。

硬指标：每个案例的 `capacity_*`、审计及收件箱及 IM 通知行在评测前后相同；工具结果没有其他部门个人字段。

shortcut: Pi 不回传 token 用量；需要货币成本时升级 Pi 协议。
