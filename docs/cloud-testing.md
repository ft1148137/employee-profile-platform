# 共享云端测试机

在仓库根目录运行 `scripts/remote-test.sh`，可把自检和指定用例交给共享 GCP 测试机。首次配置需要本机的 Bash、Python 3.11+、Git、rsync 和已认证的 Google Cloud CLI，以及目标实例的 IAP SSH 权限。测试机需要 Docker Compose（支持 `!override`）、uv、Bun、Playwright Chromium、ripgrep、ShellCheck，以及已部署的共享租约和空闲关机脚本。

## 本机配置

将以下模板填入 `~/.config/employee-profile-platform/cloud-runner.env` 并设置权限为 `600`。脚本会加载这个受信任的本机 Bash 文件。也可直接导出同名环境变量，环境变量优先。

```bash
GCP_PROJECT=your-gcp-project
GCP_ZONE=your-zone
GCP_INSTANCE=your-instance
MONTHLY_BUDGET_USD=40
SOFT_CAP_USD=36
DISK_FIXED_MONTHLY_USD=10
SPOT_HOURLY_RATE_USD=0.161
# USAGE_LEDGER_FILE=~/.config/employee-profile-platform/cloud-runner-usage.jsonl
```

按共享机配置填写 Spot 估算单价。省略 `SOFT_CAP_USD` 时按月预算的 90% 计算。配置文件与台账应保存在本机。

## 常用命令

```bash
export COMPOSE_PROJECT_NAME=nere123  # 替换成当前 issue 编号
scripts/remote-test.sh status
scripts/remote-test.sh preflight
scripts/remote-test.sh pytest tests/api/routes/test_utils.py --no-cov -x -q
scripts/remote-test.sh playwright tests/login.spec.ts -g 'Log in with valid email and password' --project=chromium
scripts/remote-test.sh --force preflight
bash scripts/test-remote-test.sh
```

`pytest` 的路径相对于 `backend/`，Playwright 的路径相对于 `frontend/`。自检的 pytest 默认 4 个 worker，可用 `PREFLIGHT_PYTEST_WORKERS=1` 至 `4` 调低。Playwright 复用 Compose 后端、worker 和 mock Jira，通过 VM 上的 Chromium 跑单 worker。失败时终端会显示本机临时报告目录，可在其中打开 `report/index.html` 或用 Playwright 查看 `test-results/` 里的 trace。

每个项目使用 `~/epp-runs/<COMPOSE_PROJECT_NAME>`、单独的数据库和 API 端口，以及 `epp-<COMPOSE_PROJECT_NAME>` 共享租约。第二套环境使用 `-review` 后缀。同步和执行都在项目锁内进行。遇到同项目正在运行或同步快照被另一进程替换时，等待对方完成后重试。测试结束时清理本次 Compose 容器和卷，保留镜像与构建缓存。

工作区通过 IAP 增量同步，包含已跟踪文件和 Git 未忽略的新文件，跳过依赖目录、常见凭据路径和环境文件，保留 `.env.example`。自检从公开的 `origin/master` 和过滤后的 Git 补丁重建已提交及暂存改动，再检测当前工作区。请确保 Git 未忽略的新文件也只包含可上传的代码和合成数据。测试设置在 VM 上生成，浏览器通过后端同源访问测试 UI。

## 预算和共享约定

`status` 显示实例状态、VM 上的活跃租约及本机台账估算的 UTC 当月开机时长与费用。台账按观测到的 VM 开关机区间计入共享时长，同一次开机连续跑多个测试只计一次。两次观察之间完全错过的开机区间可能缺失，费用以 GCP 账单和共享预算告警为准。

实例处于 `TERMINATED` 且估算费用达到软上限时，脚本会提示回退本机。明确要覆盖时使用命令最前面的 `--force`。已经运行的实例仍可执行测试。测试结束后只释放自己的租约，请避免手动关闭共享机。VM 会等所有项目空闲后自行关机。
