从 `backend/` 运行 `uv run python scripts/eval_coordination.py --mode both --fake`。默认 pytest 入口只选每类第一个案例，两种模式都跑；运行器默认选全部 34 个案例。`--case` 选一个案例，`--report` 写 Markdown 报告。

真实模型需要本任务独立数据库和 Pi 开发服务；使用 `compose.chatgpt-local.yml` 只读挂载本机授权。运行器顺序执行，无并发和重试。模型暂停记为 deferred，其他模型或解析失败会原样写入报告。输出只含指标，不含对话正文、工具结果或凭据。

未取得成功 `TurnResult` 时，模型调用次数记为 `null`，报告显示「不可取得」；旧结果中失败状态的 0 同样视为缺失。均值仅使用计数已知的记录，括号显示已知/已评估数量；全组未知显示 `—`。这类缺失不能解释为零次调用或较低成本，工具调用次数和用时仍按实际记录统计。

纯 Agent 只暴露三个读取工具，最后回复须含 `assignees` JSON 代码块。Agent + 校验器暴露完整协调工具，取最后一次准备提案的方案，或最后一次模拟后在最终回复点名的方案。两种模式均使用 M7 的造数与快照；规则引擎基线只在内存计算，避免纯 Agent 读到基线方案。脚本经理的修订在回滚的保存点内执行，容量、审计和通知表必须前后完全相同。

2026-11-17 失败分类复现（原基线 `b3de3602`，不调用模型）：固定输入 `{"function":"simulate_plan","arguments":{}}` 可被 `bot.tools.CoordinationInput.model_validate()` 接受，但登记表中对应的 `input_model.model_validate({})` 抛出 `ValidationError`，错误类型 `missing`、位置 `demand_id`。将容量开关固定为开启、会话入口设为 web，用 mock Session 调用 `tools.call()`，返回含 `error` 的对象，且 `begin_nested()` 未被调用，证明尚未进入 Planning handler。此链路属于输入校验失败，不是 Planning 服务异常或 Pi/供应商异常。

原轮次日志已按验证清理要求删除，未留存参数或细分错误码；原报告的统一 `failed:model_unavailable` 不能用于回溯 32 次已有 simulate_plan 结果的根因。固定坏参数复现不代表原 32 次均为这一原因，另两次工具结果前失败也保持根因未知。有效固定参数的服务路径由 `--fake --case direct-01` 覆盖；生产参数契约和更完整的失败分类由 NERE-750 处理。本次不重跑整轮真实模型。
