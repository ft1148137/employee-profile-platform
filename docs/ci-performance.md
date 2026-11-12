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


## NERE-328：路径调度与准备复用（11-11）

测量仍使用 Actions job/step 时间戳。整轮为所有工作流最早 job 开始至最后 job 完成，含中间调度等待；分片 job、测试步骤、前置准备分别列出，不用汇总 gate 耗时代替全流程。样本是实际 pull_request 事件，不是仅在本地模拟匹配。测量 PR 的 base 使用实现分支，以保证差异只含目标路径；测量后关闭，不合并合成标记。

文档样本 #182：优化前完整门禁 521 秒，优化后 36 秒（约减少 93%）。优化后 backend-shard、playwright-prepare、playwright-shard、compose-tests、production-tests 全部明确 skipped；八个必需检查都 success，没有 pending。文档变更优化前已跳过 Playwright，因此不将这项记为新增收益。

- [文档优化前 backend（521 秒）](https://github.com/ft1148137/employee-profile-platform/actions/runs/42986064642)
- [文档优化后 backend（汇总成功、分片跳过）](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987432525)
- [文档优化后 Compose / 生产安装](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987432499)
- [文档优化后 Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987432594)

后端拆分前收集 1754 个用例：1745 passed、3 个已有 skip、另 6 个串行 roster 用例。最终收集为 1756：三片 584+583+583，另 6 个串行；比原来增加两个准备失败/取消门禁用例，没有减少测试或新增 skip。分片集合没有交集，完整覆盖非性能集合。原来的九种门禁状态场景全部保留并适配前置准备依赖，新增两个场景；另有 192 组状态组合和 24 组实际 picomatch 路径场景验证共享脚本与规则。

`--durations=25` 的基线最慢项：飞书 SDK 回调/进程清理 4.41 秒，M2 Data Center 3.27 秒、Cloud 3.00 秒，模型基线 2.71 秒，密码重置 2.51 秒，roster 迁移 2.35 秒。已有迁移模板按 worker 复用、每用例克隆独立库，继续保留；业务 fixture 的创建与清理承载隔离，不把它们跨用例共享。首轮三片测试步骤为 100.56 / 131.19 / 149.03 秒，但当轮契约测试失败，不能冒充最终通过样本。

一次 M2 快照失败的原始 diff 经解析核对：61 条证据和 1 条判断的全部字段相同，仅查询返回顺序改变。查询增加 `ORDER BY id`，完整内容比较和所有断言保留；独立数据库本地回归及工作流契约共 14 个用例通过。


Pi 样本 #183：优化前 340 秒，优化后 272 秒，约减少 20%。优化后三个 backend shard 和 Compose smoke 正常执行，Playwright 准备与分片、生产安装明确跳过；所有必需门禁成功。此次变更的后台/普通代码样本仍沿用原来的三处 skip，没有扩大跳过范围。

- [Pi 优化前 backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/42986071710)、[安装](https://github.com/ft1148137/employee-profile-platform/actions/runs/42986071615)
- [Pi 优化后 backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987792113)、[Compose / 安装门禁](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987792011)、[Playwright 跳过与门禁](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987792024)

实现 PR #184 在包含安装 workflow 变化时实际执行了生产安装、幂等、备份恢复和升级全部场景，production-tests 用时 208 秒并成功；普通代码测量 #185 则明确跳过生产安装。不能仅凭规则单测声称安装触发已验证。

[后端通过样本](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987725422)的三片测试步骤为 151 / 109 / 102 秒，roster 六场景额外串行执行 8 秒。三份 JUnit 合计 1750 个非性能用例（1747 passed、原有 3 skipped），没有重复；加串行六项后总计 1756。合并覆盖率为 13018 行、355 未覆盖、97%，与拆分前相同，`--fail-under=90` 成功。151 秒约为 2.5 分钟；这不是包括 runner 准备、artifact 与汇总门禁的整轮时间。

文档过滤仅跳过已知文档路径；`templates/profile-import/SKILL.md` 由 API 作为运行时协议资源发布，因此继续跑重测试。路径回归也覆盖这个边界。


| 实际 PR 样本 | 优化前整轮 | 优化后整轮 | 变化 |
| --- | ---: | ---: | ---: |
| 普通代码：#181 → #185 | 402 秒 | 480 秒 | 增加 78 秒（约 19%） |
| 仅文档：#182 两轮 | 521 秒 | 36 秒 | 减少 485 秒（约 93%） |
| 仅 Pi：#183 两轮 | 340 秒 | 272 秒 | 减少 68 秒（20%） |

普通代码样本是 `backend/app/main.py` 中的合成注释，PR 相对实现分支仅变更这一个代码路径。优化前 #181 是真实前端代码 PR；两者跑同一套浏览器套件，源码内容不同，因此这是一轮运行对比，不是严格控制 runner 与所有变量的基准。原有普通 PR 六个工作流均成功，测量 PR 也全部成功。

| Playwright 项 | 优化前 #181 | 优化后 #185 |
| --- | ---: | ---: |
| 前置准备 job | 无 | 109 秒 |
| shard 1 / 2 / 3 job | 389 / 301 / 307 秒 | 293 / 238 / 251 秒 |
| shard 1 / 2 / 3 节省 | — | 96 / 63 / 56 秒 |
| shard 1 / 2 / 3 测试本身 | 见 Actions 时间戳 | 176 / 133 / 159 秒 |
| 客户端生成 | 每片重复 | 前置执行一次，11 秒 |
| 镜像构建 | 每片重复 | 前置执行一次，46 秒 |
| 导出镜像与客户端 | 无共享 artifact | 前置执行一次，5 秒 |
| 加载共享镜像与客户端 | 无 | 25 / 27 / 21 秒 |

[普通代码优化后 Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987806724)、[backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987806844)、[Compose / 安装门禁](https://github.com/ft1148137/employee-profile-platform/actions/runs/42987806719)。优化前来自上文 #181 对应的 [Playwright](https://github.com/ft1148137/employee-profile-platform/actions/runs/42984039204)、[backend](https://github.com/ft1148137/employee-profile-platform/actions/runs/42984039241)。

此普通代码样本**整轮未提速**。各片确实省约一分钟，但新增的 109 秒前置依赖抵消了并行分片准备节省；该样本关键路径的中间调度间隔约 65 秒，基线约 5 秒。另一个全部通过的实现 PR #184 为 426 秒，也不能用它声称普通代码整轮下降。并行测量、runner 波动与新增依赖阶段都会影响端到端时间，后续需要以 master 热缓存和正常单 PR 负载继续评估；不通过减少测试或放宽门槛制造收益。本 PR 保留大古决定的共享准备方案，把整轮瓶颈作为自审 Should fix 留给后续规划。
