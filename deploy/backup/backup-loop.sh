#!/usr/bin/env bash
set -euo pipefail
umask 077
export LC_ALL=C
keep=${BACKUP_KEEP:-14}
time=${BACKUP_TIME:-02:00}
interval=${BACKUP_INTERVAL_SECONDS:-}
[[ "$keep" =~ ^[1-9][0-9]*$ ]] || { echo 'BACKUP_KEEP 必须为正整数' >&2; exit 1; }
[[ "$time" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo 'BACKUP_TIME 必须为 HH:MM' >&2; exit 1; }
[[ -z "$interval" || "$interval" =~ ^[1-9][0-9]*$ ]] || { echo 'BACKUP_INTERVAL_SECONDS 必须为正整数' >&2; exit 1; }
mkdir -p /backups
chmod 700 /backups
backup_once() (
    # The scheduler and manual backup share this lock and retention policy.
    exec 9>/backups/.lock
    flock -n 9 || { echo '已有备份正在执行' >&2; exit 1; }
    partial=''
    trap 'rm -f "$partial"' EXIT
    file="/backups/app-$(date +%Y%m%d-%H%M%S).dump"
    while [[ -e "$file" ]]; do
        sleep 1
        file="/backups/app-$(date +%Y%m%d-%H%M%S).dump"
    done
    partial="$file.partial"
    pg_dump -Fc --no-owner -f "$partial"
    mv "$partial" "$file"
    chown --reference=/backups "$file"
    shopt -s nullglob
    files=(/backups/app-*.dump)
    excess=$((${#files[@]} - keep))
    if (( excess > 0 )); then rm -- "${files[@]:0:excess}"; fi
    echo "$file"
)
if [[ "${1:-}" == --once ]]; then
    backup_once
    exit
fi
last_day=''
while true; do
    today=$(date +%F)
    if [[ -n "$interval" ]]; then
        sleep "$interval"
        backup_once || echo '备份失败，将在下次计划重试' >&2
    else
        if [[ "$(date +%H:%M)" == "$time" && "$last_day" != "$today" ]]; then
            last_day=$today
            backup_once || echo '备份失败，将在下次计划重试' >&2
        fi
        sleep 20
    fi
done
