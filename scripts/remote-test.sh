#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo 'Usage: scripts/remote-test.sh [--force] {preflight|pytest [args...]|playwright [args...]|status}'
  echo 'Set COMPOSE_PROJECT_NAME=nere<issue-number> (optional -review suffix) for test runs.'
}

die() { echo "$*" >&2; exit 2; }

force=false
if [[ ${1:-} == --force ]]; then force=true; shift; fi
mode=${1:-}
case "$mode" in
  -h|--help) usage; exit 0 ;;
  preflight|pytest|playwright|status) shift ;;
  *) usage >&2; exit 2 ;;
esac
case "$mode" in
  preflight|status) [[ $# == 0 ]] || die "$mode does not take arguments" ;;
esac
project=${COMPOSE_PROJECT_NAME:-}
workers=${PREFLIGHT_PYTEST_WORKERS:-4}
if [[ $mode == status ]]; then
  ! $force || die '--force only applies to test runs'
else
  if ! [[ $project =~ ^nere([1-9][0-9]{0,3})(-review)?$ ]] || (( BASH_REMATCH[1] > 2999 )); then
    die 'Set COMPOSE_PROJECT_NAME=nere<issue-number 1–2999> (optional -review suffix)'
  fi
  [[ $workers =~ ^[1-4]$ ]] || die 'Set PREFLIGHT_PYTEST_WORKERS to 1–4'
fi

config_dir=$HOME/.config/employee-profile-platform
config=${CLOUD_RUNNER_CONFIG:-$config_dir/cloud-runner.env}
keys=(GCP_PROJECT GCP_ZONE GCP_INSTANCE MONTHLY_BUDGET_USD SOFT_CAP_USD
      DISK_FIXED_MONTHLY_USD SPOT_HOURLY_RATE_USD USAGE_LEDGER_FILE)
if [[ -f $config ]]; then
  overrides=()
  values=()
  for key in "${keys[@]}"; do
    if declare -p "$key" >/dev/null 2>&1; then
      overrides+=("$key")
      values+=("${!key}")
    fi
  done
  # shellcheck source=/dev/null
  source "$config"
  for i in "${!overrides[@]}"; do printf -v "${overrides[i]}" '%s' "${values[i]}"; done
fi
[[ ${GCP_PROJECT:-} =~ ^[a-z][a-z0-9-]+$ && ${GCP_ZONE:-} =~ ^[a-z0-9-]+$ &&
   ${GCP_INSTANCE:-} =~ ^[a-z][a-z0-9-]*$ ]] || die \
  "Set GCP_PROJECT, GCP_ZONE and GCP_INSTANCE in $config or export them. See docs/cloud-testing.md."
[[ -n ${SPOT_HOURLY_RATE_USD:-} ]] || die "Set SPOT_HOURLY_RATE_USD in $config or export it."
export GCP_PROJECT GCP_ZONE GCP_INSTANCE
export PATH
monthly=${MONTHLY_BUDGET_USD:-40}
cap=${SOFT_CAP_USD:-}
disk=${DISK_FIXED_MONTHLY_USD:-10}
ledger=${USAGE_LEDGER_FILE:-$config_dir/cloud-runner-usage.jsonl}

scripts_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
metadata=$scripts_dir/remote-test-metadata.py
umask 077

observe() {
  local instance estimate
  instance=$(gcloud compute instances describe "$GCP_INSTANCE" --project "$GCP_PROJECT" \
    --zone "$GCP_ZONE" --format='json(status,lastStartTimestamp,lastStopTimestamp)') || return
  vm_status=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])' <<< "$instance") || return
  estimate=$(python3 "$metadata" estimate "$ledger" "$instance" "$disk" "$SPOT_HOURLY_RATE_USD" "$cap" "$monthly") || return
  budget_state=${estimate##*$'\n'}
  printf 'VM: %s\n%s\n' "$vm_status" "${estimate%$'\n'*}"
}

cloud_ssh() {
  gcloud compute ssh "$GCP_INSTANCE" --project "$GCP_PROJECT" --zone "$GCP_ZONE" \
    --tunnel-through-iap --quiet --ssh-flag=-oBatchMode=yes --ssh-flag=-oConnectTimeout=10 "$@"
}

observe
if [[ $mode == status ]]; then
  if [[ $vm_status == RUNNING ]]; then
    cloud_ssh --command="/usr/local/bin/auto-shutdown-idle.sh --status"
  else
    echo 'Active leases: unavailable while the VM is offline.'
  fi
  exit 0
fi

scratch=$(mktemp -d)
finish() {
  local code=$1
  trap - EXIT
  rm -rf -- "$scratch"
  if ! (observe); then
    echo 'Could not update the local usage estimate at exit.' >&2
    if [[ $code == 0 ]]; then code=1; fi
  fi
  exit "$code"
}
trap 'finish "$?"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

case "$vm_status" in
  TERMINATED)
    if [[ $budget_state == at-cap ]] && ! $force; then
      die 'Monthly soft cap reached. Run locally or use --force to start the VM.'
    fi
    gcloud compute instances start "$GCP_INSTANCE" --project "$GCP_PROJECT" --zone "$GCP_ZONE" --quiet
    ;;
  RUNNING|PROVISIONING|STAGING) ;;
  *) die "VM state $vm_status does not support a test run. Check status and retry." ;;
esac
ready=false
for (( attempt=0; attempt<12; attempt++ )); do
  if cloud_ssh --command=true; then ready=true; break; fi
  sleep 5
done
$ready || die 'IAP SSH did not become ready'
observe

cd "$scripts_dir/.."
run_id=$(basename "$scratch")
[[ $run_id =~ ^[A-Za-z0-9._-]+$ ]] || die 'Unexpected temporary directory name'
mkdir "$scratch/tree"
python3 "$metadata" snapshot "$scratch/tree" "$run_id"

# rsync's shell receives the instance name followed by the server command.
cat > "$scratch/iap-ssh" <<'BASH'
#!/usr/bin/env bash
host=$1
shift
exec gcloud compute ssh "$host" --project "$GCP_PROJECT" --zone "$GCP_ZONE" \
  --tunnel-through-iap --quiet --ssh-flag=-T --ssh-flag=-oBatchMode=yes --ssh-flag=-oConnectTimeout=10 \
  --command="${EPP_RSYNC_PREFIX:+$EPP_RSYNC_PREFIX }$*"
BASH
chmod 700 "$scratch/iap-ssh"

remote_root="\"\$HOME/epp-runs\""
remote_dir="\"\$HOME/epp-runs/$project\""
remote_lock="\"\$HOME/epp-runs/.$project.lock\""
lease=/usr/local/bin/cloud-test-lease-run.sh
locked="$lease epp-$project -- flock -n -E 75 $remote_lock"
# Invalidate the previous snapshot before any writes. A racing or failed sync
# can cause a retry, but it cannot run the wrong checkout.
receiver="test ! -L $remote_dir && rm -f $remote_dir/.remote-test-snapshot/id && exec \"\$@\""
upload="umask 077 && mkdir -p $remote_root && exec $locked bash -c $(printf '%q' "$receiver") epp-receiver"
EPP_RSYNC_PREFIX=$upload rsync -rpt --checksum --timeout=60 --delete --exclude='/.git' --exclude=.venv --exclude=node_modules \
  -e "$(printf '%q' "$scratch/iap-ssh")" \
  "$scratch/tree/" "$GCP_INSTANCE:epp-runs/$project/" || die 'Workspace sync failed (project may be busy)'

# Quote each argument for the VM shell. A tty gives the remote worker a HUP
# on disconnect, including when the caller interrupts gcloud or SSH.
args=$(printf ' %q' "$run_id" "$project" "$mode" "$workers" "$@")
execute="cd $remote_dir && test \"\$(cat .remote-test-snapshot/id)\" = $run_id || { echo 'Snapshot changed; retry the run.' >&2; exit 75; }; exec bash scripts/remote-test-worker.sh$args"
code=0
cloud_ssh --command="exec $locked bash -c $(printf '%q' "$execute")" -- -tt || code=$?

if [[ $mode == playwright && $code != 0 ]]; then
  report=$(mktemp -d "${TMPDIR:-/tmp}/epp-playwright.XXXXXXXX")
  remote_report="epp-runs/.artifacts/$project/$run_id"
  if rsync -rpt --timeout=60 -e "$(printf '%q' "$scratch/iap-ssh")" \
      "$GCP_INSTANCE:$remote_report/" "$report/"; then
    echo "Playwright artifacts: $report"
    cloud_ssh --command="rm -rf -- \"\$HOME/$remote_report\"" || true
  else
    rmdir "$report" 2>/dev/null || true
    echo "Playwright artifact download failed. VM path: ~/$remote_report" >&2
  fi
fi
finish "$code"
