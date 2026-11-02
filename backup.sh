#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo '请先运行 install.sh' >&2; exit 1; }
mkdir -p backups
chmod 700 backups
file=$(docker compose exec -T backup bash /backup-loop.sh --once)
printf '%s/backups/%s\n' "$PWD" "${file##*/}"
