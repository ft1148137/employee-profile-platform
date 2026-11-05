#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=deploy/common.sh
source deploy/common.sh
[[ $# == 1 && -f "$1" && -f .env ]] || fail '用法：./restore.sh <备份文件>（不能将新版本备份恢复到旧代码）'
# Validate the archive before stopping the application.
docker compose exec -T db pg_restore --list < "$1" >/dev/null
echo '恢复将覆盖当前数据。不能将新版本备份恢复到旧代码。'
read -r -p '请输入「确认恢复」：' answer
[[ "$answer" == 确认恢复 ]] || fail '已取消恢复'
docker compose stop backend worker im backup
trap 'echo "恢复失败，应用保持停止。请检查备份和数据库，再重新运行 restore.sh。" >&2' ERR
# Finish decoding before opening the transaction; a damaged archive cannot commit partial SQL.
docker compose exec -T db bash -c '
    set -euo pipefail
    sql=$(mktemp)
    trap "rm -f \"$sql\"" EXIT
    pg_restore --clean --if-exists --no-owner -f "$sql"
    {
        printf "%s\n" "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
        cat "$sql"
    } | psql -X -v ON_ERROR_STOP=1 --single-transaction -U postgres -d app
' < "$1"
# Re-run migrations even when the image and configuration did not change.
docker compose rm -sf prestart
docker compose up -d --wait
check_health
echo '恢复完成。'
