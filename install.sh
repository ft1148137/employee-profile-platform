#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=deploy/common.sh
source deploy/common.sh

demo=false
interactive=true
for argument in "$@"; do
    case "$argument" in
        --demo) demo=true ;;
        --non-interactive) interactive=false ;;
        --help)
            echo '用法：./install.sh [--demo] [--non-interactive]'
            echo '环境变量：EPP_ADDRESS、EPP_ACCESS=http|https-selfsigned|https-letsencrypt、EPP_HTTP_PORT、EPP_ACME_EMAIL、COMPOSE_PROJECT_NAME'
            exit 0 ;;
        *) echo "未知参数：$argument" >&2; exit 1 ;;
    esac
done
for command in docker git curl; do
    command -v "$command" >/dev/null || fail "请先安装 $command"
done
version=$(docker compose version --short)
version=${version#v}
IFS=. read -r major minor patch <<< "${version%%-*}"
if (( major < 2 || (major == 2 && minor < 24) || (major == 2 && minor == 24 && patch < 4) )); then
    fail '需要 Docker Compose 2.24.4 或以上版本'
fi
docker info >/dev/null

random_secret() {
    if command -v openssl >/dev/null; then
        openssl rand -hex 32
    else
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    fi
}
default_address() {
    if command -v ipconfig >/dev/null; then
        ipconfig getifaddr en0 || ipconfig getifaddr en1 || echo localhost
    elif command -v hostname >/dev/null; then
        hostname -I 2>/dev/null | awk '{print $1}'
    fi
}
port_busy() {
    (echo >"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1 ||
        { command -v lsof >/dev/null && lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; } ||
        { command -v ss >/dev/null && ss -H -ltn "sport = :$1" | read -r _; }
}
if [[ ! -e .env ]]; then
    address=${EPP_ADDRESS:-$(default_address)}
    address=${address:-localhost}
    access=${EPP_ACCESS:-http}
    port=${EPP_HTTP_PORT:-80}
    email=${EPP_ACME_EMAIL:-}
    project=${COMPOSE_PROJECT_NAME:-employee-profile}
    if "$interactive"; then
        read -r -p "访问地址（IP 或域名）[$address]：" answer
        address=${answer:-$address}
        echo '1 HTTP（默认）；2 HTTPS 自签（浏览器提示不安全）；3 HTTPS 域名证书（需要公网 DNS 和开放 80/443）'
        read -r -p '访问方式 [1]：' answer
        case "${answer:-1}" in
            1) access=http ;; 2) access=https-selfsigned ;; 3) access=https-letsencrypt ;;
            *) fail '访问方式无效' ;;
        esac
        if [[ "$access" == https-letsencrypt ]]; then
            read -r -p '证书通知邮箱：' email
        fi
        read -r -p "HTTP 端口 [$port]：" answer
        port=${answer:-$port}
    fi
    [[ "$address" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || fail '地址只能是 IPv4 或域名（不含协议、路径和端口）'
    [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail 'Compose 项目名格式无效'
    if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then fail '端口必须为 1–65535'; fi
    case "$access" in
        http|https-selfsigned|https-letsencrypt) ;;
        *) fail 'EPP_ACCESS 无效' ;;
    esac
    if [[ "$access" == https-letsencrypt ]]; then
        [[ "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || fail '请输入有效的证书通知邮箱'
        [[ ! "$address" =~ ^[0-9.]+$ ]] || fail "Let's Encrypt 需要域名"
    fi
    while port_busy "$port"; do
        "$interactive" || fail "端口 $port 已占用，请设置 EPP_HTTP_PORT（例如 8080）"
        read -r -p "端口 $port 已占用，请换一个（例如 8080）：" port
        if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then fail '端口无效'; fi
    done
    files=compose.yml:deploy/compose.prod.yml
    host="http://$address"
    if [[ "$access" == http ]]; then
        [[ "$port" == 80 ]] || host="$host:$port"
    else
        port_busy 443 && fail 'HTTPS 端口 443 已占用'
        files="$files:deploy/compose.$access.yml"
        host="https://$address"
    fi
    umask 077
    env_temp=$(mktemp .env.XXXXXX)
    trap 'rm -f "$env_temp"' EXIT
    secret_key=$(random_secret)
    database_password=$(random_secret)
    cat > "$env_temp" <<ENV
PROJECT_NAME="Employee Profile Platform"
SECRET_KEY=$secret_key
# 登录限流：NAT、Docker Desktop、反向代理后面的所有人可能共用一个 IP，按人数调整 LOGIN_MAX_FAILURES_PER_IP
LOGIN_MAX_FAILURES=5
LOGIN_MAX_FAILURES_PER_IP=300
LOGIN_LOCK_MINUTES=15
POSTGRES_PASSWORD=$database_password
FRONTEND_HOST=$host
DOMAIN=$address
ACME_EMAIL=$email
HTTP_PORT=$port
API_BIND=0.0.0.0
SEED_DEMO_DATA=$demo
COMPOSE_PROJECT_NAME=$project
COMPOSE_FILE=$files
TZ=Asia/Shanghai
BACKUP_TIME=02:00
BACKUP_KEEP=14
ENV
    if "$demo"; then
        demo_password=$(random_secret)
        echo "DEMO_USER_PASSWORD=$demo_password" >> "$env_temp"
    fi
    # A concurrent installer must not replace an existing configuration.
    ln "$env_temp" .env || fail '.env 已存在，请重新运行安装'
    rm "$env_temp"
    trap - EXIT
else
    echo '保留已有 .env 和密钥，使用现有配置启动。'
fi
mkdir -p backups
chmod 700 backups
docker compose build
docker compose up -d --wait
check_health
echo "访问地址：$(config_value FRONTEND_HOST)"
echo "配置：$PWD/.env；备份：$PWD/backups"
echo '常用命令：./backup.sh；./restore.sh <备份文件>；./upgrade.sh [git ref]；docker compose down'
if [[ "$(config_value SEED_DEMO_DATA)" == true ]]; then
    echo "演示账号：employee.platform@demo.example.com；密码：$(config_value DEMO_USER_PASSWORD)"
elif setup_link=$(docker compose exec -T backend python -m app.cli setup-link --base-url "$(config_value FRONTEND_HOST)"); then
    echo "首次设置链接：$setup_link"
    echo '72 小时内有效，只能使用一次；过期后重新运行 ./install.sh 获取新链接'
else
    setup_exit=$?
    if [[ "$setup_exit" == 2 ]]; then
        echo '企业已初始化，请用管理员账号登录'
    else
        exit "$setup_exit"
    fi
fi
