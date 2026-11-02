# 安装、备份与升级

需要 Git、curl、Docker 和 Docker Compose 2.24.4+，兼容 macOS 自带 bash 3.2 和 Linux。在仓库根目录执行：

```bash
./install.sh
# 无交互安装，默认选择本机 IPv4 和 HTTP
EPP_ADDRESS=192.168.1.10 EPP_HTTP_PORT=8080 ./install.sh --non-interactive
# 独立演示安装（在另一个全新克隆目录执行）
COMPOSE_PROJECT_NAME=epp-demo EPP_HTTP_PORT=18080 ./install.sh --demo --non-interactive
```

安装器询问访问地址、访问方式和 HTTP 端口，生成权限 600 的 `.env` 和随机密钥，然后构建并启动服务。生产安装不导入演示数据、不创建默认超管；首次设置链接由 M1-S7 补充。演示安装打印演示账号和随机密码，也不会启用 `/private` 开发接口。

重跑安装器保留已有 `.env` 的所有内容，不更换密钥。`--demo` 只在首次生成 `.env` 时生效。请保护 `.env` 和备份，勿提交或发到群里。开发仍用 `.env.example`、`compose.yml` 和可选的 `compose.dev.yml`，默认监听 `127.0.0.1:8000`。

无交互参数：`EPP_ADDRESS`、`EPP_ACCESS`（`http`、`https-selfsigned`、`https-letsencrypt`）、`EPP_HTTP_PORT`、`EPP_ACME_EMAIL` 和 `COMPOSE_PROJECT_NAME`。项目名和端口可以隔离同机安装。生成的 `COMPOSE_FILE` 让后续所有 `docker compose` 命令自动使用相同配置；不要在终端导出另一套 `COMPOSE_FILE` 或数据库密码覆盖它。

## HTTPS

- HTTP 默认监听所有网卡，可供局域网访问。防火墙需允许选定端口。
- 自签 HTTPS：选择方式 2 或 `EPP_ACCESS=https-selfsigned`，Traefik 提供默认证书，浏览器会提示不安全。HTTPS 固定使用 443；选定 HTTP 端口重定向到 HTTPS。
- 域名证书：选择方式 3，设置域名、证书通知邮箱，公网 DNS 指向服务器并开放 80/443。Let's Encrypt 签发依赖公网条件，本地自签验证不能证明签发成功。

HTTPS 配置移除 backend 的宿主机端口，只由 Traefik 暴露入口。Docker socket 以只读方式供 Traefik 发现服务；不开放其管理面板。域名证书保存在独立卷中，日常停止请勿加 `-v`。

## 备份与恢复

```bash
./backup.sh
./restore.sh backups/app-20261102-020000.dump
```

备份容器每天按 `.env` 中 `TZ=Asia/Shanghai`、`BACKUP_TIME=02:00` 运行，默认 `BACKUP_KEEP=14`。文件在仓库 `backups/`（目录权限 700、文件 600），用 `pg_dump -Fc` 写 `.partial`，成功后改名并保留最新份数。手动备份使用相同保留策略。失败不会删除已有备份，错误可用 `docker compose logs backup` 查看。测试可设置 `BACKUP_INTERVAL_SECONDS=5` 并重新创建 backup 服务。

恢复前校验备份格式，输入「确认恢复」才继续；停止应用、Worker 和自动备份，在事务中恢复，重新执行迁移并启动服务。**不能将新版本备份恢复到旧代码。** 恢复失败时应用保持停止，需要排查后重新执行。恢复会覆盖当前数据，请先另存当前备份。

备份包含受限证据原文，当前不提供加密和异地备份，由部署者负责保管和转存。此脚本只备份数据库，不备份 `.env` 和证书卷，二者也需单独保管。

## 升级

```bash
./upgrade.sh          # 当前分支 git pull --ff-only
./upgrade.sh v0.2.0   # fetch 后切到指定 ref（detached HEAD）
docker compose down  # 停止，保留数据
```

升级要求工作区无未提交改动，依次获取代码、备份、构建和启动，迁移由 prestart 执行。失败会打印原提交和本次备份的恢复命令。回退必须同时恢复升级前的数据库，不能仅切代码。未生成备份时不会执行迁移。指定 ref 后的下次升级也应显式传 ref，或先切回跟踪远端的分支。
