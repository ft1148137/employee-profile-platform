# M4 验收记录

环境：macOS、Docker Desktop，起点 `master` `e5bbbd2`。Compose 项目 `nere132`，数据库宿主端口 15132，Web/API 18132。只用合成数据和假外部服务；模型通过真实 Pi 进程调用本地假模型，没有调用外部模型或读取本机订阅凭据。

## 验收结果

后端入口 `backend/tests/acceptance/test_m4.py` 复用原场景的完整断言和参数化，新增启光 AIoT 贯通测试；浏览器入口 `frontend/tests/m4-walkthrough.spec.ts` 使用独立新企业、真实 API 和页面，相关已有 spec 补充界面边界。

| 标准 | 结果 | 可执行依据 |
| --- | --- | --- |
| 模拟 Jira → 初始画像 → 员工更新 → 批准 → r2，每步可追溯 | 通过 | `test_qiguang_jira_to_second_version`：模拟 Jira 与 CSV、真实 Pi + 假模型；核对请求时间线、起草/提交/审批审计、版本引用和原证据不变 |
| 部分批准、退回、拒绝申诉、经理本人提交 | 通过 | 同一启光场景：r2 只发布批准项，退回关联草稿，拒绝后争议及重新提交草稿，经理本人路由管理员；`test_mixed_decisions`、`test_appeal_lifecycle`、`test_manager_request` 补充边界 |
| 增量只补证据直接发布，修改判断走请求 | 通过 | `test_incremental_evidence`、`test_incremental_judgment`；同步发布仍保留审计请求记录，但不产生待审批请求或通知 |
| 网页、MCP、IM 禁止自批 | 通过 | `test_web_self_approval`、`test_mcp_self_approval`、`test_im_confirmation_guards[self]`；网页贯通另验经理本人 POST 决定返回 403、管理员可读 |
| 规则关闭后暂定、代理、提醒、证据较旧 | 通过 | `test_policy`（四种来源）、`test_delegation`（撤销/过期和范围）、`test_reminders`、`test_stale_evidence` |
| 三平台员工提交与经理审批 | 通过（假服务） | `test_telegram_submission/review`、`test_feishu_submission/review`、`test_wecom_submission/review`：确认按钮、真实适配器、来源记录；IM 安全用例检查重复/过期/跨企业动作 |
| 员工网页提交、经理逐项审批、r1/r2 历史、手机时间线 | 通过 | `m4-walkthrough.spec.ts`，390px 员工视口；`reviews.spec.ts` 补充部分批准、退回重提及代理 |
| 中文、申诉、规则、提醒、绑定及入口设置 | 通过 | `chinese-copy.spec.ts`、`appeals.spec.ts`、`approval-policy.spec.ts`、`update-reminders.spec.ts`、`im-bindings.spec.ts`、`im-settings.spec.ts` |

本地结果：后端全量 1223 passed、3 skipped（既有可选真实模型测试），覆盖率 97%；M4 集中验收 44 passed；Pi 40 passed；Chromium 上述 8 个 spec 连同登录准备共 21 passed。后端 mypy/ty/ruff、前端 lint/完整 TypeScript/生产 build 通过，生成客户端无差异。Alembic 唯一 head 为 `b129001bc001`。网页由本机 Python 服务承载并随测试结束关闭；Docker 仅运行本任务 PostgreSQL。浏览器镜像下载过慢已取消，未完成整套 Compose 镜像构建。

## 启光 AIoT 演练

使用 `examples/test-enterprises/qiguang-aiot` 的员工、能力词典、模拟 Jira 和 CSV，QG019 为提交人，QG002 获测试部门经理授权。账号仅在隔离测试库临时创建，结束后连同企业数据清理。假模型只按可用证据引用生成建议，没有读取画像真值。

初始请求为待本人过目，员工确认后由经理批准得到 r1。员工再提交三项，批准一项、退回一项、拒绝一项；只发布批准项得到 r2，r1 快照不变。随后实测完整退回、拒绝后申诉并允许重提，以及经理本人提交交管理员。原有 Evidence 逐条对比保持不变。

## 复现与验证

先按任务端口规范检查占用，复制 `.env.example` 到忽略的 `.env`，把 `DATABASE_URL` 的宿主端口改为 15132。仓库外 `../nere132.ports.yml`：

```yaml
services:
  db:
    ports: !override ["15132:5432"]
```

```bash
set -e
export COMPOSE_PROJECT_NAME=nere132 API_PORT=18132
docker compose -f compose.yml -f compose.dev.yml -f ../nere132.ports.yml up -d --wait db
docker compose -f compose.yml -f compose.dev.yml -f ../nere132.ports.yml port db 5432
# 必须显示 15132，之后才执行 backend 下的命令
cd backend
uv sync
uv run bash scripts/lint.sh
uv run pytest tests/acceptance/test_m4.py
PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run bash scripts/tests-start.sh
uv run coverage report --fail-under=90
uv run alembic heads
```

前端构建时显式设置 `VITE_API_URL=http://127.0.0.1:18132`，避免沿用开发默认地址。执行 `bash scripts/generate-client.sh`、`bun run lint`、`bun run --filter frontend build` 和 `cd frontend && bunx tsc --noEmit`。Pi 执行 `npm ci && npm test`。浏览器指向同任务 18132 服务，运行上表的 8 个 spec，使用 `--workers=1` 避免演示企业设置用例互相覆盖。截图见[经理指南](manager-guide.md)和[员工指南](employee-guide.md)。

验证后停止服务，对 `nere132` 执行 `down -v --remove-orphans`，只删除本项目镜像，清理悬空镜像和超过 5 GB 的构建缓存。最终本地结果、清理后空间及 PR/CI 记录在 NERE-132 结果评论。

## 边界

不含真实飞书、企业微信、Telegram 账号，真实平台的权限、可用范围、通讯录授权、卡片布局及长度仍需部署方验证；飞书、企业微信超出保守预算时引导网页。IM 消息外发、Telegram 境外存储和网络要求见[部署指南](deploy.md#m4im-服务与网络)。未新增依赖、迁移或生产权限逻辑，未删除或放宽已有测试和检查，未恢复根目录 README。
