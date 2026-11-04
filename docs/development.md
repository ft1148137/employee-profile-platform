# FastAPI Project - Development

Before starting, run `cp .env.example .env` from the repository root and replace the example secrets. Run all Compose commands from the repository root.

## Local Development

For local development, run PostgreSQL with Docker Compose, and run the FastAPI and Vite development servers locally.

Start the supporting services:

```bash
docker compose -f compose.yml -f compose.dev.yml up -d db
```

Then, from the `backend` directory, install the dependencies and prepare the database:

```bash
uv sync
uv run bash scripts/prestart.sh
```

Start the FastAPI development server:

```bash
uv run fastapi dev
```

In another terminal, from the project root, install the frontend dependencies and start the Vite development server:

```bash
bun install
bun run dev
```

Now you can open these URLs:

Frontend development server: <http://localhost:5173>

Backend API: <http://localhost:8000>

Automatic interactive API documentation with Swagger UI: <http://localhost:8000/docs>

The frontend development server uses the backend at `http://localhost:8000`, as configured in `frontend/.env`.

### Frontend Served by FastAPI

Build the frontend from the `frontend` directory:

```bash
bun run build
```

The build is written to `backend/app/frontend` and served by FastAPI at <http://localhost:8000>. Rebuild the frontend after making frontend changes.

## 后端测试

只需要 PostgreSQL 的 `db` 服务在跑。以下命令在 `backend/` 中执行，先在仓库根目录准备 `.env` 并启动 `db`，再运行 `uv sync`。数据库账号需要建库权限。测试不需要先跑 prestart：每个进程从空的迁移模板库复制自己的数据库，再初始化账号和演示数据，不会改动本地 `app` 库。数据库名包含 worker 名和随机后缀；单进程使用 `main`。会话结束自动强制删除测试库和模板库。xdist 主控进程不导入应用、不连接数据库。

改代码时只跑相关文件，单进程通常更快；也可以只重跑上次失败的测试：

```bash
uv run pytest tests/api/routes/test_profiles.py -x
uv run pytest --lf
```

push 之前和 CI 一样，全量运行（包括迁移测试），默认 `-n auto` 并行，pytest-cov 自动合并各 worker 的覆盖率。`--dist loadgroup` 将标为 `xdist_group("model")` 的模型测试放在同一 worker：

```bash
uv run bash scripts/tests-start.sh
uv run coverage report --fail-under=90
```

几个任务同时在本机跑时，可以限制为 4 个 worker：

```bash
PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run bash scripts/tests-start.sh
# 或直接指定 worker 数
uv run pytest tests/ -n 4 --dist loadgroup --cov --durations=25
```

单独运行迁移测试或临时跳过它们（CI 每次仍跑全量）：

```bash
uv run pytest -m migration
uv run pytest -m "not migration" -n auto
```

需要按测试查看覆盖情况时再开启上下文，默认不记录以减少开销。Python 3.14 默认使用的 sysmon 核心对动态上下文支持不完整，这次运行临时选择 ctrace：

```bash
COVERAGE_CORE=ctrace uv run pytest --cov --cov-context=test
uv run coverage html --show-contexts
```

新测试中，只读演示数据使用会话级 `shared_demo`（结束时检查未被修改），写数据使用函数级 `demo`。迁移测试使用 `migration_engine` 和 `tests.utils.migrations.migrate(engine, "upgrade" / "downgrade", revision)`，不要启动 alembic 子进程；使用该 fixture 会自动加上 `migration` 标记。应用 fixture 放在 `tests/fixtures.py`，由 `tests/conftest.py` 配置独立库后加载，避免导入时绑定到共享库。

## Full Stack with Docker Compose

To run the backend and built frontend in Docker Compose:

```bash
docker compose -f compose.yml -f compose.dev.yml watch
```

Now you can open these URLs:

Application, with the frontend and API served by FastAPI: <http://localhost:8000>

Automatic interactive API documentation with Swagger UI: <http://localhost:8000/docs>

Adminer, database web administration: <http://localhost:8080>

Stop a locally running FastAPI server before starting the Compose backend because both use port `8000`.

**Note**: The first time you start the stack, it might take a minute for all the services to be ready. To monitor it, use `docker compose -f compose.yml -f compose.dev.yml logs`, or `docker compose -f compose.yml -f compose.dev.yml logs backend` for the backend service.

## Docker Compose Files and Environment Variables

`compose.yml` is the default local startup: PostgreSQL, one-shot migrations and initial account setup, Web/API, and Worker. Run `docker compose up -d --wait` and open <http://localhost:8000>. Only the API port is published, on `127.0.0.1:8000`; optionally set `API_BIND` / `API_PORT` in `.env`.

`compose.dev.yml` adds hot reload, watch, Adminer, Playwright, and host access to PostgreSQL on 5432. It must be selected explicitly. You can set `export COMPOSE_FILE=compose.yml:compose.dev.yml` in your own shell to shorten development commands; do not save it in `.env`. There is no automatically loaded override file.

`deploy/compose.traefik.yml` adds Traefik and HTTPS for a domain deployment; see [deploy/README.md](../deploy/README.md). Never combine it with development services on a public server.

Compose passes only explicitly listed settings to application containers. `FASTAPI_ENV` is passed through from `.env` when present and omitted otherwise. Development mode permits example secrets and exposes development-only endpoints, so remove it and replace all example secrets before public deployment.

### 本机 ChatGPT 订阅测试

使用 fake-ip 代理时需要 `PI_EGRESS_ALLOW_FAKE_IP=1`；本机订阅覆盖文件已仅为 Pi 设置它。它只放行代理使用的 `198.18.0.0/15`，其他内网和元数据地址仍被拒绝。带此开关且 `FASTAPI_ENV` 不是 `development` 时，Pi 拒绝启动；不要把它加入生产配置。

仅开发模式可叠加 `compose.chatgpt-local.yml`，例如本任务使用：

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) COMPOSE_PROJECT_NAME=nere104 API_PORT=18104 docker compose \
  -f compose.yml -f compose.chatgpt-local.yml up -d --wait
```

`.env` 必须设置 `FASTAPI_ENV=development`。Pi 使用宿主用户的 UID/GID，以读取原本权限为 0600 的授权文件；专用 tmpfs 也归该用户。该覆盖文件只把 `${HOME}/.codex/auth.json` 单个文件只读挂给 Pi，不挂载 HOME，不接触 `~/.claude`，后端和 Worker 没有授权文件。开发设置页选择 ChatGPT 订阅并启用、保存即可测试，不需要企业登录；本机模式会用于该部署内全部企业的 ChatGPT 调用，因此只用于合成数据。移除此覆盖文件并重建容器即可恢复企业连接。

Pi 每轮只提取 access token、账号和 JWT 到期时间，临时文件在独立 tmpfs 中，权限 0600，不含 refresh token，调用结束后删除；不写回、不刷新、不存企业数据库。有效期不足一小时会中文提示：请在本机正常运行一次 codex 刷新，然后重新测试。开发者自己在本机正常登录；自动化测试只创建合成 JWT，不能读取真实授权文件。

若同时使用 `compose.dev.yml`，先按任务分配数据库端口并使用仓库外覆盖文件，禁止连接其他任务的数据库。验证后对同一组 Compose 文件执行 `down -v --remove-orphans` 并清理本任务镜像。

After changing variables, make sure you restart the stack:

```bash
docker compose -f compose.yml -f compose.dev.yml watch
```

## The `.env` File

The ignored local `.env` file contains local development defaults, passwords, and other configuration. Its hostnames use `localhost` for processes running on your machine. Docker Compose overrides hostnames such as the database with their Compose service names.

Do not commit secrets. Configure deployment environment variables as described in the [Docker Compose deployment guide](./deployment-docker-compose.md).

## Pre-commit Hooks and Code Linting

The project uses [prek](https://prek.j178.dev/), a modern alternative to [pre-commit](https://pre-commit.com/), for code linting and formatting.

You can find a file `.pre-commit-config.yaml` with configurations at the root of the project.

### Install `prek` to Run Automatically

`prek` is already part of the dependencies of the project.

From the project root, install the Git hook so that `prek` runs automatically before each commit:

```bash
uv run prek install -f
```

The `-f` flag forces the installation, in case there was already a `pre-commit` hook previously installed.

Now whenever you try to commit, for example with:

```bash
git commit
```

`prek` will check and format the code you are about to commit. If it modifies any files, add those files to Git again before committing.

### Run `prek` Manually

You can also run `prek` manually on all files from the project root:

```bash
uv run prek run --all-files
```

## Worker

Run the queue locally after the database migrations:

```bash
cd backend
uv run procrastinate --app=app.worker.app.app worker
```

In another terminal in `backend/`, enqueue a side-effect-free task and inspect queue configuration:

```bash
uv run procrastinate --app=app.worker.app.app defer system.ping
uv run procrastinate --app=app.worker.app.app healthchecks
```

The same commands can run through `docker compose exec worker` in the default stack. Healthchecks verify configuration, database connectivity and queue table availability; they do not detect a stalled worker heartbeat. See [ADR-0002](adr/0002-worker-and-compose-layout.md) for migrations and future tenant-task requirements.
