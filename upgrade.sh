#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=deploy/common.sh
source deploy/common.sh
[[ $# -le 1 && -f .env ]] || fail '用法：./upgrade.sh [git ref]；请先安装'
[[ -z "$(git status --porcelain)" ]] || fail '工作区有未提交改动，请先处理'
previous=$(git rev-parse HEAD)
backup=''
rollback() {
    echo '升级失败。回退命令：' >&2
    printf 'git checkout %q\n' "$previous" >&2
    if [[ -n "$backup" ]]; then
        printf './restore.sh %q\n' "$backup" >&2
    else
        echo '尚未生成本次备份；数据库未执行迁移，使用已有备份或重新启动旧代码。' >&2
    fi
}
trap rollback ERR
if [[ $# == 1 ]]; then
    git fetch origin
    git checkout --detach "$1"
else
    git pull --ff-only
fi
backup=$(./backup.sh)
docker compose build
docker compose up -d --wait
check_health
echo "升级完成。升级前备份：$backup"
