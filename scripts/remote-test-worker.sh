#!/usr/bin/env bash
# Invoked inside the project lock and VM lease by remote-test.sh.
set -euo pipefail
umask 077

run_id=$1
export COMPOSE_PROJECT_NAME=$2
mode=$3
export PREFLIGHT_PYTEST_WORKERS=$4
shift 4
project=$COMPOSE_PROJECT_NAME
number=${project#nere}
number=${number%-review}
export POSTGRES_PORT=$((15000 + number))
export API_PORT=$((18000 + number))
if [[ $project == *-review ]]; then
  export POSTGRES_PORT=$((POSTGRES_PORT + 10000))
  export API_PORT=$((API_PORT + 10000))
fi

if [[ $mode == preflight && -d .remote-test-snapshot ]]; then
  meta=.remote-test-snapshot
  base=$(cat "$meta/base")
  if [[ ! -d .git ]]; then
    git init -q
    git remote add origin https://github.com/ft1148137/employee-profile-platform.git
    echo "/$meta/" >> .git/info/exclude
  fi
  git fetch -q origin master
  git merge-base --is-ancestor "$base" origin/master
  git read-tree "$base"
  if [[ -s $meta/committed.patch ]]; then git apply --cached "$meta/committed.patch"; fi
  tree=$(git write-tree)
  head=$base
  if [[ $tree != "$(git rev-parse "$base^{tree}")" ]]; then
    head=$(git -c user.name='Remote test snapshot' -c user.email=snapshot@example.invalid \
      commit-tree "$tree" -p "$base" -m 'Reproduce the local committed changes')
  fi
  git update-ref HEAD "$head"
  if [[ -s $meta/staged.patch ]]; then git apply --cached "$meta/staged.patch"; fi
  git update-index --skip-worktree -z --stdin < "$meta/excluded"
fi

# Scope all Compose calls to an explicit test project and synthetic settings.
export FASTAPI_ENV=development API_BIND=127.0.0.1 COMPOSE_ANSI=never COMPOSE_PROGRESS=plain
export PROJECT_NAME='Employee Profile Remote Test'
export SECRET_KEY PI_SERVICE_TOKEN POSTGRES_PASSWORD FIRST_SUPERUSER_PASSWORD DEMO_USER_PASSWORD
SECRET_KEY=$(openssl rand -hex 32)
PI_SERVICE_TOKEN=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
FIRST_SUPERUSER_PASSWORD=$(openssl rand -hex 16)
DEMO_USER_PASSWORD=$(openssl rand -hex 16)
export FIRST_SUPERUSER=admin@example.com SEED_DEMO_DATA=true
export DATABASE_URL="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:$POSTGRES_PORT/app"
export FRONTEND_HOST="http://127.0.0.1:$API_PORT"
unset SENTRY_DSN COMPOSE_PROFILES
# The override is kept outside the build context.
scratch=$(mktemp -d)
compose=(docker compose --env-file /dev/null -p "$project" -f compose.yml -f compose.dev.yml -f "$scratch/ports.yml")
started=false
artifacts=
cleanup() {
  local code=$?
  trap - EXIT
  trap '' HUP INT TERM
  if $started; then
    if ! "${compose[@]}" down -v --remove-orphans; then
      echo "Compose cleanup failed for $project." >&2
      if [[ $code == 0 ]]; then code=1; fi
    fi
  fi
  if [[ -n $artifacts ]]; then
    rm -rf -- frontend/playwright/.auth
    if [[ $code == 0 ]]; then rm -rf -- "$artifacts"; fi
  fi
  rm -rf -- "$scratch"
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

check_port() {
  local port=$1 listeners mappings
  listeners=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN) || [[ $? == 1 ]]
  mappings=$(docker ps --format '{{.Names}} {{.Ports}}')
  if [[ -n $listeners || $mappings == *":$port->"* ]]; then
    printf 'Port %s is occupied.\n%s\n' "$port" "$listeners" >&2
    printf '%s\n' "$mappings" | grep -F ":$port->" >&2 || true
    exit 1
  fi
}

check_port "$POSTGRES_PORT"
if [[ $mode == playwright ]]; then check_port "$API_PORT"; fi
cat > "$scratch/ports.yml" <<EOF
services:
  db:
    ports: !override
      - "127.0.0.1:$POSTGRES_PORT:5432"
EOF

started=true
"${compose[@]}" up -d --wait db
[[ $("${compose[@]}" port db 5432) == "127.0.0.1:$POSTGRES_PORT" ]]
case "$mode" in
  preflight) bash scripts/preflight.sh ;;
  pytest) (cd backend && uv run pytest "$@") ;;
  playwright)
    artifacts=$HOME/epp-runs/.artifacts/$project/$run_id
    mkdir -p "$artifacts"
    # Use the existing host-browser CI flow, with the backend serving its UI.
    "${compose[@]}" build backend pi
    "${compose[@]}" up -d --no-build --wait backend worker mock-jira
    (
      cd frontend
      bun install --frozen-lockfile
      export PLAYWRIGHT_BASE_URL=$FRONTEND_HOST VITE_API_URL=$FRONTEND_HOST
      export MODEL_TEST_HOST
      MODEL_TEST_HOST=${MODEL_TEST_HOST:-$(hostname -I | awk '{print $1}')}
      export PLAYWRIGHT_HTML_OUTPUT_DIR=$artifacts/report PLAYWRIGHT_HTML_OPEN=never
      bunx playwright test "$@" --workers=1 --trace=retain-on-failure --reporter=html \
        --output "$artifacts/test-results"
    )
    ;;
esac
