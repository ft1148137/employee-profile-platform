# ADR-0005: Password changes and shared login throttling

Status: Accepted

## Context

NERE-26 requires password changes to revoke existing sessions and database-backed
login limits across four API workers. HTTP clients must not choose their rate-limit IP.

## Decision

JWTs include integer `iat` and a `password_version` equal to the account's
`password_changed_at` ISO timestamp (or null before its first change). Authentication
checks both. The version closes the same-second revocation gap left by integer `iat`.
Tokens without `iat` require signing in again after this upgrade.

Unchanged accounts retain the mandatory-password-change gate on every protected
API except reading the current user and changing their password.

Store attempts in the platform-level `login_attempt` table. PostgreSQL transaction
advisory locks serialize each IP and identifier's check and record across workers.
Password-hash upgrades flush into the same transaction rather than committing early.
Failures preceding an account's latest successful login or password change are
excluded, including when counting IP failures. Delete records older than one day
when recording an attempt; idle deployments need no scheduled cleanup worker.

The locked fastapi-cli 0.0.32 `run` command enables proxy headers by default;
Uvicorn 0.49.0 trusts `FORWARDED_ALLOW_IPS`, defaulting to `127.0.0.1`.
Only the two HTTPS overlays set it to `*`, where backend ports are unpublished
and Traefik is the inbound proxy. HTTP uses the socket peer, ignoring external
clients' forwarded headers. See [FastAPI proxy documentation](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)
and [Uvicorn settings](https://uvicorn.dev/settings/).

## Consequences

No new dependencies or Redis. The shared form replaces the saved access token
after changing a password. Logout still clears only the local session.
Attempt cleanup happens on the next recorded login; inactive installations can
retain older rows until that next login. The proxy trust setting assumes other
containers on the Compose network are trusted.
