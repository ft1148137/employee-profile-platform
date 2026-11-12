#!/usr/bin/env bash
# Run from the repository root; --list never fetches or executes checks.
set -euo pipefail

list=false
case "${1:-}" in
  --list) list=true ;;
  '') ;;
  *) echo 'Usage: bash scripts/preflight.sh [--list]' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Too many arguments' >&2; exit 2; }
[[ -d backend && -d frontend && -d scripts ]] || {
  echo 'Run from the repository root' >&2; exit 2;
}

steps=(同步检查 单一head pre-commit 数据库环境 pytest 手动提示)
states=(跳过 跳过 跳过 跳过 跳过 跳过)
current=0
scratch=$(mktemp -d)
finish() {
  local code=$?
  trap - EXIT
  if [[ $code != 0 ]]; then states[current]=失败; fi
  printf '\n自检汇总（exit=%s）\n' "$code"
  for i in "${!steps[@]}"; do printf '%s: %s\n' "${steps[$i]}" "${states[$i]}"; done
  rm -rf "$scratch"
  exit "$code"
}
trap finish EXIT

if ! $list; then
  echo '同步检查: git fetch origin'
  git fetch origin
fi
git rev-parse --verify origin/master >/dev/null
git diff --name-only origin/master...HEAD > "$scratch/committed"
git diff --name-only > "$scratch/dirty"
git diff --cached --name-only >> "$scratch/dirty"
git ls-files --others --exclude-standard >> "$scratch/dirty"
cat "$scratch/committed" "$scratch/dirty" | LC_ALL=C sort -u > "$scratch/changed"
git diff -U3 origin/master...HEAD -- backend/app > "$scratch/app-diff"
git diff -U3 -- backend/app >> "$scratch/app-diff"
git diff --cached -U3 -- backend/app >> "$scratch/app-diff"
git diff --name-only --diff-filter=A origin/master...HEAD -- backend/app/alembic/versions > "$scratch/local-migrations"
while IFS= read -r path; do
  case "$path" in
    backend/app/alembic/versions/*.py)
      if ! git cat-file -e "HEAD:$path" 2>/dev/null; then
        echo "$path" >> "$scratch/local-migrations"
      fi ;;
  esac
done < "$scratch/dirty"
git diff --name-only --diff-filter=A HEAD...origin/master -- backend/app/alembic/versions > "$scratch/upstream-migrations"

# All test selection rules live here. Paths are relative to backend for pytest.
select_checks() {
  local path module package stem relative domain candidate route token
  local import_space='([[:space:]]|#[^\n]*\n)*'
  : > "$scratch/targets"
  : > "$scratch/playwright"
  : > "$scratch/services"
  while IFS= read -r path; do
    case "$path" in
      backend/tests/test_*.py|backend/tests/*/test_*.py)
        [[ ! -f $path ]] || echo "${path#backend/}" >> "$scratch/targets" ;;
    esac
    case "$path" in
      backend/app/*.py|backend/scripts/*.py)
        relative=${path#backend/}
        module=${relative%.py}; module=${module//\//.}
        stem=${path##*/}; stem=${stem%.py}
        module=${module%.__init__}
        package=${module%.*}
        # Match qualified references and package imports, including multiline lists.
        rg -l -U \
          -e "(^|[^[:alnum:]_])${module//./\\.}([^[:alnum:]_]|$)" \
          -e "from[[:blank:]]+${package//./\\.}[[:blank:]]+import[[:blank:]]+(\\($import_space)?([[:alnum:]_]+([[:blank:]]+as[[:blank:]]+[[:alnum:]_]+)?[[:blank:]]*,$import_space)*${module##*.}([^[:alnum:]_]|$)" \
          backend/tests --glob 'test_*.py' > "$scratch/importers" || [[ $? == 1 ]]
        sed 's|^backend/||' "$scratch/importers" >> "$scratch/targets"
        relative=${relative#app/}
        for candidate in "backend/tests/${relative%/*}/test_$stem.py" "backend/tests/${relative%.py}/"; do
          [[ ! -e $candidate ]] || echo "${candidate#backend/}" >> "$scratch/targets"
        done
        if [[ $path == backend/app/domains/* ]]; then
          domain=${path#backend/app/domains/}; domain=${domain%%/*}
          [[ ! -d backend/tests/$domain ]] || echo "tests/$domain/" >> "$scratch/targets"
          rg --files backend/tests/domains -g "test_${domain}*.py" >> "$scratch/domain-tests" || [[ $? == 1 ]]
        fi
        if [[ -f $path ]] && rg -q 'audit|target_type' "$path"; then
          echo tests/domains/test_audit_labels.py >> "$scratch/targets"
        fi
        case "$path" in
          backend/app/models.py|backend/app/core/*)
            echo "共享底层模块 ${path}：本地选择直接引用及对应测试，全量回归交给 CI。" ;;
        esac ;;
    esac
    case "$path" in
      .github/*) echo tests/test_ci_workflows.py >> "$scratch/targets" ;;
      backend/app/domains/audit/*|frontend/src/components/Admin/auditLabels.ts)
        echo tests/domains/test_audit_labels.py >> "$scratch/targets" ;;
      backend/app/domains/admin_assistant/*change_set*|backend/app/domains/admin_assistant/*audit*|backend/app/domains/admin_assistant/registry.py|backend/scripts/eval_admin_assistant.py|backend/tests/eval/admin_assistant/*)
        # Evaluation patches the shared registry and can break change-set guards.
        echo tests/admin_assistant/ >> "$scratch/targets" ;;
      backend/app/alembic/versions/*)
        rg --files backend/tests -g 'test_*_migration.py' | sed 's|^backend/||' >> "$scratch/targets" ;;
    esac
    case "$path" in
      frontend/tests/*.spec.ts) [[ ! -f $path ]] || echo "$path" >> "$scratch/playwright" ;;
      frontend/src/*)
        token=${path##*/}; token=${token%.*}
        rg -l -F --glob '*.spec.ts' -- "$token" frontend/tests >> "$scratch/playwright" || [[ $? == 1 ]]
        if [[ -f $path ]]; then
          # TanStack route declarations carry the URL even for renamed page files.
          route=$(sed -n "s/.*createFileRoute([\"']\([^\"']*\)[\"']).*/\1/p" "$path" | sed 's|/_layout||')
          if [[ -n $route && $route != / ]]; then
            rg -l -F --glob '*.spec.ts' -- "$route" frontend/tests >> "$scratch/playwright" || [[ $? == 1 ]]
          fi
        fi ;;
    esac
    case "$path" in
      backend/Dockerfile*) echo 'backend（prestart / worker / im 共用镜像）' >> "$scratch/services" ;;
      pi/*) echo pi >> "$scratch/services" ;;
      frontend/Dockerfile*) echo 'playwright（核对对应 Dockerfile）' >> "$scratch/services" ;;
      *compose*.yml|*compose*.yaml) printf '%s\n' 'backend（prestart / worker / im 共用镜像）' pi 'playwright（仅 compose.dev.yml）' >> "$scratch/services" ;;
      */Dockerfile*) echo "$path（核对对应服务）" >> "$scratch/services" ;;
    esac
  done < "$scratch/changed"
  if [[ -f $scratch/domain-tests ]]; then sed 's|^backend/||' "$scratch/domain-tests" >> "$scratch/targets"; fi
  if rg -q 'audit|target_type' "$scratch/app-diff"; then
    echo tests/domains/test_audit_labels.py >> "$scratch/targets"
  fi
  LC_ALL=C sort -u "$scratch/targets" > "$scratch/sorted"
  # A directory already includes its children; don't execute the same test twice.
  while IFS= read -r candidate; do
    if [[ -e backend/$candidate ]]; then echo "$candidate"; fi
  done < "$scratch/sorted" > "$scratch/existing"
  awk 'prefix && index($0, prefix) == 1 {next} {print; prefix = ($0 ~ /\/$/ ? $0 : "")}' "$scratch/existing" > "$scratch/targets"
}
select_checks

echo '检查计划: 同步检查 → (cd backend && uv run alembic heads) → uv run prek run --from-ref origin/master --to-ref HEAD → 数据库环境 → pytest'
echo "pytest 目标（backend 下，uv run pytest --no-cov -x -q -n ${PREFLIGHT_PYTEST_WORKERS:-2} --dist loadgroup）:"
cat "$scratch/targets"
if [[ -s $scratch/dirty ]]; then
  echo '工作区有未提交改动：请先提交；本次也会用 uv run prek run --files 检查现存改动文件。'
fi
if [[ -s $scratch/local-migrations && -s $scratch/upstream-migrations ]]; then
  echo '迁移同步冲突：请先 git merge origin/master，再把新迁移的 down_revision 接到最新 head。'
  if ! $list; then exit 1; fi
fi
if ! $list; then
  states[0]=通过
  current=1
  heads=$(cd backend && uv run alembic heads)
  printf '%s\n' "$heads"
  count=$(printf '%s\n' "$heads" | awk 'NF {n++} END {print n+0}')
  [[ $count == 1 ]] || { echo "Alembic 必须只有一个 head，当前为 $count" >&2; exit 1; }
  states[1]=通过
  current=2
  uv run prek run --from-ref origin/master --to-ref HEAD
  if [[ -s $scratch/dirty ]]; then
    files=()
    while IFS= read -r path; do [[ ! -f $path ]] || files+=("$path"); done < "$scratch/dirty"
    if [[ ${#files[@]} -gt 0 ]]; then uv run prek run --files "${files[@]}"; fi
  fi
  states[2]=通过
  current=3
  # Never echo the URL: it may contain credentials. No database connection here.
  uv run python - <<'PY'
import os
import sys
from urllib.parse import urlsplit

try:
    port = int(os.environ.get("POSTGRES_PORT", ""))
    url = urlsplit(os.environ.get("DATABASE_URL", ""))
    valid = (
        url.scheme in {"postgres", "postgresql", "postgresql+psycopg"}
        and bool(url.hostname)
        and 0 < port < 65536
        and port != 5432
        and url.port == port
    )
except ValueError:
    valid = False
if not valid:
    sys.exit("数据库环境失败：显式设置 DATABASE_URL 和 POSTGRES_PORT，端口须一致且不能为默认 5432。")
PY
  states[3]=通过
  current=4
  targets=()
  while IFS= read -r path; do targets+=("$path"); done < "$scratch/targets"
  if [[ ${#targets[@]} -gt 0 ]]; then
    (cd backend && uv run pytest --no-cov -x -q -n "${PREFLIGHT_PYTEST_WORKERS:-2}" --dist loadgroup "${targets[@]}")
    states[4]=通过
  else
    echo 'pytest: 跳过（无受影响目标）'
  fi
fi
current=5
echo '仅提示：可能受影响的 Playwright spec（请核对改动文案 / aria-label，按需手动跑）：'
LC_ALL=C sort -u "$scratch/playwright"
echo '仅提示：需要本地构建的服务（本脚本不执行构建）：'
LC_ALL=C sort -u "$scratch/services"
if ! $list; then states[5]=通过; fi
