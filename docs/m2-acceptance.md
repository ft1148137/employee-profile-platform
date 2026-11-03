# M2 验收记录

日期：2026-11-04（北京时间）；实现基线 `master` `5d7b3c6801cdc1be03bc1d2eada506c6f4c2b521`。所有人员、任务与工作记录均为合成数据。CI 不连接真实 Jira 或真实模型；真实站点凭据只经运行时环境变量进入本地测试容器。

| 父 issue / 补充验收项 | 用例与实测结果 |
| --- | --- |
| Cloud、Data Center 已关联员工各有一个初始请求、判断可溯源 | `test_sync_draft_repeat_migrate[cloud/data_center]`，各 12 人、37 条 Jira；请求唯一、pending_owner，支持证据 active/属于本人/本人接口可读，经验数、来源数和时间一致，通过 |
| 重复同步不增加证据和计数 | 同上，Task/WorkEvent/Evidence/ChangeItem 行数不变，新增计数 0，通过 |
| 中途失败可恢复 | 同上 Cloud，页大小 10、写第 12 条时注入失败，断点 10，重试最终处理 37 条，行数与完整同步相同，通过 |
| 关闭模型可起草，模拟模型也可起草 | `test_switches_pause_resume[False/True]`，真实 LiteLLM mock_response 输出解析及来源记录通过；合成用量上限→暂停时间→队列续跑通过 |
| DC→Cloud 迁移 | `test_sync_draft_repeat_migrate[data_center]`，新账号按候选重关联，证据不增加、请求 id 不变，通过 |
| CSV 参与人、服务账号和经验合并 | `test_csv_participants_and_explicit_cross_source_reference`，PLM-0001 主讲人有证据、未关联服务账号的 PLM-0031 无证据；`对应FW-215` 与新 PLM 记录合为 1 经验、2 来源，通过 |
| 三个自动起草开关 | `test_switches_pause_resume[False/True]`，能力排除、父子部门关闭、员工退出与恢复均有效，Evidence/ProfileClaim 内容未变，通过 |
| 栖光智联规则准确率，无通过门槛 | 26 名在职、64 改动项；精确率 53.1%、召回率 47.9%，25 项 undocumented 全部未知；见 [基线](m2-baseline.md) |
| 网页完整流程 | `m2-walkthrough.spec.ts`，26 名在职员工，Jira 36 条 + PLM 32 条、向导完成、本人改密后可读草稿/证据，通过；sources/employee-evidence 截图作为 Playwright 产物 |
| 真实 Jira Cloud 完整流程 | `m2-real-jira.spec.ts`，同步 64 条（种子脚本维护其中 60 条），关联员工得到 8 改动项/17 证据引用，逐条对比站点 summary，通过；`test_seed_touch_replaces_only_one_evidence`：`--touch PLAT-101` 后 1 新证据、1 取代、其余 63 条有效证据不变，通过 |
| 真实 ChatGPT 订阅 | 只读本机登录、`chatgpt/gpt-6.1-sol`、high，5 个请求/10 个改动项、0 失败，每项有证据，提供方/模型/强度记录正确；登录文件测试前后 SHA-256 不变 |

真实 ChatGPT 本次未触发用量上限，不宣称实测过真实的 5 小时/每周暂停；暂停与自动续跑由模拟限流及已有进度页面用例验证。模型准确率未测；真实设备码授权流程未重新登录，本次只验证已登录文件的隔离调用。

## 自动检查

后端模板 mypy、ty、ruff check/format 通过；全量 pytest **604 passed、5 skipped**，coverage **98%**，`--fail-under=90` 通过；真实 Cloud 后续测试默认跳过，本地显式启用时 1 passed。前端 lint、TypeScript/build、OpenAPI 客户端重新生成及 pre-commit 全部通过。PR CI 结果在合并前另行核对，不将本地结果视为 CI 结果。

本机使用 Playwright 1.64.1 / Chromium 156.0.8078.4；模拟流程先在本机运行，再从容器复核。默认 Playwright 基础镜像下载缓慢，本地改用已有 Bun 容器挂载同一工作区、连接本机同版本 Chromium，真实凭据仍只以运行时 `-e` 传入测试容器，没有写进配置。该临时配置不入仓库，默认 Dockerfile 的构建与全套浏览器检查由 PR CI 验证。

## 复现与隔离

后端新建临时企业、进程内 `create_mock_app`，不经过外部网络；数据库仅启动隔离项目。Data Center 与多人准确率只在模拟 Jira 验证，真实 Cloud 站点仅一个账号，不能外推到多人准确率或真实 Data Center。

```bash
export COMPOSE_PROJECT_NAME=nere66
docker compose -f compose.yml -f compose.dev.yml up -d --wait db
(cd backend && uv run bash scripts/prestart.sh && uv run bash scripts/tests-start.sh && uv run coverage report --fail-under=90)
uv run --project backend python examples/test-enterprises/evaluate_drafts.py qiguang-aiot
docker compose -f compose.yml -f compose.dev.yml build backend playwright
docker compose -f compose.yml -f compose.dev.yml up -d --wait --no-build backend worker mock-jira
docker compose -f compose.yml -f compose.dev.yml run --rm playwright bunx playwright test tests/m2-walkthrough.spec.ts
```

启动前检查 5432 未被其他项目占用，否则改本项目端口并设置 DATABASE_URL；失败后不继续迁移。真实 Cloud 用例只在配置凭据时运行，使用 `--trace=off --reporter=line`，不截图/录视频；退出前扫描 test-results、blob-report 和 Compose 日志确认无令牌，再删产物。测试后 `down -v --remove-orphans`，删除项目镜像、悬空镜像，构建缓存最多保留 5 GB；不用全局卷或镜像清理。

真实站点续测：先加载本机凭据，在根目录运行 `uv run --project backend python scripts/seed_jira_cloud.py`，随后按 issue 指定的 `-e JIRA_BASE_URL -e JIRA_EMAIL -e JIRA_API_TOKEN` 运行 `m2-real-jira.spec.ts --trace=off --reporter=line`。保留同一数据库和 worker，再执行：

```bash
(cd backend && M2_JIRA_INCREMENTAL=1 uv run pytest tests/acceptance/test_m2_real_jira.py -q --tb=short)
```

该可选续测调用同一造数脚本（先恢复种子基线，再 `--touch PLAT-101`），检查原证据仅变为 superseded、新记录引用原 id，其余证据的状态和哈希不变；不删除站点记录。未启用 opt-in 时 CI 跳过，不靠放宽检查接入真实服务。

## 已知边界

同步历史列表仍读出完整 CSV 原文；账号对应页读取全部账号来算来源/数量；更换 SECRET_KEY 后查询 ChatGPT 登录可能返回 500；验证码到期/需重新登录的两种界面状态没有端到端用例。按阶段核对意见留给后续任务，本 PR 不扩大范围。正式发布前仍不创建根 README 或设置仓库简介/topics。
