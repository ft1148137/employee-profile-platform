# FastAPI Project - Docker Compose Deployment

You can deploy the project to your own remote server with Docker Compose. The deployment configuration includes Traefik to handle HTTPS and route incoming traffic to the application.

Run the commands below from the repository root. Copy `.env.example` to `.env` for local development; configure deployment secrets through environment variables.

## Preparation

* Have a remote server ready and available.
* Configure DNS records pointing to the server for the application domain and any supporting service subdomains you want to expose, such as `fastapi-project.example.com` and `adminer.fastapi-project.example.com`.
* Install and configure [Docker](https://docs.docker.com/engine/install/) on the remote server (Docker Engine, not Docker Desktop).

## Copy the Code

```bash
rsync -av --exclude=".git/" --filter=":- .gitignore" ./ root@your-server.example.com:/root/code/app/
```

The `--filter=":- .gitignore"` option tells `rsync` to use the same ignore rules as Git, excluding files such as the Python virtual environment.

## Configure the Application

### Environment Variables

Set the application domain, project name, and first superuser email:

```bash
export DOMAIN=fastapi-project.example.com
export PROJECT_NAME="Full Stack FastAPI Project"
export FIRST_SUPERUSER=admin@example.com
```

You can also configure these environment variables as needed:

* `SMTP_HOST`: The SMTP server host from your email provider.
* `SMTP_USER`: The SMTP server user.
* `EMAILS_FROM_EMAIL`: The email account used to send emails.
* `SENTRY_DSN`: The DSN for Sentry.

### Secrets

Generate and set secure values for the database password, token signing key, and first superuser password:

```bash
export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FIRST_SUPERUSER_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

To use an authenticated email provider, also set `SMTP_PASSWORD`.

## Deploy

```bash
cd /root/code/app/
docker compose -f compose.yml -f compose.deploy.yml build
docker compose -f compose.yml -f compose.deploy.yml run --rm backend bash scripts/prestart.sh
docker compose -f compose.yml -f compose.deploy.yml up -d
```

The `compose.deploy.yml` file adds HTTPS and automatic certificate handling to the shared `compose.yml` configuration. Explicitly listing both files excludes the local settings from `compose.override.yml`.

The backend Docker image builds the frontend, so the server does not need Bun or prebuilt frontend files.

## URLs

Replace `fastapi-project.example.com` with your domain.

Application (frontend and API): `https://fastapi-project.example.com`

Interactive API docs: `https://fastapi-project.example.com/docs`

Adminer: `https://adminer.fastapi-project.example.com`
