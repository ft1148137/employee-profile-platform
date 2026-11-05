#!/usr/bin/env bash
# Run against an already started demo stack; never evaluate .env as shell code.
set -euo pipefail
step="prerequisites"
trap 'printf "FAIL: %s (line %s)\n" "$step" "$LINENO" >&2' ERR
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
for command in curl python3 docker; do
  command -v "$command" >/dev/null
done
docker compose version >/dev/null
BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"

step="demo password configuration"
DEMO_USER_PASSWORD="$(python3 - <<'PY'
import os
from pathlib import Path

password = os.environ.get("DEMO_USER_PASSWORD")
if password is None:
    for line in Path(".env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "DEMO_USER_PASSWORD":
            password = value.strip()
            if len(password) >= 2 and password[0] == password[-1] and password[0] in "\"'":
                password = password[1:-1]
            break
if not password:
    raise SystemExit("Set DEMO_USER_PASSWORD or add it to the root .env")
print(password)
PY
)"

step="db/backend/worker health and im running"
docker compose ps --all --format json db backend worker im | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines()]
for service in ("db", "backend", "worker"):
    matches = [row for row in rows if row["Service"] == service]
    assert matches and all(row.get("State") == "running" and row.get("Health") == "healthy" for row in matches), f"{service} is missing or unhealthy"
im = [row for row in rows if row["Service"] == "im"]
assert im and all(row.get("State") == "running" for row in im), "im is missing or stopped"
'
printf 'PASS: %s\n' "$step"

request() {
  curl --silent --show-error --fail --max-time 30 "$@"
}
for path in /api/v1/utils/health-check/ /; do
  step="HTTP 200 $path"
  status="$(request --output /dev/null --write-out '%{http_code}' "$BASE_URL$path")"
  test "$status" = 200
  printf 'PASS: %s\n' "$step"
done

login() {
  request --data-urlencode "username=$1" --data-urlencode "password=$DEMO_USER_PASSWORD" \
    "$BASE_URL/api/v1/login/access-token" | python3 -c '
import json, sys
token = json.load(sys.stdin)["access_token"]
assert token, "Empty access token"
print(token)
'
}
get() {
  request --header "Authorization: Bearer $1" "$BASE_URL/api/v1$2"
}

step="employee login and demo membership"
employee_token="$(login employee.platform@demo.example.com)"
membership="$(get "$employee_token" /me/memberships | python3 -c '
import json, sys
membership, = [m for m in json.load(sys.stdin) if m["org"]["slug"] == "demo"]
assert membership["employee_id"], "No employee identity"
print(membership["org"]["id"], membership["employee_id"])
')"
read -r org_id employee_id <<< "$membership"
employee_path="/orgs/$org_id/employees/$employee_id"

step="employee profile covers the capability dictionary"
capability_count="$(get "$employee_token" "/orgs/$org_id/capabilities" | python3 -c '
import json, sys
count = len(json.load(sys.stdin)["data"])
assert count == 12, f"Expected 12 demo capabilities, got {count}"
print(count)
')"
get "$employee_token" "$employee_path/profile" | python3 -c '
import json, sys
items = json.load(sys.stdin)["items"]
assert len(items) == int(sys.argv[1]), "Profile does not cover every capability"
' "$capability_count"
printf 'PASS: %s (%s)\n' "$step" "$capability_count"

for role in admin manager.platform; do
  step="$role login and self-report permissions"
  token="$(login "$role@demo.example.com")"
  get "$token" "$employee_path/evidence?kind=self_report" | python3 -c '
import json, sys
rows = json.load(sys.stdin)["data"]
assert rows, "No self-report evidence found"
expected = sys.argv[1] == "admin"
assert all(row["kind"] == "self_report" and row["redacted"] is expected for row in rows), "Unexpected self-report visibility"
' "$role"
  printf 'PASS: %s\n' "$step"
done
printf 'PASS: Compose smoke test\n'
