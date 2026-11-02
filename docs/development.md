# FastAPI Project - Development

Before starting, run `cp .env.example .env` from the repository root and replace the example secrets. Run all Compose commands from the repository root.

## Local Development

For local development, run PostgreSQL and Mailpit with Docker Compose, and run the FastAPI and Vite development servers locally.

Start the supporting services:

```bash
docker compose -f compose.yml -f compose.dev.yml up -d db mailpit
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

Mailpit: <http://localhost:8025>

The frontend development server uses the backend at `http://localhost:8000`, as configured in `frontend/.env`.

### Frontend Served by FastAPI

Build the frontend from the `frontend` directory:

```bash
bun run build
```

The build is written to `backend/app/frontend` and served by FastAPI at <http://localhost:8000>. Rebuild the frontend after making frontend changes.

## Full Stack with Docker Compose

To run the backend and built frontend in Docker Compose:

```bash
docker compose -f compose.yml -f compose.dev.yml watch
```

Now you can open these URLs:

Application, with the frontend and API served by FastAPI: <http://localhost:8000>

Automatic interactive API documentation with Swagger UI: <http://localhost:8000/docs>

Adminer, database web administration: <http://localhost:8080>

Mailpit: <http://localhost:8025>

Stop a locally running FastAPI server before starting the Compose backend because both use port `8000`.

**Note**: The first time you start the stack, it might take a minute for all the services to be ready. To monitor it, use `docker compose -f compose.yml -f compose.dev.yml logs`, or `docker compose -f compose.yml -f compose.dev.yml logs backend` for the backend service.

## Mailpit

[Mailpit](https://mailpit.axllent.org) captures emails sent during local development instead of delivering them. The local backend connects to it at `localhost:1025`, and the Compose backend connects to the `mailpit` service. Captured emails are available at <http://localhost:8025>.

## Docker Compose Files and Environment Variables

`compose.yml` is the default local startup: PostgreSQL, one-shot migrations and initial account setup, Web/API, and Worker. Run `docker compose up -d --wait` and open <http://localhost:8000>. Only the API port is published, on `127.0.0.1:8000`; optionally set `API_BIND` / `API_PORT` in `.env`.

`compose.dev.yml` adds hot reload, watch, Mailpit, Adminer, Playwright, and host access to PostgreSQL on 5432. It must be selected explicitly. You can set `export COMPOSE_FILE=compose.yml:compose.dev.yml` in your own shell to shorten development commands; do not save it in `.env`. There is no automatically loaded override file.

`deploy/compose.traefik.yml` adds Traefik and HTTPS for a domain deployment; see [deploy/README.md](../deploy/README.md). Never combine it with development services on a public server.

Compose passes only explicitly listed settings to application containers. `FASTAPI_ENV` is passed through from `.env` when present and omitted otherwise. Development mode permits example secrets and exposes development-only endpoints, so remove it and replace all example secrets before public deployment.

After changing variables, make sure you restart the stack:

```bash
docker compose -f compose.yml -f compose.dev.yml watch
```

## The `.env` File

The ignored local `.env` file contains local development defaults, passwords, and other configuration. Its hostnames use `localhost` for processes running on your machine. Docker Compose overrides hostnames such as the database and SMTP server with their Compose service names.

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
