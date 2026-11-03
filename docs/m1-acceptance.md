# M1 验收记录（NERE-34）

2026-11-03，本机 macOS 26.4.1、Docker Desktop。使用全新 Git 克隆 `nere34-accept`，项目名始终 `nere34`；升级与自签验证按顺序替换同一项目，没有运行两套验收栈。所有企业、员工和密码均为合成测试数据。截图见[管理员指南](admin-guide.md)。

## 1. HTTP 生产安装

```bash
COMPOSE_PROJECT_NAME=nere34 EPP_ADDRESS=192.168.10.215 EPP_HTTP_PORT=18034 ./install.sh --non-interactive
```

安装成功，backend、worker、db 健康。检查 `.env` 无 `changethis`；用 `psql` 检查 `organization` 和 `user` 均为 0 行，没有演示数据。在 backup 容器尝试 curl 时发现镜像未装 curl，改为在 backend 容器中用 Python `urllib.request`，禁用代理并设置 `Accept: text/html`，访问宿主机局域网 `http://192.168.10.215:18034/login` 返回 200。

这验证的是 Docker Desktop 中另一个容器经局域网 IP 访问，不是真实的另一台物理电脑。Ubuntu 的安装、初始化 API、重跑幂等与备份由现有 `test-production-install` CI job 检查，最终结果以 PR 检查为准。

## 2. 只用浏览器完成企业初始化

本机 Google Chrome 156.0.8078.80（Playwright `channel="chrome"`，不是内置 Chromium）打开安装器打印的链接，建立「星衍科技（模拟）」及独立管理员账号。经 `/setup-wizard` 上传 `examples/roster/sample-30.csv`，预览新增 30 人、15 个层级部门及上级关系，确认导入；选择科技研发词典并启用；生成并下载账号 Excel；完成向导。管理员操作未触碰服务器文件、未直接调用业务 API。

用 openpyxl 打开下载文件：31 行（含表头），30 个不重复工号，6 列与账号模板一致。初始化、名单预览、词典和账号步骤截图见管理员指南。

按大古最新要求改用 Chrome，使用包含延迟 60 秒撤销下载地址改动的生产构建，重新跑完整向导、账号文件下载、首次改密、重置密码和限流流程，全部通过。Chrome 安装版本在本次运行中由 156.0.8078.74 更新为 156.0.8078.80；账号下载与 HTTP 复制的正式复核版本为后者。**Safari 没有实测**。

## 3. 首次登录、重置密码与限流

从下载的账号文件取 XY002，在独立浏览器会话登录，跳到 `/change-password`；填写当前密码、新密码和确认密码，保存后进入本人画像，出现「你的画像还没有内容」。截图见管理员指南。

管理员在 `/admin/accounts` 重置 XY002：员工原页面刷新后回到登录页；新密码登录再次要求改密。HTTP 局域网地址的 Chrome 中点「复制」显示「已复制」，使用了非安全上下文回退；截图对密码做了遮挡。

再通过网页连续输入错误密码 5 次，均返回 400，第 6 次返回 429 并显示中文限流提示。管理员网页重置后，员工用新密码立即返回 200 并再次进入改密页。

Chrome HTTP 专项使用当前局域网 IP `10.193.51.3`，复核 `window.isSecureContext=false`、`navigator.clipboard` 不存在，点击复制后通过 `execCommand` 回退显示「已复制」。Safari 没有实测。模拟企业共用 Docker Desktop 来源 IP 时建议设置 `LOGIN_MAX_FAILURES_PER_IP=1000`，每账号阈值保留 5 次。

## 4. 备份、恢复、升级

在 30 名员工导入和账号生成后执行 `COMPOSE_PROJECT_NAME=nere34 ./backup.sh`，得到非空的 `app-20261103-092810.dump`（实际文件名以脚本输出为准）。随后用浏览器上传增加 XY031 的全量名单，SQL 核对员工数为 31。

```bash
COMPOSE_PROJECT_NAME=nere34 ./restore.sh <上一步备份路径>
# 输入：确认恢复
```

恢复成功，服务重新健康，员工数回到 30。恢复脚本重建数据库容器时移除了临时测试端口，导致并行 pytest 连接中断；重新发布独立测试端口并重建测试数据库后重跑，产品数据库未用于 pytest。

升级首次在 Git fetch 遇到 HTTP/2 连接错误，未生成备份、未执行迁移；以单次调用的 `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1` 重试同一 `./upgrade.sh origin/master`，不修改仓库配置。重试通过：从第 2 阶段 `3a95184` 升级到 `c321577`，Alembic 从 `c20cd19c259b` 升到 `e35a91c7d024`。升级前自动备份生成，所有服务健康；企业 UUID、名称和 `upgrade-admin` 账号均保留。此项只通过初始化 API 准备旧版本数据，主向导验收仍完全使用浏览器。

## 5. 中文文案与自动检查

人工查看登录、导航、向导四步、账号操作与空状态，未见英文模板提示；Excel、CSV、工号及员工自填内容属于数据或技术名词。已有 `chinese-copy` 检查用于自动扫描，本地 3 worker 执行 `setup.spec.ts`、`admin-account-actions.spec.ts`、`chinese-copy.spec.ts` 共 14 项，通过。前端首次运行使用了本地开发 `.env` 的固定 API 地址而未连到测试后端，改为 `VITE_API_URL=''` 同源构建并设置测试端 API 地址后重跑通过。

10 份 `roster.csv` 由 YAML 生成，共 260 名在职人员；生成时逐份调用正式解析器，校验器比较文件与源数据一致性。执行两条命令均成功，数据集校验报告 10 家、276 人（包含 16 名已离职人员）、0 错误。

```bash
uv run --project backend python examples/test-enterprises/export_rosters.py
uv run --project backend python examples/test-enterprises/validate.py
```

## 6. HTTPS 与范围

自签安装命令：`COMPOSE_PROJECT_NAME=nere34 EPP_ACCESS=https-selfsigned EPP_ADDRESS=192.168.10.215 EPP_HTTP_PORT=18034 ./install.sh --non-interactive`。安装与健康检查通过；Chrome 156.0.8078.74 接受证书后能打开登录页。验证中本机网络地址变为 `10.193.51.3`，旧地址连接超时；用当前局域网地址复测 HTTPS 返回 200，HTTP 返回 301，跳到对应的 HTTPS 登录页。也确认 localhost HTTPS 返回 200。Let's Encrypt 公网签发未实测；没有真实域名和公网验证环境，不宣称签发通过。Safari、真实企业网络和生产数据恢复没有实测，不在通过结果中。

## 本地检查

- 后端：`uv run bash scripts/lint.sh`，mypy、ty、ruff 检查和格式全部通过；`uv run bash scripts/test.sh`，230 项通过，覆盖率 97%。测试使用独立数据库 `nere34_tests`，不接入验收业务库。
- 前端：`bun run lint`、`VITE_API_URL='' bun run --cwd frontend build`（含 TypeScript 检查）通过。使用本地测试后端和已构建同源前端运行上述 14 项 Playwright，用 3 个 worker 验证账号操作文件内串行设置。
- `uv run prek run --files <本 PR 文件>` 通过；未添加依赖、未放宽或跳过检查。

最终 Chrome 复核：`python3 nere34-chrome-check.py`（临时浏览器验收脚本）驱动本机 Chrome 156.0.8078.80，30 人账号文件打开成功、HTTP 非安全上下文复制成功，强制改密、旧会话下线和限流重置均通过。

## 清理

四套临时目录按顺序复用 `nere34`，每次验证后执行本项目 `docker compose down -v --remove-orphans`，删除带 `com.docker.compose.project=nere34` 标签的构建镜像，再执行 `docker image prune -f` 与 `docker builder prune -f --reserved-space 5gb`。最终删除四个验证克隆；本项目容器和数据卷查询均为空。safaridriver 的本次 PID 8730 已停止。清理后的剩余磁盘以 issue 最终结果评论中的 `df -h ~` 为准。
