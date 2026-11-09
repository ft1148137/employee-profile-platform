# Agent 本地检查与异步合并

NERE-272 试行此流程；Mika 更新「全栈开发」指令前，其他正在进行的 M4.1 子 issue 继续按原流程交付。本文件不修改任何 agent 的配置或指令。

## 本地检查

每个 PR 必跑后端 ruff、mypy、ty，前端 biome、类型检查、build，以及改动涉及的 pytest 文件或目录。沿用仓库的 uv、Bun 和现有脚本：

```bash
uv sync --all-packages --frozen
bun ci
(cd backend && uv run bash scripts/lint.sh)
(cd frontend && bunx biome check --no-errors-on-unmatched --files-ignore-unknown=true ./)
(cd frontend && bunx tsc --noEmit && bun run build)
# DATABASE_URL 和 POSTGRES_PORT 必须使用当前 issue 的独立端口。
(cd backend && uv run pytest tests/<相关路径> -x -q)
```

排查上次失败可加 `--lf`；提交前仍需覆盖受影响的用例。默认不在本地重复全量 pytest、Playwright、Compose 和生产安装测试，这些由必需 CI 检查执行。

- 迁移、权限和租户范围、证据协议、凭据发生变化：本地跑全量 pytest；迁移还需验证 upgrade、downgrade 和单一 head。
- 修改界面流程且需要本地排查：只跑相关 Playwright 文件或用例。
- 使用 Docker 时仍遵守项目名、端口、磁盘和清理规则；不能连接其他任务的数据库。

## Push、自审与自动合并

1. 本地检查通过后 push，创建以子 issue 编号开头的 PR。PR 描述列出验证、假设、依赖及许可证；CI 改动单独列出。
2. 立即用 `ponytail-review` 审查 `gh pr diff <PR>` 的完整改动和关联代码，不等 CI。Must fix 全部修好、验证并 push 后才继续。高风险改动仍逐项检查迁移、证据只追加、跨租户/越权测试、凭据保密和已有检查完整性。Should fix / Nice to have 记入结果评论。
3. 用 `"$MULTICA_CLI_PATH" issue pull-requests <子issue> --output json` 确认 PR 已关联。记录当前 head SHA，并在子 issue 发结果评论：实现、PR 链接、本地验证、自审 Verdict、修复轮数、假设和遗留事项，明确写「已开启自动合并，CI 通过后由 GitHub 合并」。这条评论先发布，再执行开启命令；若命令失败，立即补充纠正。
4. 只对当前子 issue 自己创建的 PR 执行：

   ```bash
   gh pr view <PR> --json headRefOid,mergeable,mergeStateStatus
   gh pr merge <PR> --auto --merge --match-head-commit <SHA>
   gh pr view <PR> --json autoMergeRequest,headRefOid,state
   ```

   不用 `--admin`，不直接 push master，不 force push。`--match-head-commit` 保证开启时的 head 没被意外替换；之后每次自己 push 都需重新自审并核实新的 head 与 auto-merge 状态。保护不要求 strict，所以不为单纯落后 master 而重复合并；有冲突时仍 fetch、merge master、验证、普通 push。
5. 用已核实的条件参数注册下一次运行，然后结束当前运行，不执行 `gh pr checks --watch`：

   ```bash
   "$MULTICA_CLI_PATH" issue wakeup create <子issue> \
     --until-pr checks --expires-in 2h --on-timeout wake \
     --parent <结果评论ID> \
     --instruction "检查当前 PR 的 CI：失败则修复，成功则确认 GitHub 自动合并及 issue 状态；回复合并 SHA。"
   "$MULTICA_CLI_PATH" issue wakeup list <子issue> --output json
   ```

   条件只观察本 issue 直接关联的 PR；缺失链接或 GitHub snapshot 会记入 `last_error` 后重试。它只等待注册之后完成的新结果，不能拿它等待已经完成的检查；注册前若发现检查已完成，当前运行直接处理结果。多个 PR 时须核对实际触发的 PR 和 head，不能把旧 PR 的成功当作新 PR 的结果。
6. 唤醒后读取 issue、评论、规则和当前 PR 的所有 checks，记录触发时间与结果：
   - 有失败：读取 `gh run view <run-id> --log-failed`，按原规则最多修 3 轮；不删除、跳过或放宽检查。普通 push 后核实 auto-merge 仍有效，必要时对新 head 重新开启，然后注册新的 checks wakeup。与本次无关且本地通过的偶发失败只重跑一次。
   - 尚有运行中的检查：对新完成结果重新注册条件后退出。超时唤醒则检查是否卡住，说明原因，必要时 blocked 并提及技术规划。
   - 全部成功或 skipped：确认 `state=MERGED`，回复 `mergeCommit.oid`；若 GitHub 尚未完成合并，注册 `--until-pr merged` 后退出，不能手动绕过自动合并。
   - 有冲突：merge 最新 master，重新检查、自审并 push，再等新结果。
   - 已合并：确认 issue 已 done；还有本 issue 验收事项（例如本次提速测量）时先保留 in_progress，完成后再 done。

### 条件唤醒的边界

`--until-pr checks` 依赖平台定期检查，GitHub 自动合并和平台将 issue 关闭可能发生在它之前。平台关闭 issue 会禁用 wakeup，因此必须记录实际的 `wakeup runs`，不能把「已注册」写成「已唤醒」。发现这个竞争时在 issue 写明事实；在修正平台前，可以暂缓开启自动合并，先注册 checks 条件，唤醒后开启自动合并并读取合并结果（该替代流程不满足 NERE-272 的“先开启再唤醒”验证，须明确注明）。不能靠反复计时轮询 PR 取代条件，也不能声称原验证已完成。

## 合并后 master CI

在 checks 唤醒中用合并 SHA 查询 `gh run list --branch master --commit <merge-SHA> --json databaseId,status,conclusion,url`。已结束且失败的运行，在结果评论下提及技术规划并附失败链接；不自动回滚、不放宽门禁。

master CI 尚未结束时，先记录一次状态，并为当前 issue 创建一次定时 wakeup（例如 `--kind at --after 10m --instruction "检查合并 SHA ... 的 master CI；失败提及技术规划"`），随后退出，不在 agent 中等待。此时需让 issue 保持 in_progress，因为 done 会禁用规则；醒来检查一次，若仍运行或尚未出现，说明运行状态并安排下一次有界检查。完成后确认 done。PR 条件观察的是 PR head 的检查，不能用它冒充 master 合并提交的检查。pre-commit 目前只在 PR 上运行，master 查询只要求实际配置的 push workflow。

## CI 门禁与并行边界

master 必需检查名：`changes`、`test-playwright`、`test-backend`、`test-docker-compose`、`test-production-install`、`test-pi`、`pre-commit`、`Run zizmor`。所有 workflow 都在每个 PR 上触发，无顶层 paths 过滤；Playwright 保留 job 级路径过滤，汇总 job 在无关改动时也返回成功，在过滤失败或任一 shard 失败时失败。

Playwright 分 3 个独立 runner，每个有自己的 Compose 网络、数据库和报告；每个 shard 仍用 1 worker。检查发现 admin-account-actions、admin-roles、admin-accounts、employees、approval-policy、drafting-settings 等使用共用演示企业，不能让同一数据库中的多个 worker 并发修改。已有 setup 和 admin-account-actions 的 serial 分组保留，不新增 serial 标记，不改变重试或 flaky 检查。

Docker 使用 SHA 固定的 Docker 官方 setup-buildx / bake action，分别为各 workflow 的 backend、pi、playwright 镜像配置 `type=gha` 导入与导出缓存。Bake 加载本地镜像，安装脚本仍执行原来的 Compose 构建和验证，并复用当前 Buildx builder。首次 PR 缓存可能为空；合并后 master 的 push 运行建立后续 PR 可读取的缓存。生产安装仍现场生成真实测试配置，缓存预热只使用合成的 Compose 解析占位值。

roster 性能函数的 6 个参数场景（包括两个 5000 行 XLSX）从带 coverage 的 xdist 阶段分离，在同一必需 backend job 中用 `-n 0 --no-cov` 运行。2 秒断言保持不变，功能覆盖率仍必须达到原来的 90%。

分支保护要求 PR 和全部门禁，不要求人工 review；禁止 force push 和删除，保留 GitHub 默认的管理员 bypass，但 agent 不使用。strict 关闭，避免并行 PR 每合并一次就使其他分支重跑；合并后的 master CI 兜底。
