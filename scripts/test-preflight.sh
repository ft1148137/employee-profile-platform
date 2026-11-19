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
mkdir -p scripts backend/app/domains/{sample,planning} backend/app/alembic/versions backend/tests/{sample,domains,admin_assistant,api/routes,eval/planning,eval/coordination,acceptance} frontend/{src/routes,tests} .github
cp "$source_script" scripts/preflight.sh
touch frontend/tests/.gitkeep
touch backend/tests/sample/test_feature.py backend/tests/domains/test_audit_labels.py backend/tests/test_ci_workflows.py backend/tests/test_sample_migration.py backend/tests/admin_assistant/test_change_set.py
echo 'from app.domains.sample import feature' > backend/tests/domains/test_importer.py
echo 'from app.domains.sample import feature' > backend/tests/eval/planning/test_eval.py
echo 'from app.domains.sample import feature' > backend/tests/acceptance/test_acceptance.py
echo 'from app.domains import planning' > backend/tests/eval/coordination/test_coordination.py
echo 'from app.domains.planning import solver' >> backend/tests/eval/coordination/test_coordination.py
touch backend/tests/domains/test_sample_unrelated.py
for module in models schemas demand_schemas; do
  printf 'class OldContract:\n    pass\n\nclass ChangedContract:\n    value: str\n' > "backend/app/domains/sample/$module.py"
  echo "from app.domains.sample.$module import OldContract" >> backend/tests/sample/test_shared.py
  echo "from app.domains.sample.$module import OldContract" >> backend/tests/api/routes/test_unrelated.py
  echo "from app.domains.sample.$module import ChangedContract" >> backend/tests/api/routes/test_affected.py
done
printf 'class OldContract:\n    pass\n\ntype ChangedContract = str\n' > backend/app/domains/sample/demand_schemas.py
echo 'from app.domains import sample' > backend/tests/domains/test_package_importer.py
echo 'domains/__init__.py' > backend/tests/test_m6_capacity_architecture.py
echo 'before' > backend/app/domains/__init__.py
echo 'oldrev000001' > backend/tests/test_old_migration.py
printf 'audit(action="sample.existing")\nbefore\n' > backend/app/domains/sample/feature.py
printf 'record_audit(\n    action="sample.old",\n    target_type="sample",\n)\n' > backend/app/domains/sample/audited.py
echo 'before' > backend/app/domains/planning/solver.py
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

printf 'audit(action="sample.existing")\nafter\n' > backend/app/domains/sample/feature.py
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
for target in tests/sample/test_feature.py tests/domains/test_importer.py tests/domains/test_deleted.py tests/domains/test_audit_labels.py tests/test_ci_workflows.py tests/test_sample_migration.py tests/admin_assistant/ frontend/tests/settings.spec.ts; do
  rg -q -F -x "$target" "$scratch/list"
done
rg -q '^pi$' "$scratch/list"
bash scripts/preflight.sh > "$scratch/run"
rg -q 'pytest: 通过' "$scratch/run"
for step in 同步检查 单一head pre-commit 数据库环境 pytest; do
  rg -q "^${step}: 通过 \([0-9]+s\)$" "$scratch/run"
done
rg -q '^总耗时: [0-9]+s$' "$scratch/run"
rg -q 'run prek run --files' "$PREFLIGHT_CALLS"
[[ $(rg -c '^run prek run' "$PREFLIGHT_CALLS") == 1 ]]
rg -q 'run pytest --no-cov -x -q -n 4 --dist loadgroup' "$PREFLIGHT_CALLS"
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
rg -q '^pytest: 失败 \([0-9]+s\)$' "$scratch/failure"
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
: > "$PREFLIGHT_CALLS"
bash scripts/preflight.sh > "$scratch/run"
[[ $(rg -c '^run prek run' "$PREFLIGHT_CALLS") == 1 ]]
rg -q '^run prek run --from-ref origin/master --to-ref HEAD$' "$PREFLIGHT_CALLS"

git checkout -qb selection main
printf 'audit(action="sample.existing")\nafter\n' > backend/app/domains/sample/feature.py
bash scripts/preflight.sh --list > "$scratch/list"
for target in tests/sample/test_feature.py tests/domains/test_importer.py; do
  rg -q -F -x "$target" "$scratch/list"
done
assert_absent '^tests/(eval/|acceptance/|domains/test_(sample_unrelated|audit_labels)\.py)' "$scratch/list"
git restore backend/app/domains/sample/feature.py

printf 'record_audit(\n    action="sample.new",\n    target_type="sample",\n)\n' > backend/app/domains/sample/audited.py
bash scripts/preflight.sh --list > "$scratch/list"
rg -q -F -x 'tests/domains/test_audit_labels.py' "$scratch/list"
git restore backend/app/domains/sample/audited.py

for module in models schemas demand_schemas; do
  sed 's/str/int/' "backend/app/domains/sample/$module.py" > "$scratch/updated"
  cp "$scratch/updated" "backend/app/domains/sample/$module.py"
  bash scripts/preflight.sh --list > "$scratch/list"
  rg -q '全量回归交给 CI' "$scratch/list"
  rg -q -F -x 'tests/sample/test_shared.py' "$scratch/list"
  rg -q -F -x 'tests/api/routes/test_affected.py' "$scratch/list"
  assert_absent '^tests/api/routes/test_unrelated.py$|^tests/sample/test_feature.py$' "$scratch/list"
  git restore "backend/app/domains/sample/$module.py"
done

echo 'after' > backend/app/domains/planning/solver.py
bash scripts/preflight.sh --list > "$scratch/list"
rg -q -F -x 'tests/eval/planning/' "$scratch/list"
rg -q -F -x 'tests/eval/coordination/test_coordination.py' "$scratch/list"
git restore backend/app/domains/planning/solver.py
echo 'case' > backend/tests/eval/planning/case.yml
echo 'after' >> backend/tests/acceptance/test_acceptance.py
bash scripts/preflight.sh --list > "$scratch/list"
rg -q -F -x 'tests/eval/planning/' "$scratch/list"
rg -q -F -x 'tests/acceptance/test_acceptance.py' "$scratch/list"
assert_absent '^tests/eval/coordination/' "$scratch/list"
rm backend/tests/eval/planning/case.yml
git restore backend/tests/acceptance/test_acceptance.py

echo 'newrev000001' > backend/app/alembic/versions/newrev000001_added.py
echo 'newrev000001' > backend/tests/test_new_migration.py
git add .
git commit -qm migration
echo 'after' > backend/app/domains/__init__.py
bash scripts/preflight.sh --list > "$scratch/list"
rg -q -F -x 'tests/domains/test_package_importer.py' "$scratch/list"
rg -q -F -x 'tests/test_m6_capacity_architecture.py' "$scratch/list"
rg -q -F -x 'tests/test_new_migration.py' "$scratch/list"
assert_absent '^tests/domains/test_importer.py$|^tests/test_old_migration.py$' "$scratch/list"
: > "$PREFLIGHT_CALLS"
bash scripts/preflight.sh > "$scratch/run"
[[ $(rg -c '^run prek run' "$PREFLIGHT_CALLS") == 1 ]]
rg -q '^run prek run --files ' "$PREFLIGHT_CALLS"
rg -q 'backend/app/alembic/versions/newrev000001_added.py' "$PREFLIGHT_CALLS"
rg -q 'backend/app/domains/__init__.py' "$PREFLIGHT_CALLS"
git restore backend/app/domains/__init__.py
git checkout -q feature
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
