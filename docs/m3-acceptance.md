# M3 验收记录

环境：macOS、Docker Desktop，起点 `master` `5788963`；合成企业数据，无真实 Jira 或员工资料。数据库宿主端口 15101、后端 18101、Compose 项目 `nere101`。模型基线另见 [M3 基线](m3-baseline.md)。

## 验收映射

`backend/tests/acceptance/test_m3.py` 集中收集既有边界场景，并新增同一员工依次经五次提交的 SDK/API 集成场景，避免复制领域测试逻辑。

| 验收项 | 可执行证据 |
| --- | --- |
| 名单缺邮箱、邮箱登录、Jira 邮箱自动对应 | `test_roster_email`、`test_email_login`、`test_page_links_backfills_and_respects_unlink` |
| 无模型不排队、Pi 候选可溯源、命令与文件工具不可用 | `test_model_required`、`test_injected_commands_and_invalid_reference_never_publish`（真实 Pi + 假模型传输） |
| 到期仅同步一次 | `test_concurrent_dispatch_and_missed_periods`（两个并发调度者、错过周期） |
| 文件上传和网页编辑词典不改变旧判断 | `test_dictionary_versions` 的 POST / PUT 两种参数 |
| 同一员工的 MCP 个人令牌 / OAuth、JSON、表单、机器人 | `test_one_employee_all_channels`：SDK 读取本人画像和初始请求，补充依据，五次待审批、撤回；正式判断不变，五份 restricted 自述；机器人不能凭模型话术或工具直接提交 |
| 冒用和跨企业拒绝 | `test_boundaries_and_safe_errors`、`test_business_and_format_errors`、`test_other_employee_and_tenant_requests`、`test_no_model_and_http_scope` |
| 基线限流退出清理续跑任务 | `test_baseline_quota_exit_cleans_only_its_jobs`，保留其他企业任务 |
| 新企业向导、必填模型、手机表单和机器人 | `frontend/tests/m3-walkthrough.spec.ts`，真实后端和 Pi，模型为本地合成传输；`bot.spec.ts` 补充桌面/手机预览修订与确认边界 |

## 复现

本地后端全量：891 passed、3 skipped（既有可选真实模型用例），覆盖率 98%；集中验收 27 项通过；Pi Node 测试 32 项通过。后端 mypy、ty、ruff，前端 lint、完整 TypeScript 检查与生产构建通过。批量导入旧测试的可空 `items` 类型访问同步修正，保留原断言。

Chromium 的 M3 完整流程通过（390px 员工视口），修正了初次测试的无工作记录员工选择、名单邮箱与其他用例冲突、账号对应页加载等待。机器人和中文文案回归使用同一隔离服务。假模型现在能在没有待过目请求时创建新请求，并在退出时关闭连接。

从仓库根目录复制 `.env.example` 为 `.env`。按[开发文档](development.md#后端测试)安装依赖；先确认 15101、18101 未被其他任务占用。不要改用默认端口，也不要连接别的任务数据库。

仓库外 `../nere101.ports.yml` 内容：

```yaml
services:
  db:
    ports: !override ["15101:5432"]
  playwright:
    ports: !reset []
```

```bash
set -e
export COMPOSE_PROJECT_NAME=nere101 API_PORT=18101
docker compose -f compose.yml -f compose.dev.yml -f ../nere101.ports.yml up -d --wait db
docker compose -f compose.yml -f compose.dev.yml -f ../nere101.ports.yml port db 5432
export DATABASE_URL=postgresql://postgres:changethis@localhost:15101/app POSTGRES_PORT=15101
npm ci --prefix pi --ignore-scripts --no-audit --no-fund
cd backend
uv sync
uv run pytest tests/acceptance/test_m3.py
PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run bash scripts/tests-start.sh
uv run coverage report --fail-under=90
uv run bash scripts/lint.sh
uv run alembic heads
cd ../frontend
bun install --frozen-lockfile
bun run lint
bunx tsc --noEmit
bun run build
cd ..
docker compose -f compose.yml -f compose.dev.yml -f ../nere101.ports.yml build backend pi playwright
docker compose -f compose.yml -f compose.dev.yml -f ../nere101.ports.yml up -d --wait --no-build backend worker mock-jira
docker compose -f compose.yml -f compose.dev.yml -f ../nere101.ports.yml run --rm --no-deps playwright bunx playwright test m3-walkthrough.spec.ts bot.spec.ts chinese-copy.spec.ts --workers=1
```

数据库示例密码仅对应本地 `.env.example`；若已修改，在环境变量中使用自己的值。真实模型基线与假模型 E2E 分开执行，避免占用同一 Pi 槽。验证结束对同组配置执行 `down -v --remove-orphans`，仅清理本任务镜像、悬空镜像和构建缓存（最多保留 5 GB），删除仓库外覆盖文件。

批准与发布、外部 IM 入口在 M4；本次只核对提交到 `pending_review` 和撤回。外部 ChatGPT / Claude 产品界面没有实连；OAuth 用 MCP SDK 与本地授权流程验证。
