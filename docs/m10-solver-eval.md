# M10 多项目多角色求解器评测

## 环境与复现

- 基线 `e539fe1d82825061b6c67ed01a025c9bc3610170`（包含 A2）。宿主机 macOS arm64，Python 3.14.3，uv 0.9.27。CP-SAT 固定单线程和种子 0，每案最多求解 10 秒，原型要求证明最优才出结果。
- OR-Tools 固定为 `9.15.6755`，2026-01-14 发布，Apache-2.0，仅在后端开发依赖组安装。宿主机一次从空虚拟环境同步全工作区用时 6.19 秒，新增 OR-Tools、NumPy、pandas 的 macOS wheel 合计下载约 37 MiB。同一宿主机使用缓存下载、全新虚拟环境以及 CI 的字节码编译选项测得基线 11.55 秒，本分支 17.52 秒（仅同步依赖，排除构建应用），增加 5.97 秒。[OR-Tools 发布页](https://pypi.org/project/ortools/9.15.6755/)
- Agent 使用实际运行的 Pi 和 `chatgpt/gpt-6.1-sol`，思考档位 `low`。Pi 使用开发 Compose 的只读本机授权挂载，监听 `127.0.0.1:20838`。评测脚本只给模型发送本套合成案例和匿名回放数据。`replay-unavailability-01` 首次返回 `upstream_unavailable`，用相同命令加 `--case replay-unavailability-01` 单独重跑一次。表中的 Agent 结果和耗时来自成功的重跑。
- Compose 项目 `nere838`。宿主测试数据库使用 `127.0.0.1:15838`，后端健康检查使用 `127.0.0.1:18838`。运行命令前设置 `DATABASE_URL` 指向此评测数据库、`POSTGRES_PORT=15838`、`COMPOSE_PROJECT_NAME=nere838` 和 `API_PORT=18838`。

在仓库根目录运行 `uv sync --all-packages --frozen`。独立数据库启动并确认映射后，从 `backend/` 执行：

```bash
uv run bash scripts/prestart.sh
FASTAPI_ENV=development uv run pytest tests/eval/planning --no-cov -x -q -n 2 --dist loadgroup
FASTAPI_ENV=development CHATGPT_LOCAL_AUTH=true PI_URL=http://127.0.0.1:20838 \
  uv run python -m scripts.eval_solver --agent
```

用 `SOLVER_EXPORT_REPLAYS=1 uv run pytest tests/eval/planning/contention/test_solver.py -q -k 'test_export_saved_replay'` 生成两份回放。测试使用合成企业启光（`examples/test-enterprises/qiguang-aiot`），通过 Planning API 创建并持久化快照，再按企业 UUID 和快照 UUID 读取数据库行，通过 `export_planning_case` 导出。两个回放分别覆盖多能力和不可用日期。也可执行 `uv run python -m scripts.export_planning_case --org-id <组织UUID> --snapshot-id <快照UUID> --output replay.json` 导出指定行。回放日期固定为捕获值，读取时仍使用各自快照的当前周。

## 评测口径

- 12 个争夺案例由 `synthetic.yaml` 声明。前四例把更灵活的角色放在前面，让按余量排序的规则先消耗关键人选。2 个单角色回放用于校验真实快照形状，不计入 D-033(a) 的争夺案例分母。
- 全员满额覆盖角色完整窗口才算配齐。三种做法都复用周容量语义。不可用工作日按每个工作日 20% 扣减，已确认的其他工作、平台分配、预占和本批其他方案共同占用余量。前置角色必须完成，下游才能开始。允许整周平移最多八周，平移一周记 5 个工作日。分段可在周交界交接，规则模拟和 CP-SAT 使用相同的窗口中点。延期只对配齐的角色求和。新人方案统计包含至少一名新人的已配齐角色方案数。
- 规则按提交顺序调用生产模拟器，取延期最少的满额方案。同等延期优先用模拟器原顺序。Agent 按相同顺序逐角色在 Pi 中运行，沿用协调评测的「先模拟、再选择或验证修改」协议。离线工具在内存中对当前匿名快照调用现有模拟器和校验器。已选安排影响后续角色的快照。只采纳通过校验的满额方案，落地前的模型输出不计入硬约束违反数。
- CP-SAT 同时安排全部角色，依次最大化配齐数、最小化总延期、最大化含新人方案数。评测使用原校验器加角色间依赖和占用复核，以违反任一硬约束的输出角色方案数计数。耗时包括求解器生成方案，包含实际模型往返，指标汇总用各案中位数。

## 生产镜像实测

使用生产 `compose.yml` 和仓库外端口覆盖文件构建。模型评测结束后，用去掉 `FASTAPI_ENV` 的环境文件重新创建后端及 Pi，启动时未加载开发授权覆盖。

```bash
docker compose -f compose.yml -f ../nere838-ports.yml build backend
umask 077
sed '/^FASTAPI_ENV=/d' .env > ../nere838-production.env
unset FASTAPI_ENV
compose_prod() {
  docker compose --env-file ../nere838-production.env -f compose.yml -f ../nere838-ports.yml "$@"
}
compose_prod up -d --wait --no-build backend
compose_prod exec -T backend python -c 'import ortools'
compose_prod exec -T backend python -c 'import app.main'
compose_prod exec -T backend sh -c 'command -v fastapi'
curl -fsS http://127.0.0.1:18838/api/v1/utils/health-check/
```

- `import ortools` 退出码 1，错误为 `ModuleNotFoundError: No module named 'ortools'`。
- `import app.main` 退出码 0，且应用设置 `FASTAPI_ENV is None`。另用 `importlib.util.find_spec` 确认 `pytest`、`ruff`、`mypy` 均未安装。`fastapi` 位于 `/app/.venv/bin/fastapi`。
- Compose 将后端标为 `healthy`。宿主机请求健康端点返回 HTTP 200，正文为 `true`。

## 结果

逐案例单元格依次为 **配齐角色数 / 总延期工作日 / 含新人方案数 / 硬约束违反数 / 秒**。

| 案例 | 角色数 | 规则 | Agent + 校验器 | CP-SAT |
| --- | ---: | --- | --- | --- |
| `trap-primary` | 6 | 6 / 25 / 0 / 0 / 0.0061 | 6 / 25 / 0 / 0 / 28.8490 | 6 / 5 / 3 / 0 / 0.0946 |
| `trap-extra` | 6 | 6 / 25 / 0 / 0 / 0.0130 | 6 / 25 / 0 / 0 / 24.5007 | 6 / 5 / 3 / 0 / 0.0602 |
| `trap-headcount` | 6 | 6 / 35 / 0 / 0 / 0.0053 | 6 / 35 / 0 / 0 / 30.3149 | 6 / 5 / 3 / 0 / 0.0440 |
| `trap-chain` | 9 | 9 / 30 / 1 / 0 / 0.0075 | 9 / 30 / 1 / 0 / 37.7382 | 9 / 0 / 3 / 0 / 0.0649 |
| `leave-relay` | 6 | 6 / 5 / 2 / 0 / 0.0052 | 6 / 5 / 2 / 0 / 30.0837 | 6 / 5 / 3 / 0 / 0.0405 |
| `shared-person` | 6 | 6 / 5 / 2 / 0 / 0.0050 | 6 / 5 / 2 / 0 / 27.8617 | 6 / 5 / 4 / 0 / 0.0380 |
| `impossible-predecessor` | 6 | 3 / 0 / 1 / 0 / 0.0060 | 3 / 0 / 1 / 0 / 23.9339 | 3 / 0 / 2 / 0 / 0.0351 |
| `partial-week` | 6 | 6 / 0 / 3 / 0 / 0.0060 | 6 / 0 / 3 / 0 / 30.3238 | 6 / 0 / 5 / 0 / 0.1024 |
| `four-projects` | 12 | 12 / 10 / 2 / 0 / 0.0218 | 12 / 10 / 2 / 0 / 54.4825 | 12 / 10 / 8 / 0 / 0.2147 |
| `expertise-intersection` | 7 | 7 / 5 / 3 / 0 / 0.0183 | 7 / 5 / 3 / 0 / 37.2759 | 7 / 5 / 4 / 0 / 0.1502 |
| `full-allocation` | 6 | 4 / 0 / 3 / 0 / 0.0073 | 4 / 0 / 3 / 0 / 31.2215 | 4 / 0 / 4 / 0 / 0.0394 |
| `long-pipeline` | 9 | 9 / 40 / 3 / 0 / 0.0118 | 9 / 40 / 3 / 0 / 40.6366 | 9 / 40 / 8 / 0 / 0.1941 |
| `replay-multi-capability-01` | 1 | 1 / 0 / 0 / 0 / 0.0010 | 1 / 0 / 0 / 0 / 3.8210 | 1 / 0 / 0 / 0 / 0.0137 |
| `replay-unavailability-01` | 1 | 1 / 5 / 0 / 0 / 0.0007 | 1 / 5 / 0 / 0 / 5.4351 | 1 / 5 / 0 / 0 / 0.0027 |

汇总包含两个回放案例。

| 做法 | 案例覆盖 | 配齐角色 | 总延期工作日 | 含新人方案 | 硬约束违反 | 耗时中位数（秒） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 规则 | 14/14 | 82 | 185 | 20 | 0 | 0.0060 |
| Agent + 校验器 | 14/14 | 82 | 185 | 20 | 0 | 30.1993 |
| CP-SAT | 14/14 | 82 | 85 | 50 | 0 | 0.0521 |

## D-033 接入判定

- **(a) 满足**。CP-SAT 在 4/12 个争夺案例（33.3%）严格优于两种基线中的较好结果，案例为 `trap-primary`、`trap-extra`、`trap-headcount`、`trap-chain`。Agent 完成 12/12 个争夺案例。
- **(b) 满足**。逐案比较硬约束违反数。CP-SAT 共 0 次，规则共 0 次。
- **(c) 满足**。14 个案例耗时中位数 0.0521 秒，门槛为 10 秒。

**建议接入 CP-SAT**。由 Reviewer 依据 D-033 核对并决定是否放行 B2。B1 中的 CP-SAT 仅供离线评测。

## 承诺变更回归与复跑

以 `6ec8349f` 为修复基线。保留 `trap-primary` 首个角色的人员、周容量和窗口，将其设为承诺变更，把承诺结束日从 2026-11-29 延长至 2026-12-06。原 CP-SAT 将截至 11-29 的方案列入 `filled`，校验器将这个提前结束的方案判为须经需求方确认。修复后，CP-SAT 在建立分配变量前排除这类方案。

[回归测试](../backend/tests/eval/planning/contention/test_solver.py)将该变更与同一窗口内争夺人员的角色一同求解。规则和 CP-SAT 均将变更列为未配齐，重叠角色的人员、投入比例和日期与单独求解一致，硬约束违反为 0。此用例单独作为回归测试。

在 `backend/` 运行 `uv run python -m scripts.eval_solver`，复跑原 12 个争夺案例及 2 个快照回放。规则和 CP-SAT 的逐案配齐数、延期、新人方案数和违反数均与上表一致。此次复跑沿用原报告中 14 个真实 Agent 的结果。

| 做法 | 配齐角色 | 总延期工作日 | 含新人方案 | 硬约束违反 | 耗时中位数（秒） |
| --- | ---: | ---: | ---: | ---: | ---: |
| 规则 | 82 | 185 | 20 | 0 | 0.0082 |
| CP-SAT | 82 | 85 | 50 | 0 | 0.0748 |

D-033 三项仍满足。**(a)** 分母仍是原 12 个争夺案例，CP-SAT 在相同的 4 个案例上严格优于较好基线（33.3%）。**(b)** 14 案中两种确定性求解器均为 0 次硬约束违反。**(c)** CP-SAT 中位用时 0.0748 秒，最慢单案 0.3785 秒。接入建议维持，由 Reviewer 核对并决定 B2 是否放行。
