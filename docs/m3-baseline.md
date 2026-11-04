# M3 模型起草基线

2026-11-04，合成企业栖光智联（`qiguang-aiot`），词典 `aiot-hardware`。起点 `master` `5788963`，Pi SDK 0.73.1。尝试模型 **`chatgpt/gpt-5.4`，推理强度 `high`**，本机 ChatGPT 订阅只读开发授权模式。

本轮真实模型评测**未完成**：按 26 名在职员工运行期间持续出现进度错误「连不上模型服务：模型服务暂时不可用」，脚本最终抛出 `ModelUnavailable: 模型起草未完成，请检查配置或额度后重试`。Pi 健康检查正常。平台屏蔽上游错误详情，本轮不能确定是上游连接、账号权限还是模型可用性问题。没有刷新或写回本机授权，也没有将凭据复制到后端或仓库。失败后的临时企业和续跑任务已清理。

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
