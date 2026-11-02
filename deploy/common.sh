#!/usr/bin/env bash
# Shared by the root deployment scripts; never execute .env as shell code.
fail() { echo "$*" >&2; exit 1; }
config_value() {
    docker compose config --environment | awk -v key="$1" 'index($0, key "=") == 1 {print substr($0, length(key) + 2)}'
}
check_health() {
    local port
    port=$(config_value HTTP_PORT)
    if [[ "$(config_value FRONTEND_HOST)" == https://* ]]; then
        curl --noproxy '*' -fkSs --resolve "$(config_value DOMAIN):443:127.0.0.1" "https://$(config_value DOMAIN)/api/v1/utils/health-check/" >/dev/null
    else
        curl --noproxy '*' -fSs "http://127.0.0.1:${port:-8000}/api/v1/utils/health-check/" >/dev/null
    fi
}
