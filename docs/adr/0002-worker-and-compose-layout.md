# ADR-0002：PostgreSQL Worker 与 Compose 分工

- 状态：接受
- 日期：2026-11-02
- 对应：NERE-9 / M0-S3

## 背景与决策

沿用模板的 Python 3.14、psycopg 3.3、PostgreSQL 18、FastAPI 静态前端托管方式。使用 `procrastinate~=3.10.0`，锁文件固定 3.10.0；不引入 Redis，不自行实现队列。API 和 Worker 使用同一个后端镜像，由 backend 服务负责唯一一次构建，prestart / worker 复用该镜像，避免多个构建并发写入同一个镜像标签。镜像名含 Compose 项目名，避免本机任务互相覆盖。

`Settings.DATABASE_URL` 的 SQLAlchemy 驱动前缀 `postgresql+psycopg://` 在 Worker 入口转换回 libpq 的 `postgresql://`。任务通过 `import_paths` 注册，当前只有无租户数据、无副作用的 `system.ping`。

## 验证与限制

- macOS arm64：Python 3.14.3、psycopg 3.3.6、Procrastinate 3.10.0、PostgreSQL 18。实际 defer `system.ping`，运行 `run_worker_async(wait=False)`，数据库状态为 `succeeded`，`healthchecks` 三项全部 OK。
- Linux arm64 容器使用模板的 `python:3.14`，当前解析到 Python 3.14.10；其余版本保持相同。默认启动 db / backend / worker 均 healthy、prestart 以 0 退出；CLI defer 后查到 `system.ping = succeeded`，`healthchecks` 三项全部 OK。
- 初次构建发现旧共享 uv 缓存中 psycopg-binary / mypy wheel 元数据损坏。使用项目专属、`sharing=locked` 的 BuildKit 缓存后依赖安装成功；没有降级 Python 或替换依赖。
- 原始 schema SQL 内的 PL/pgSQL `%` 会被 psycopg 参数解析误读。Alembic 用 `exec_driver_sql(..., execution_options={"no_parameters": True})` 执行固定文件，保留原始 SQL，不做字符串拼接或业务 SQL 插值。
- CLI `healthchecks` 检查配置、连接、队列表存在性，不检查 Worker 心跳。Compose 进程退出会使容器退出，但进程卡住需要后续监控识别；本期不增加监控系统。

## Schema 与升级

初始 revision `6c2d9e0f4a81` 接在已合并 S2 的 `84c2e7d91a60` 后，读取同目录的 `procrastinate_3_10_0.sql`。该文件是锁定版本 `uv run procrastinate schema --read` 的完整输出；同时保留上游 MIT 声明。队列表、函数和类型位于默认 public schema。回滚脚本只删除该快照定义的对象，保留业务表与共享的 plpgsql 扩展；降级会丢失队列及执行历史，执行前停 Worker 并备份。

以后升级时：

1. 阅读目标版本的发行说明和 [官方迁移说明](https://procrastinate.readthedocs.io/en/stable/howto/production/migrations.html)，核对所需 pre / post SQL；`uv run procrastinate schema --migrations-path` 输出包内迁移目录。
2. 将这次所需 SQL 固定进仓库，新增 Alembic revision 并串在当前唯一 head 后；记录来源版本、顺序和回滚方式。不要重写已应用的初始 SQL 或直接对现有库运行 `schema --apply`。
3. 按上游要求，在升级代码前应用 pre 迁移，升级依赖和 Worker 后再应用 post 迁移。需要分两次发布时，使用两个 revision 和明确的部署步骤，不把所有阶段盲目塞进一次 prestart。
4. 在隔离数据库验证现有数据、upgrade / downgrade（不可逆变更必须明确记录）、入队执行和健康检查；确认 `uv run alembic heads` 只有一个 head。

S2 已先合并，本 revision 已串接其后，保持唯一 head。

## 租户任务约定

未来所有业务任务的第一个参数必须是 `org_id`；Worker 执行前校验组织与任务数据属于同一租户，再以该租户的 `system_worker` Principal 执行。每次查询继续显式限制租户，不能信任任务载荷中的员工 ID。Principal 在 S4 实现，本期不添加占位权限实现。只有不访问任何租户数据的系统探针 `system.ping` 例外。

## Compose 文件职责

| 文件 | 用途 |
| --- | --- |
| `compose.yml` | 默认本地启动，db / prestart / backend / worker；生产形态启动命令，默认仅绑定 127.0.0.1:8000，数据库不发布端口 |
| `compose.dev.yml` | 显式选择：fastapi dev、watch、Adminer、Playwright、数据库 5432 |
| `deploy/compose.traefik.yml` | 可选域名 HTTPS：Traefik、证书卷、重启策略；取消 API 宿主机端口，保留模板域名路由模式 |

默认 `docker compose up -d --wait` 自动构建镜像，等待数据库 healthy、prestart 成功，再启动 API 与 Worker。迁移不在每个应用进程里重复执行。API 健康检查执行 `SELECT 1`，连接或查询失败返回不泄露连接详情的 503。

Compose 不使用 `env_file`，仅将显式列出的变量传给容器。`FASTAPI_ENV` 通过空值映射从 shell / `.env` 透传；没有设置时省略，避免把空字符串传给 Literal 字段。保留 Settings 的默认密钥检查。示例的 development 只用于本地体验，公开部署必须换密钥并删除该变量。

## 新增依赖与许可证

直接依赖 Procrastinate 3.10.0 为 MIT。新增间接依赖为 asgiref（BSD-3-Clause）、croniter（MIT）、python-dateutil（Apache-2.0 / BSD-3-Clause 双许可）、six（MIT）、psycopg-pool（LGPL-3.0-only）。attrs 已在原锁文件中。保留包内许可证，不修改这些库；psycopg-pool 作为独立 Python 模块动态导入，遵守其 LGPL 条件，与既有 psycopg 的许可处理一致。开源发布前仍按项目要求逐项核验分发义务；本期不更改模板 MIT 版权声明。
