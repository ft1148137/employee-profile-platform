#!/usr/bin/env bash
# Run from the repository root; --list never fetches or executes checks.
set -euo pipefail

list=false
full=${PREFLIGHT_FULL_PYTEST:-0}
for option in "$@"; do
  case "$option" in
    --list) list=true ;;
    --full) full=1 ;;
    *) echo 'Usage: bash scripts/preflight.sh [--list] [--full]' >&2; exit 2 ;;
  esac
done
[[ -d backend && -d frontend && -d scripts ]] || {
  echo 'Run from the repository root' >&2; exit 2;
}

steps=(同步检查 单一head pre-commit 数据库环境 pytest 手动提示)
states=(跳过 跳过 跳过 跳过 跳过 跳过)
elapsed=(0 0 0 0 0 0)
current=0
started=$SECONDS
step_started=$started
scratch=$(mktemp -d)
next_step() {
  elapsed[current]=$((SECONDS - step_started))
  current=$1
  step_started=$SECONDS
}
finish() {
  local code=$?
  trap - EXIT
  if [[ $code != 0 ]]; then states[current]=失败; fi
  printf '\n自检汇总（exit=%s）\n' "$code"
  elapsed[current]=$((SECONDS - step_started))
  for i in "${!steps[@]}"; do
    printf '%s: %s (%ss)\n' "${steps[$i]}" "${states[$i]}" "${elapsed[$i]}"
  done
  if [[ -n ${diversion:-} ]]; then printf '%s\n' "$diversion"; fi
  printf '总耗时: %ss\n' "$((SECONDS - started))"
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
git ls-files --others --exclude-standard > "$scratch/untracked"
cat "$scratch/untracked" >> "$scratch/dirty"
cat "$scratch/committed" "$scratch/dirty" | LC_ALL=C sort -u > "$scratch/changed"
app_diff() {
  local context=$1 path=$2
  git diff "$context" origin/master...HEAD -- "$path"
  git diff "$context" -- "$path"
  git diff --cached "$context" -- "$path"
}
app_diff -U3 backend/app > "$scratch/app-diff"
while IFS= read -r path; do
  case "$path" in
    backend/app/*.py)
      git diff --no-index -U0 -- /dev/null "$path" >> "$scratch/app-diff" || [[ $? == 1 ]] ;;
  esac
done < "$scratch/untracked"
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
  local path module package stem relative domain subpath candidate route token boundary revision shared
  local test_globs outside_domain
  local import_space='([[:space:]]|#[^\n]*\n)*'
  : > "$scratch/targets"
  : > "$scratch/focused"
  : > "$scratch/guards"
  : > "$scratch/playwright"
  : > "$scratch/services"
  while IFS= read -r path; do
    case "$path" in
      backend/tests/test_*.py|backend/tests/*/test_*.py)
        [[ ! -f $path ]] || echo "${path#backend/}" >> "$scratch/focused" ;;
      backend/tests/acceptance/*) echo tests/acceptance/ >> "$scratch/targets" ;;
      backend/tests/eval/admin_assistant/*) ;; # Covered by the registry guard below.
      backend/tests/eval/*/*)
        subpath=${path#backend/tests/eval/}
        echo "tests/eval/${subpath%%/*}/" >> "$scratch/targets" ;;
      backend/tests/eval/*) echo tests/eval/ >> "$scratch/targets" ;;
    esac
    case "$path" in
      backend/app/*.py|backend/scripts/*.py)
        relative=${path#backend/}
        module=${relative%.py}; module=${module//\//.}
        stem=${path##*/}; stem=${stem%.py}
        module=${module%.__init__}
        package=${module%.*}
        boundary='([^[:alnum:]_]|$)'
        if [[ $stem == __init__ ]]; then boundary='([^[:alnum:]_.]|$)'; fi
        test_globs=(--glob 'test_*.py' --glob '!**/acceptance/**')
        case "$path" in
          backend/app/domains/planning/solver.py|backend/app/domains/planning/simulator.py|backend/app/domains/planning/validator.py|backend/app/domains/planning/cpsat.py|backend/app/domains/planning/snapshot.py|backend/scripts/eval_planning.py|backend/scripts/eval_solver.py|backend/scripts/export_planning_case.py)
            echo tests/eval/planning/ >> "$scratch/targets" ;;
          backend/scripts/eval_coordination.py)
            echo tests/eval/coordination/ >> "$scratch/targets" ;;
          backend/scripts/eval_*.py) ;;
          *) test_globs+=(--glob '!**/eval/**') ;;
        esac
        # Match qualified references and package imports, including multiline lists.
        rg -l -U \
          -e "(^|[^[:alnum:]_])${module//./\\.}${boundary}" \
          -e "from[[:blank:]]+${package//./\\.}[[:blank:]]+import[[:blank:]]+(\\($import_space)?([[:alnum:]_]+([[:blank:]]+as[[:blank:]]+[[:alnum:]_]+)?[[:blank:]]*,$import_space)*${module##*.}([^[:alnum:]_]|$)" \
          "${test_globs[@]}" backend/tests > "$scratch/importers" || [[ $? == 1 ]]
        domain=
        shared=false
        if [[ $path == backend/app/domains/*/*.py ]]; then
          subpath=${path#backend/app/domains/}
          domain=${subpath%%/*}
          case "$stem" in
            models|*_models|schemas|*_schemas|contracts|*_contracts|types|*_types)
              shared=true ;;
          esac
        fi
        if $shared; then
          echo "共享领域模块 ${path}：本地选择本域测试及引用变更类型的测试，全量回归交给 CI。"
          # Git can put the preceding class in the header of an unrelated top-level change.
          app_diff -U0 "$path" | awk '
            /^diff --git / {header=""}
            /^@@ / {header=$0}
            /^[+-][^+-]/ {
              print
              if (header && /^[+-][[:blank:]]+[^[:blank:]]/) {
                print header
                header=""
              }
            }
          ' | sed -nE \
            -e 's/^(@@ .* @@ |[+-])(class|def|type) ([[:alnum:]_]+).*/\3/p' \
            -e 's/^(@@ .* @@ |[+-])([A-Z][A-Z_0-9]+)[[:space:]]*[:=].*/\2/p' |
            LC_ALL=C sort -u > "$scratch/symbols"
          outside_domain=()
          while IFS= read -r candidate; do
            case "$candidate" in
              backend/tests/test_m6_capacity_architecture.py|backend/tests/"$domain"/*|backend/tests/domains/"$domain"/*|*/test_"$domain".py|*/test_"$domain"_*.py|*/test_*_"$domain".py|*/test_*_"$domain"_*.py)
                echo "${candidate#backend/}" >> "$scratch/targets" ;;
              *) outside_domain+=("$candidate") ;;
            esac
          done < "$scratch/importers"
          # A test can cover the changed contract even if its filename names a different domain.
          if [[ -s $scratch/symbols && ${#outside_domain[@]} -gt 0 ]]; then
            rg -l -F -w -f "$scratch/symbols" -- "${outside_domain[@]}" > "$scratch/affected-importers" || [[ $? == 1 ]]
            sed 's|^backend/||' "$scratch/affected-importers" >> "$scratch/targets"
          fi
        else
          sed 's|^backend/||' "$scratch/importers" >> "$scratch/targets"
        fi
        relative=${relative#app/}
        for candidate in "backend/tests/${relative%/*}/test_$stem.py" "backend/tests/${relative%.py}/"; do
          [[ ! -e $candidate ]] || echo "${candidate#backend/}" >> "$scratch/focused"
        done
        if [[ -n $domain ]]; then
          relative=${relative#domains/}
          for candidate in "backend/tests/${relative%/*}/test_$stem.py" "backend/tests/${relative%.py}/" "backend/tests/domains/test_${domain}_${stem}.py"; do
            [[ ! -e $candidate ]] || echo "${candidate#backend/}" >> "$scratch/focused"
          done
          if $shared || [[ $stem == __init__ || $stem == service ]]; then
            candidate="backend/tests/domains/test_$domain.py"
            [[ ! -f $candidate ]] || echo "${candidate#backend/}" >> "$scratch/focused"
          fi
        fi
        if [[ $path == backend/app/domains/__init__.py ]]; then
          echo tests/test_m6_capacity_architecture.py >> "$scratch/guards"
        fi
        case "$path" in
          backend/app/models.py|backend/app/core/*)
            echo "共享底层模块 ${path}：本地选择直接引用及对应测试，全量回归交给 CI。" ;;
        esac ;;
    esac
    case "$path" in
      .github/*) echo tests/test_ci_workflows.py >> "$scratch/guards" ;;
      backend/app/domains/audit/*|frontend/src/components/Admin/auditLabels.ts)
        echo tests/domains/test_audit_labels.py >> "$scratch/guards" ;;
      backend/app/domains/admin_assistant/*change_set*|backend/app/domains/admin_assistant/*audit*|backend/app/domains/admin_assistant/registry.py|backend/scripts/eval_admin_assistant.py|backend/tests/eval/admin_assistant/*)
        # Evaluation patches the shared registry and can break change-set guards.
        echo tests/admin_assistant/ >> "$scratch/guards"
        if [[ $path == backend/app/domains/admin_assistant/registry.py ]]; then
          # The turn integration tests exercise registry tools through the callback.
          echo tests/domains/test_admin_assistant.py >> "$scratch/guards"
        fi ;;
      backend/app/alembic/versions/*)
        if [[ $path == *.py ]]; then
          revision=${path##*/}; revision=${revision%%_*}
          rg -l -F -w --glob 'test_*_migration.py' -- "$revision" backend/tests | sed 's|^backend/||' >> "$scratch/migration-tests" || [[ ${PIPESTATUS[0]} == 1 ]]
        fi ;;
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
  if [[ -f $scratch/migration-tests ]]; then cat "$scratch/migration-tests" >> "$scratch/guards"; fi
  if rg -q '^backend/app/alembic/versions/[^/]+\.py$' "$scratch/changed" && ! rg -q '^tests/.*test_.*_migration\.py$' "$scratch/guards" "$scratch/focused"; then
    rg --files backend/tests -g 'test_*_migration.py' | sed 's|^backend/||' >> "$scratch/guards"
  fi
  # An audit label can change on the action line of an unchanged multiline call.
  if awk '
    /^(diff --git |@@ )/ {action=0; audit_context=0}
    /^[+-][^+-]/ && /audit|target_type/ {found=1}
    /^[+-][[:space:]]*action[[:space:]]*=/ {action=1}
    /^ / && /audit\(|target_type[[:space:]]*=/ {audit_context=1}
    action && audit_context {found=1}
    END {exit !found}
  ' "$scratch/app-diff"; then
    echo tests/domains/test_audit_labels.py >> "$scratch/guards"
  fi
}

normalize_targets() {
  local candidate
  LC_ALL=C sort -u "$@" > "$scratch/sorted"
  # A directory already includes its children; don't execute the same test twice.
  while IFS= read -r candidate; do
    if [[ -e backend/$candidate ]]; then echo "$candidate"; fi
  done < "$scratch/sorted" > "$scratch/existing"
  awk 'prefix && index($0, prefix) == 1 {next} {print; prefix = ($0 ~ /\/$/ ? $0 : "")}' "$scratch/existing"
}
select_checks
normalize_targets "$scratch/targets" "$scratch/focused" "$scratch/guards" > "$scratch/selected"
if [[ $full != 1 && -s $scratch/selected ]]; then
  rg --files backend/tests -g 'test_*.py' | sed 's|^backend/||' > "$scratch/test-files"
  total=$(awk 'END {print NR}' "$scratch/test-files")
  selected=$(awk '
    NR == FNR {if ($0 ~ /\/$/) dirs[$0]; else files[$0]; next}
    $0 in files {count++; next}
    {for (dir in dirs) if (index($0, dir) == 1) {count++; break}}
    END {print count+0}
  ' "$scratch/selected" "$scratch/test-files")
  if (( selected * 100 > total * 30 )); then
    printf '%s\n' tests/test_ci_workflows.py tests/test_test_database.py >> "$scratch/guards"
    normalize_targets "$scratch/focused" "$scratch/guards" > "$scratch/selected"
    diversion="pytest 分流：命中 $selected/$total 个测试文件（超过 30%），仅执行直接改动、对应单测和守卫；全量后端回归交由 GitHub Actions CI（3 分片并行）验收。"
    echo "$diversion"
  fi
fi

echo '检查计划: 同步检查 → (cd backend && uv run alembic heads) → uv run prek run → 数据库环境 → pytest'
echo "pytest 目标（backend 下，uv run pytest --no-cov -x -q -n ${PREFLIGHT_PYTEST_WORKERS:-4} --dist loadgroup）:"
cat "$scratch/selected"
if [[ -s $scratch/dirty ]]; then
  echo '工作区有未提交改动：请先提交；本次用 uv run prek run --files 检查所有现存变动文件。'
fi
if [[ -s $scratch/local-migrations && -s $scratch/upstream-migrations ]]; then
  echo '迁移同步冲突：请先 git merge origin/master，再把新迁移的 down_revision 接到最新 head。'
  if ! $list; then exit 1; fi
fi
if ! $list; then
  states[0]=通过
  next_step 1
  heads=$(cd backend && uv run alembic heads)
  printf '%s\n' "$heads"
  count=$(printf '%s\n' "$heads" | awk 'NF {n++} END {print n+0}')
  [[ $count == 1 ]] || { echo "Alembic 必须只有一个 head，当前为 $count" >&2; exit 1; }
  states[1]=通过
  next_step 2
  if [[ -s $scratch/dirty ]]; then
    files=()
    while IFS= read -r path; do [[ ! -f $path ]] || files+=("$path"); done < "$scratch/changed"
    if [[ ${#files[@]} -gt 0 ]]; then
      uv run prek run --files "${files[@]}"
    else
      uv run prek run --from-ref origin/master --to-ref HEAD
    fi
  else
    uv run prek run --from-ref origin/master --to-ref HEAD
  fi
  states[2]=通过
  next_step 3
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
  next_step 4
  targets=()
  while IFS= read -r path; do targets+=("$path"); done < "$scratch/selected"
  if [[ ${#targets[@]} -gt 0 ]]; then
    (cd backend && uv run pytest --no-cov -x -q -n "${PREFLIGHT_PYTEST_WORKERS:-4}" --dist loadgroup "${targets[@]}")
    states[4]=通过
  else
    echo 'pytest: 跳过（无受影响目标）'
  fi
fi
next_step 5
echo '仅提示：可能受影响的 Playwright spec（请核对改动文案 / aria-label，按需手动跑）：'
LC_ALL=C sort -u "$scratch/playwright"
echo '仅提示：需要本地构建的服务（本脚本不执行构建）：'
LC_ALL=C sort -u "$scratch/services"
if ! $list; then states[5]=通过; fi
