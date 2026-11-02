# 域名与 HTTPS 部署

默认入口 `compose.yml` 只提供本地回环地址上的 Web/API、Worker 和 PostgreSQL；`compose.dev.yml` 仅用于开发。

`deploy/compose.traefik.yml` 为默认入口增加 Traefik、HTTPS、证书卷和长期服务的重启策略。需要 Docker Compose 2.24.4+（使用 `!reset` 移除 API 宿主机端口）、域名 DNS 指向服务器，以及开放的 80/443 端口。此部署文件本任务未做实际域名和证书验证。

在仓库根目录复制 `.env.example` 到 `.env`，然后：

- **替换 `SECRET_KEY`、`POSTGRES_PASSWORD`、`FIRST_SUPERUSER_PASSWORD` 的默认值**，使用独立随机密钥。
- **删除 `FASTAPI_ENV=development` 那一行**，并确保 shell 里也未设置；生产配置会拒绝默认密钥。不要写 `FASTAPI_ENV=`。
- 设置 `DOMAIN=profiles.example.com`、`ACME_EMAIL=ops@example.com`（替换为自己的域名和证书通知邮箱）、初始管理员邮箱和需要的 SMTP 参数。数据库密码建议使用 URL 安全字符，例如 `secrets.token_urlsafe(32)` 的输出。
- 不要提交 `.env`，不要把开发用 `COMPOSE_FILE` 写进去。

```bash
docker compose -f compose.yml -f deploy/compose.traefik.yml build
docker compose -f compose.yml -f deploy/compose.traefik.yml up -d --wait
```

访问 `https://profiles.example.com`，API 文档为 `/docs`。迁移由 `prestart` 自动执行，backend 和 worker 等待其成功。API 不单独发布宿主机端口；数据库也不发布端口。此配置不提供 Adminer 或 Traefik 管理面板。

Traefik 只读取明确启用的服务标签；Docker socket 以只读挂载供发现服务使用，仅在此可选部署文件中启用。升级前自行备份数据库和证书卷，停止时不加 `-v`：

```bash
docker compose -f compose.yml -f deploy/compose.traefik.yml down
```
