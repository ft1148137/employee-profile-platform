# ADR-0039：后端测试按耗时分三片

状态：采纳（NERE-371）

## 背景

后端单个 CI job 耗时约 7–10 分钟，接近原来的 10 分钟超时。NERE-328 同时调整多个 workflow 未改善普通 PR 整轮耗时，本次只拆后端测试。

## 决定

新增 MIT 许可的开发依赖 pytest-split 0.11.0，用 `least_duration` 和已提交的历史耗时将全套功能测试分为三片。每片使用独立 runner、数据库和原有 xdist loadgroup，超时 15 分钟；第 1 片额外串行运行原有 roster 性能测试。JUnit 和耗时与覆盖率一起保存，支持核对测试数量及刷新耗时基线。

保留必需检查名 `test-backend`。汇总 job 使用 `always()`，明确拒绝失败、取消、跳过和空结果，并要求三份覆盖率数据齐全，合并后执行原有 `coverage report --fail-under=90`。不改变其他 workflow、分支保护、测试断言或检查门槛。

本地 preflight 默认只用两个 pytest worker，沿用 loadgroup，可由 `PREFLIGHT_PYTEST_WORKERS` 调整；不改变测试选择。

## 影响

增加 runner 与依赖安装总量，换取更短的关键路径；额外的汇总调度和覆盖率合并仍计入实测。历史耗时不能预测 runner 波动，测试大幅变更时需合并三个分片上传的耗时文件并更新基线。新增用例仍全部执行，按平均耗时估算。
