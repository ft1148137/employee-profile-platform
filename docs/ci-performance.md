# CI 提速测量（NERE-277）

数据取自 GitHub Actions API 的 job 和 step 时间戳。单位为秒；整轮沿用 NERE-272 口径，取最早 job 开始至最后 job 结束，包含 job 之间排队，不包含工作流首次拿到 runner 前的排队。Playwright 包含测试分片开始至汇总结束，不能用 gate 自身的 3–4 秒代替。

| job | #132 基线 | #136 首次缓存 | #138 热缓存 | #140 热缓存 |
| --- | ---: | ---: | ---: | ---: |
| changes | 7 | 7 | 5 | 7 |
| test-backend | 244 | 383 | 401 | 340 |
| test-playwright | 468 | 421 | 350 | 344 |
| test-docker-compose | 116 | 206 | 170 | 144 |
| test-production-install | 249 | 312 | 232 | 231 |
| test-pi | 31 | 48 | 40 | 34 |
| pre-commit | 44 | 46 | 40 | 53 |
| Run zizmor | 9 | 7 | 8 | 10 |
| **整轮实际耗时** | **477** | **430** | **401** | **414** |

两轮热缓存使用 M4.1 已有成功 PR 的最终 head，没有额外触发测量 PR。PR #140 的整轮包含 runner 调度等待，因此会比最长单 job 的 340 秒多出 74 秒。

## 主要步骤

| job / 步骤 | #138 | #140 |
| --- | ---: | ---: |
| backend 启动数据库 | 12 | 17 |
| backend Migrate DB（含首次 uv 依赖安装） | 11 | 10 |
| backend Run tests | 342 | 279 |
| backend 串行 roster 性能测试 | 9 | 7 |
| Playwright shard 1 / 2 / 3 job | 330 / 345 / 339 | 323 / 338 / 301 |
| Playwright shard 1 / 2 / 3 构建 | 103 / 111 / 113 | 118 / 90 / 87 |
| Playwright shard 1 / 2 / 3 Run Playwright tests（含服务启动与再次构建） | 165 / 167 / 139 | 140 / 192 / 159 |
| Playwright shard 1 / 2 / 3 Buildx post | 11 / 18 / 38 | 17 / 11 / 12 |
| Compose Bake | 83 | 68 |
| Compose up | 38 | 35 |
| Compose down | 12 | 11 |
| 生产安装预热 | 61 | 55 |
| 生产安装 | 39 | 39 |
| 生产幂等、备份和恢复验证 | 46 | 49 |
| 生产升级两次 | 56 | 60 |
| 生产停止清理 | 11 | 12 |
| Pi build | 21 | 18 |
| pre-commit 执行 | 27 | 38 |

## 缓存与 backend 诊断

两轮 shard 2 日志均有 backend、pi、playwright 的 GHA manifest 导入以及大量 `CACHED`。#138 manifest 导入约 0.2 / 0.1 / 0.1 秒；pi / playwright / backend 缓存导出约 0.6 / 0.3 / 1.4 秒。#140 导出准备约 1.1 / 0 / 4.0 秒。上述任务并行，不将它们直接求和冒充关键路径。

#138 Playwright OCI 导出约 37 秒、backend 约 15 秒；#140 约 39 / 26 秒，远大于缓存索引读写。热命中仍需恢复层和载入 Docker。测试步骤里又出现第二轮本地构建、OCI 导出和加载，完整层命中也无法免除这些开销。

backend 两轮都是 `4 workers` 和 `loadgroup`，测试执行分别 335.67 / 273.71 秒；差异并非 worker 数量变化。最慢的飞书用例为 18.13 / 14.36 秒。M2 Data Center 为 5.33 / 4.99 秒、Cloud 为 4.64 / 4.02 秒。不同普通用例与 fixture 也整体变慢，不能将所有波动归因于单个测试。

model 组包含 140 个用例，独占一个 worker 约 210 / 171 秒，但之后该 worker 会继续处理其他用例。各 worker 最终完成差约十秒，本次保留现有分组。进一步压缩整个 backend 需要处理重复的企业 fixture 和数据库往返，或提供更多独立 CPU / runner；这比删除等待或改一个分组更大，需要维持数据隔离与全部验收场景。

本地对照中，飞书 SDK 冷导入在 coverage 下为 15.86 秒，字节码变热后约 4 秒；预编译 14,411 个文件花 1.51 秒。这是本机诊断，不能代替本 PR 的 CI 实测。SDK 回调和真实 spawn 子进程仍执行，原有 30 秒等待仅维持假监听进程存活，父进程会主动终止它，并非需要实际等待 30 秒。

M2 两个用例保留 37 个 Task、73 个 WorkEvent、12 人请求、Cloud 分页中断重试、重复同步幂等、Data Center→Cloud 迁移和逐项证据可见性断言。只让模拟 Jira 跨请求复用 TestClient 的 ASGI portal；没有减少数据和断言。

## 来源与后续

- [PR #132](https://github.com/ft1148137/employee-profile-platform/pull/132)、[PR #136](https://github.com/ft1148137/employee-profile-platform/pull/136)。
- [PR #138 backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/42634643660)、[Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/42634643773)、[Compose / 安装](https://github.com/ft1148137/employee-profile-platform/actions/runs/42634643706)。
- [PR #140 backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/42642759723)、[Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/42642759802)、[Compose / 安装](https://github.com/ft1148137/employee-profile-platform/actions/runs/42642759774)。
- [Playwright 官方 CI 安装步骤](https://playwright.dev/docs/ci-intro)、[pytest-xdist 分组语义](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)。

NERE-277 结果评论补充本 PR 各 job、主要步骤、整轮耗时和剩余瓶颈。覆盖率仍要求 90%，XLSX 性能仍要求 2 秒；重试、flaky 门禁、三片汇总和生产安装验证保持原样。
