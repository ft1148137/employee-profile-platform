#!/usr/bin/env bash
set -euo pipefail

scripts_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
runner=$scripts_dir/remote-test.sh
metadata=$scripts_dir/remote-test-metadata.py
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

expect_failure() {
  local expected=$1
  shift
  if "$@" >"$scratch/output" 2>&1; then
    echo "Expected failure containing: $expected" >&2
    exit 1
  fi
  if ! grep -Fq -- "$expected" "$scratch/output"; then
    cat "$scratch/output" >&2
    exit 1
  fi
}

export CLOUD_RUNNER_CONFIG=$scratch/missing.env
unset GCP_PROJECT GCP_ZONE GCP_INSTANCE
help=$("$runner" --help)
grep -q 'Usage:' <<< "$help"
expect_failure 'Usage:' "$runner" unknown
expect_failure 'preflight does not take arguments' "$runner" preflight unexpected
expect_failure '--force only applies' "$runner" --force status
expect_failure '1–2999' env COMPOSE_PROJECT_NAME=nere3000 "$runner" pytest
expect_failure '1–8' env COMPOSE_PROJECT_NAME=nere123 PREFLIGHT_PYTEST_WORKERS=9 "$runner" preflight
expect_failure 'Set GCP_PROJECT' "$runner" status

export CLOUD_RUNNER_CONFIG=$scratch/runner.env
export GCP_PROJECT=sample-project
export USAGE_LEDGER_FILE=$scratch/usage.jsonl
export SOFT_CAP_USD=36 DISK_FIXED_MONTHLY_USD=36
cat > "$CLOUD_RUNNER_CONFIG" <<'CONFIG'
GCP_PROJECT=ignored-project
GCP_ZONE=example-zone
GCP_INSTANCE=example-instance
SPOT_HOURLY_RATE_USD=0.2
CONFIG
mkdir -p "$scratch/bin"
cat > "$scratch/bin/gcloud" <<'MOCK'
#!/usr/bin/env bash
set -eu
case "$*" in
  'compute instances describe example-instance --project sample-project '*)
    printf '%s\n' '{"status":"TERMINATED"}' ;;
  'compute instances start example-instance --project sample-project '*)
    echo 'mock start reached'
    exit 99 ;;
  *) echo 'Unexpected gcloud invocation' >&2; exit 3 ;;
esac
MOCK
chmod +x "$scratch/bin/gcloud"
export PATH="$scratch/bin:$PATH"
"$runner" status | grep -F 'VM: TERMINATED'
expect_failure 'Monthly soft cap reached' env COMPOSE_PROJECT_NAME=nere123 "$runner" preflight
if grep -q 'mock start reached' "$scratch/output"; then exit 1; fi
expect_failure 'mock start reached' env COMPOSE_PROJECT_NAME=nere123 "$runner" --force preflight
expect_failure 'mock start reached' env COMPOSE_PROJECT_NAME=nere123 PREFLIGHT_PYTEST_WORKERS=8 "$runner" --force preflight --full

# Stop the worker at its first VM operation after checking the lease selection.
mkdir "$scratch/leases"
touch "$scratch/leases/quant-test.lock"
cat > "$scratch/bin/flock" <<'MOCK'
#!/usr/bin/env bash
[[ $* == '-xn -E 75 9' && -f /dev/fd/9 ]] || exit 3
exit "${MOCK_LEASE_STATUS:-0}"
MOCK
cat > "$scratch/bin/lsof" <<'MOCK'
#!/usr/bin/env bash
echo 'mock lsof reached' >&2
exit 3
MOCK
chmod +x "$scratch/bin/flock" "$scratch/bin/lsof"
worker=$scripts_dir/remote-test-worker.sh
for lease in 0 75; do
  expect_failure 'mock lsof reached' env CLOUD_TEST_LEASE_DIR="$scratch/leases" MOCK_LEASE_STATUS=$lease bash "$worker" fixture nere123 preflight ''
  if [[ $lease == 0 ]]; then expected=6; else expected=4; fi
  grep -Fq "pytest workers: $expected" "$scratch/output"
done
expect_failure 'mock lsof reached' env CLOUD_TEST_LEASE_DIR="$scratch/leases" MOCK_LEASE_STATUS=75 bash "$worker" fixture nere123 preflight 8
grep -Fq 'pytest workers: 8（显式指定）' "$scratch/output"

# Mock wall-clock time after the first hour, including when run at month rollover.
python3 - "$metadata" "$scratch" <<'PY'
from datetime import UTC, datetime
from pathlib import Path
import importlib.util
import io
import sys
from contextlib import redirect_stdout

script, work = sys.argv[1:]
spec = importlib.util.spec_from_file_location("metadata", script)
assert spec and spec.loader
metadata = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(metadata)

class TestClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 1, 2, tzinfo=UTC).astimezone(tz)

metadata.datetime = TestClock
instance = '{"status":"TERMINATED","lastStartTimestamp":"2026-10-31T12:00:00+00:00","lastStopTimestamp":"2026-11-01T01:00:00+00:00"}'
ledger = Path(work) / "estimate.jsonl"
for cap, expected in (("12", "at-cap"), ("", "below-cap")):
    output = io.StringIO()
    with redirect_stdout(output):
        metadata.estimate(ledger, instance, "10", "2", cap, "40")
    assert "1.00 h. Estimated cost: $12.00" in output.getvalue()
    assert output.getvalue().splitlines()[-1] == expected
assert len(ledger.read_text().splitlines()) == 1
PY

mkdir "$scratch/repo" "$scratch/tree"
cd "$scratch/repo"
git init -q
git config user.name Fixture
git config user.email fixture@example.invalid
git config commit.gpgsign false
printf '*.local\n' > .gitignore
printf 'base\n' > code
printf 'public example\n' > .env.example
git add -f .gitignore code .env.example
git commit -qm base
git update-ref refs/remotes/origin/master HEAD
mkdir -p frontend/credentials nested/.codex nested/.git/objects nested/node_modules nested/.venv
for path in .env frontend/.env.local frontend/credentials/key.json nested/.codex/auth.json \
  nested/.git/objects/internal nested/node_modules/package.json nested/.venv/settings \
  nested/site.pem nested/cloud-runner.env private.local; do
  printf 'sensitive fixture\n' > "$path"
done
git add -f .env frontend/credentials/key.json
printf 'committed\n' > code
git add code
git commit -qm committed
printf 'staged secret\n' > frontend/.env.local
printf 'staged\n' > code
printf 'example staged\n' > .env.example
git add code .env.example
git add -f frontend/.env.local
printf 'working\n' > code
printf 'untracked\n' > 'new file'
python3 "$metadata" snapshot "$scratch/tree" fixture
[[ $(cat "$scratch/tree/code") == working ]]
[[ $(cat "$scratch/tree/.env.example") == 'example staged' ]]
[[ $(cat "$scratch/tree/new file") == untracked ]]
[[ $(cat "$scratch/tree/.remote-test-snapshot/id") == fixture ]]
[[ ! -e "$scratch/tree/frontend" && ! -e "$scratch/tree/nested" &&
   ! -e "$scratch/tree/.env" && ! -e "$scratch/tree/private.local" &&
   ! -e "$scratch/tree/.git" ]]
if grep -R -E 'sensitive fixture|staged secret' "$scratch/tree"; then exit 1; fi
grep -q '^+committed$' "$scratch/tree/.remote-test-snapshot/committed.patch"
grep -q '^+staged$' "$scratch/tree/.remote-test-snapshot/staged.patch"
grep -q '^+example staged$' "$scratch/tree/.remote-test-snapshot/staged.patch"
mkdir "$scratch/another"
ln -s "$scratch/usage.jsonl" outside
expect_failure 'Cannot sync symlink' python3 "$metadata" snapshot "$scratch/another" fixture

# IAP is mandatory. Reject account identifiers, external emails, private keys
# and any VM stop invocation in the checked-in implementation.
grep -q -- '--tunnel-through-iap' "$runner"
if grep -Ei 'instances[[:space:]]+stop|[[:xdigit:]]{6}(-[[:xdigit:]]{6}){2}|BEGIN .*PRIVATE KEY' \
  "$runner" "$metadata" "$scripts_dir/remote-test-worker.sh"; then exit 1; fi
if grep -Ei '[[:alnum:]_.+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}' \
  "$runner" "$metadata" "$scripts_dir/remote-test-worker.sh" | grep -vE '@example\.(com|invalid)'; then exit 1; fi

if grep -E '([0-9]{1,3}\.){3}[0-9]{1,3}' \
  "$runner" "$metadata" "$scripts_dir/remote-test-worker.sh" | grep -vF '127.0.0.1'; then exit 1; fi

echo 'remote-test tests passed'
