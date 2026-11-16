从 `backend/` 运行 `uv run python scripts/eval_coordination.py --mode both --fake`。默认 pytest 入口只选每类第一个案例，两种模式都跑；运行器默认选全部 34 个案例。`--case` 选一个案例，`--report` 写 Markdown 报告。

真实模型需要本任务独立数据库和 Pi 开发服务；使用 `compose.chatgpt-local.yml` 只读挂载本机授权。运行器顺序执行，无并发和重试。模型暂停记为 deferred，其他模型或解析失败会原样写入报告。输出只含指标，不含对话正文、工具结果或凭据。

纯 Agent 只暴露三个读取工具，最后回复须含 `assignees` JSON 代码块。Agent + 校验器暴露完整协调工具，取最后一次准备提案的方案，或最后一次模拟后在最终回复点名的方案。两种模式均使用 M7 的造数与快照；规则引擎基线只在内存计算，避免纯 Agent 读到基线方案。脚本经理的修订在回滚的保存点内执行，容量、审计和通知表必须前后完全相同。
