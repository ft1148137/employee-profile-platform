# 贡献指南

本项目帮助企业基于可信能力和事实维护员工画像、开展跨部门协同。开始前请阅读[架构与权限](docs/architecture.md)、[开发环境](docs/development.md)和[本地检查与合并流程](docs/agent-workflow.md)。部署入口见[部署指南](docs/deploy.md)。

## 提交范围

一个 issue 对应一个 PR，标题以 issue 编号开头。先在 issue 说明问题、复现方法和预期行为；新功能、架构调整及任何依赖或锁文件变动须先讨论。沿用已有服务层、依赖和工具，不顺手加入无关重构。

修复行为或新增功能时先写能复现问题的失败测试，再实现最小改动。文档和拼写修正不需要新增行为测试。数据库改动必须附 Alembic 迁移，验证 upgrade、downgrade 和单一 head。API 客户端从 OpenAPI 生成，不手写请求类型。

所有业务数据访问都带企业上下文，权限在服务端统一判断。证据只追加，纠错用撤销或更正记录；不要修改原始证据。PR 应说明权限、隐私和兼容性影响，以及验证方法。

## 开发与本地检查

从仓库根目录安装锁定的依赖：

```bash
uv sync --all-packages --frozen
bun install --frozen-lockfile
```

配置环境、启动数据库和运行开发服务器的方法见[开发指南](docs/development.md)。同机并行任务须使用独立 Compose 项目和分配的端口，不能连接其他任务的数据库；本地 `.env` 不提交。

每次 push 前运行以下检查，后端测试只选择本次改动及引用该模块的测试：

```bash
(cd backend && uv run bash scripts/lint.sh)
(cd frontend && bunx biome check --no-errors-on-unmatched --files-ignore-unknown=true ./)
(cd frontend && bunx tsc --noEmit && bun run build)
# 配好当前任务的 DATABASE_URL 和 POSTGRES_PORT，再替换为相关测试路径：
(cd backend && uv run pytest tests/<相关路径> -x -q)
bash scripts/preflight.sh
```

`preflight.sh` 会同步远端、检查迁移、执行 pre-commit 和选中的测试；可先用 `--list` 查看计划。改了已有 Playwright 覆盖的页面时，只跑相关 spec；改了 Dockerfile 或 Compose 时，只构建受影响服务。全量后端、浏览器和安装检查交给 CI。不要通过删除、跳过测试或放宽检查让 CI 通过。

提交 PR 时列出做了什么、为什么、测试结果、已知限制；新增依赖须写许可证。代码、标识符、注释和 commit message 使用英文；issue、PR 和用户界面使用中文。PR 通过所需 CI、解决冲突并完成审查后，以 merge commit 合并；不直接推送 master，不 force push。

## 示例数据与安全问题

只提交合成示例，不提交真实员工资料、企业内部材料、聊天记录、密钥或令牌。密钥通过环境变量或项目已有的安全配置入口提供；日志和错误输出中也不能带凭据。

安全问题不要公开提 issue，也不要在普通 PR、评论或附件中放入漏洞利用细节或敏感数据。私下报告的具体联系方式：**发布时由维护者公布**。

## 贡献授权

提交贡献即表示你有权提供这些内容，并同意贡献按 **Apache-2.0** 授权（inbound = outbound），无需额外的贡献者许可协议。第三方内容须保留原有版权和许可证声明，不能把其许可直接改成项目许可。

项目许可证全文见 [LICENSE](LICENSE)，来源与第三方许可入口见 [NOTICE](NOTICE)。上游模板的 MIT 版权、许可与免责全文保留在 [模板 MIT 许可证](licenses/full-stack-fastapi-template-MIT.txt)。第三方依赖的使用和再分发义务见[许可证核验](docs/licenses.md)，贡献授权约定不改变这些第三方内容的许可。
