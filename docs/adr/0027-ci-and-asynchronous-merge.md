# ADR-0027：CI 隔离并行与异步自动合并

状态：接受（NERE-272 试行）

## 背景

Playwright 共用演示企业，单个数据库中增加 worker 会使账号和企业配置修改互相影响。agent 在运行中等 CI 也占用了大量任务时间。

## 决定

使用三个独立 runner 和数据库执行 Playwright shard，以原有 `test-playwright` 名称汇总所有 shard，保留失败、取消和路径过滤失败的门禁。没有引入应用运行时组件。

用 Docker 官方 SHA 固定的 Buildx/Bake action 导入、导出 GitHub Actions 构建缓存。性能断言在现有 backend job 内串行运行，不统计 coverage，其他测试和覆盖率门槛保持原样。

使用 GitHub 必需检查与 auto-merge，agent 在 push 后立即自审，通过 Multica 条件唤醒续办 CI 结果。strict 不启用，无人工 review 门槛，仍要求 PR。master CI 通过单次定时唤醒处理，失败报告技术规划。

## 代价与边界

三个 shard 增加 runner 和首次构建开销，但隔离不需要修改业务测试数据。缓存首次运行无命中，PR 缓存的可见性受 GitHub 分支范围约束。

Multica 关闭 issue 会禁用 wakeup，可能与 GitHub 自动合并竞争；必须实测并记录，不能声称仅注册规则就已完成验证。具体流程和替代方案见 [agent-workflow.md](../agent-workflow.md)。这份文档不覆盖 agent 指令，由 Mika 决定何时更新。
