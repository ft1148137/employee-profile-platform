#!/usr/bin/env bash
# Exercise selection and failure paths without a database, Docker or remote fetch.
set -euo pipefail
source_script=$(cd "$(dirname "$0")" && pwd)/preflight.sh
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/repo" "$scratch/bin"
cd "$scratch/repo"
git init -q -b main
git config user.name 'Preflight Test'
git config user.email 'preflight@example.com'
git config commit.gpgsign false
git remote add origin .
git config remote.origin.fetch '+refs/heads/main:refs/remotes/origin/master'
mkdir -p scripts backend/app/domains/sample backend/app/alembic/versions backend/tests/{sample,domains,admin_assistant,api/routes} frontend/{src/routes,tests} .github
cp "$source_script" scripts/preflight.sh
touch backend/tests/sample/test_feature.py backend/tests/domains/test_audit_labels.py backend/tests/test_ci_workflows.py backend/tests/test_sample_migration.py backend/tests/admin_assistant/test_change_set.py
echo 'from app.domains.sample import feature' > backend/tests/domains/test_importer.py
echo 'before' > backend/app/domains/sample/feature.py
echo 'before' > backend/app/domains/sample/deleted.py
echo 'import app.domains.sample.deleted' > backend/tests/domains/test_deleted.py
echo 'old' > backend/app/alembic/versions/base.py
git add .
git commit -qm base
git update-ref refs/remotes/origin/master HEAD
git checkout -qb feature

cat > "$scratch/bin/uv" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$PREFLIGHT_CALLS"
case "$*" in
  'run alembic heads')
    echo 'one (head)'
    if [[ ${PREFLIGHT_TWO_HEADS:-0} == 1 ]]; then echo 'two (head)'; fi ;;
  'run prek run'*) exit "${PREFLIGHT_PREK_EXIT:-0}" ;;
  'run python -') python3 - ;;
  'run pytest --no-cov -x -q '*) exit "${PREFLIGHT_PYTEST_EXIT:-0}" ;;
  *) echo 'Unexpected uv command' >&2; exit 99 ;;
esac
SH
chmod +x "$scratch/bin/uv"
export PATH="$scratch/bin:$PATH"
export PREFLIGHT_CALLS="$scratch/calls"
export DATABASE_URL='postgresql://synthetic:synthetic@localhost:15351/test'
export POSTGRES_PORT=15351
: > "$PREFLIGHT_CALLS"

echo 'after' > backend/app/domains/sample/feature.py
git rm -q backend/app/domains/sample/deleted.py
echo 'audit(action="sample.created")' > backend/app/domains/sample/new.py
echo 'changed' > .github/check.yml
echo 'new' > backend/app/alembic/versions/new.py
mkdir -p backend/scripts
echo 'changed' > backend/scripts/eval_admin_assistant.py
echo 'export const page = createFileRoute("/settings")({})' > frontend/src/routes/preferences.tsx
echo 'page.goto("/settings")' > frontend/tests/settings.spec.ts
mkdir -p pi
echo 'changed' > pi/index.ts
bash scripts/preflight.sh --list > "$scratch/list"
[[ ! -s $PREFLIGHT_CALLS ]]
for target in tests/sample/ tests/domains/test_importer.py tests/domains/test_deleted.py tests/domains/test_audit_labels.py tests/test_ci_workflows.py tests/test_sample_migration.py tests/admin_assistant/ frontend/tests/settings.spec.ts; do
  rg -q -F -x "$target" "$scratch/list"
done
rg -q '^pi$' "$scratch/list"
bash scripts/preflight.sh > "$scratch/run"
rg -q 'pytest: 通过' "$scratch/run"
rg -q 'run prek run --files' "$PREFLIGHT_CALLS"
rg -q 'run pytest --no-cov -x -q -n 2 --dist loadgroup' "$PREFLIGHT_CALLS"
: > "$PREFLIGHT_CALLS"
PREFLIGHT_PYTEST_WORKERS=1 bash scripts/preflight.sh > "$scratch/run"
rg -q 'run pytest --no-cov -x -q -n 1 --dist loadgroup' "$PREFLIGHT_CALLS"

expect_failure() {
  local expected=$1
  shift
  : > "$PREFLIGHT_CALLS"
  if "$@" > "$scratch/failure" 2>&1; then
    echo "Expected failure: $expected" >&2; exit 1
  fi
  rg -q -F "$expected" "$scratch/failure"
}
assert_absent() {
  if rg -q "$1" "$2"; then echo "Unexpected match: $1" >&2; exit 1; fi
}
expect_failure '单一head: 失败' env PREFLIGHT_TWO_HEADS=1 bash scripts/preflight.sh
assert_absent 'run prek|run pytest' "$PREFLIGHT_CALLS"
expect_failure 'pre-commit: 失败' env PREFLIGHT_PREK_EXIT=1 bash scripts/preflight.sh
assert_absent 'run pytest' "$PREFLIGHT_CALLS"
expect_failure 'pytest: 失败' env PREFLIGHT_PYTEST_EXIT=1 bash scripts/preflight.sh
for bad_url in '' 'postgresql://user:secret@localhost/db' 'postgresql://user:secret@localhost:5432/db' 'postgresql://user:secret@localhost:15352/db' 'postgresql://user:secret@localhost:invalid/db'; do
  expect_failure '数据库环境: 失败' env DATABASE_URL="$bad_url" bash scripts/preflight.sh
  assert_absent 'run pytest' "$PREFLIGHT_CALLS"
  assert_absent 'secret' "$scratch/failure"
done
expect_failure '数据库环境: 失败' env -u POSTGRES_PORT bash scripts/preflight.sh
expect_failure '数据库环境: 失败' env POSTGRES_PORT=5432 DATABASE_URL='postgresql://localhost:5432/db' bash scripts/preflight.sh

# Added migrations on both sides must stop before Alembic or pytest runs.
git add .
git commit -qm feature
git checkout -q main
echo 'upstream' > backend/app/alembic/versions/upstream.py
git add .
git commit -qm upstream
git checkout -q feature
expect_failure 'git merge origin/master' bash scripts/preflight.sh
[[ ! -s $PREFLIGHT_CALLS ]]
bash scripts/preflight.sh --list > "$scratch/list"
rg -q '迁移同步冲突' "$scratch/list"
echo 'preflight self-check: passed'
