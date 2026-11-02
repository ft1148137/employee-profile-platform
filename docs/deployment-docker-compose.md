# Docker Compose 部署

本地体验在仓库根目录执行：

```bash
cp .env.example .env
docker compose up -d --wait
```

打开 <http://localhost:8000>，使用 `.env` 中的初始管理员账号登录。首次启动会构建包含前端的镜像，自动迁移并创建初始账号。`db`、`backend`、`worker` 应为 healthy，`prestart` 应以 0 退出。数据库不发布宿主机端口。

可在 `.env` 中设置 `API_BIND`、`API_PORT`，默认监听 `127.0.0.1:8000`。日常停止用 `docker compose down`，保留数据库卷；`down -v` 会删除数据，只用于可丢弃的开发环境。

示例配置启用 development 并使用默认密钥，仅用于本地。域名和 HTTPS 部署见 [deploy/README.md](../deploy/README.md)；对外部署前必须替换默认密钥并移除 `FASTAPI_ENV=development`。

开发服务和热重载见 [开发指南](development.md)，需要显式加载 `compose.dev.yml`。
