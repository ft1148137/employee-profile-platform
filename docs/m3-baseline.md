# M3 模型起草基线

2026-11-04（UTC），合成企业栖光智联（`qiguang-aiot`），词典 `aiot-hardware`。初次起点 `master` `5788963`，开发代理修复基于 `74debba9`，Pi SDK 0.73.1。模型始终为 **`chatgpt/gpt-5.4`，推理强度 `high`**，本机 ChatGPT 订阅只读开发授权模式。

真实模型评测**未完成**。初次显示「模型服务暂时不可用」的根因已由技术规划确认：本机代理把 `chatgpt.com` 解析成 `198.18.0.36`，触发目录提供方的私网地址拦截，不是已证实的上游故障。本次增加仅开发模式有效的 fake-ip 开关，保留其他网段限制；确认 Pi 运行在 development 且开关为 1 后，用相同数据、模型和推理强度重新评测。

放行后仍观测到 Pi 协议 **HTTP 200，流内错误码 `model_error`**。200 是平台到 Pi 的协议状态，不能当作模型上游成功状态；本轮没有记录令牌或上游响应正文，不推测新的根因。失败不能用于计算准确率，没有刷新或写回本机授权，也没有将凭据复制到后端或仓库。

受控复测：放行后的首次完整评测仍未完成，最终 `ModelUnavailable`；随后只重试一次，首个 Pi 错误仍为 `model_error` / HTTP 200，立即结束，没有继续循环。两次都在退出时清理临时企业和任务；数据库核对残留企业与起草任务均为 0。按 NERE-101 技术规划的兜底约定保留无有效测量状态，后续出网诊断需另行决定。

| 指标 | M2 规则（历史） | M3 真实模型 |
| --- | --- | --- |
| 精确率 | 34/64（53.1%） | 无有效测量 |
| 召回率 | 34/71（47.9%） | 无有效测量 |
| 没有记录的能力保持未知 | 25/25（100%） | 无有效测量 |
| misleading 被起草 | 5/7（71.4%） | 无有效测量 |

M2 数字来自[历史基线](m2-baseline.md)。分母为 26 名在职员工的 71 个能力对，其中 documented 22、partial 17、undocumented 25、misleading 7。只评能力有无，不评等级；misleading 另列风险，不设通过门槛。失败不能解释为零召回或全部保持未知。假模型测试仅证明流程与溯源，不能填入准确率表。

## 复测

先按[M3 验收](m3-acceptance.md)启动 `nere101` 的隔离数据库并迁移。保留 `.env` 的 development 模式。以下在仓库根目录执行，只把授权文件只读挂给 Pi；真实模型评测与 E2E 分开运行。

```bash
set -e
export COMPOSE_PROJECT_NAME=nere101 API_PORT=18101
export LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)
docker compose -f compose.yml -f compose.dev.yml -f compose.chatgpt-local.yml -f ../nere101.ports.yml up -d --wait --no-build pi backend
docker compose -f compose.yml -f compose.dev.yml -f compose.chatgpt-local.yml -f ../nere101.ports.yml port db 5432
docker compose -f compose.yml -f compose.dev.yml -f compose.chatgpt-local.yml -f ../nere101.ports.yml run --rm --no-deps \
  -v "$PWD/backend/tests:/app/backend/tests:ro" \
  -v "$PWD/examples/test-enterprises/evaluate_drafts.py:/app/examples/test-enterprises/evaluate_drafts.py:ro" \
  backend python /app/examples/test-enterprises/evaluate_drafts.py qiguang-aiot \
  --kind chatgpt_subscription --model chatgpt/gpt-5.4 --reasoning-effort high
```

Pi 仅提取 access token、账号和到期时间，临时文件不含 refresh token，用后删除；有效期不足一小时会拒绝调用。其他模型可在 `backend/` 运行 `PYTHONPATH=. uv run python ../examples/test-enterprises/evaluate_drafts.py qiguang-aiot --provider openai --model 模型名 --reasoning-effort high`，密钥从 `MODEL_API_KEY` 环境变量读取，不能写在命令行或结果文档。

评测仅向模型提供词典及工作记录，不发送 `personas.yaml` 的答案；按在职员工 / 能力 key 和保留答案离线比对。成功时脚本输出精确率、召回率、四类证据命中和未知比例。额度不足或任一员工失败时拒绝评分，退出清理本企业任务、临时企业和账号，其他企业的任务保留。复测成功后应追加完整输出及日期，不覆盖这次失败记录。

## 2026-11-05 追加：模型方式基线完成

来源：NERE-4「M3 总结」的「模型方式准确率基线（栖光智联）」。NERE-113 合并后，技术规划使用 **`chatgpt/gpt-6.1-sol`、推理强度 `high`、Pi 0.73.1**，以本机订阅只读授权完成一次评测，未刷新、未写回授权。本节只补记已有结果，不代表 Pi 1.5.0 的重新评测；此前「未完成」及 `gpt-5.4` 两次 `model_error` 的记录保留。

| 指标 | M2 规则方式（历史） | M3 模型方式 |
| --- | --- | --- |
| 精确率 | 34/64（53.1%） | 35/54（64.8%） |
| 召回率 | 34/71（47.9%） | 35/71（49.3%） |
| 没有记录的能力保持未知 | 25/25（100%） | 25/25（100%） |
| misleading 被起草 | 5/7（71.4%） | 6/7（85.7%） |

按证据状态的起草命中分布：

| 证据状态 | 起草命中 / 能力对 |
| --- | --- |
| documented | 19/22 |
| partial | 10/17 |
| undocumented | 0/25 |
| misleading | 6/7 |

被起草的 misleading 能力对：`QG002/pcb_layout`、`QG003/emc_debug`、`QG005/pcb_layout`、`QG007/firmware_integration`、`QG011/rknn_conversion`、`QG020/ecn_bom_management`。没有工作记录的能力均保持未知；misleading 起草比例偏高，结果仍是待本人过目的请求，须按审批规则处理，不能直接当作正式判断。沿用 M3 约定，不设准确率门槛。

当时对 `gpt-5.4` 失败原因的账号可用性判断只是推断，旧错误码不能证明原因。
