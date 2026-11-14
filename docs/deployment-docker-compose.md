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

## M5 升级与 Worker 健康

已有部署使用安装目录内的 `./upgrade.sh origin/master`，保留原 `.env`、项目名和数据卷。prestart 从 M4.7 起依次执行三个迁移：

1. `b615001dc001`：部门能力生成失败时间，失败至少隔 24 小时重试。
2. `b617001dc001`：企业时区，已有企业默认 `Asia/Shanghai`。
3. `b622001dc001`：共享 Worker 的单行心跳状态。

迁移链、备份和回退影响见 [M5 升级说明](deploy.md#m5-升级说明)。先让 prestart 成功，再检查应用与 worker；不要手工跳过迁移或删除数据卷重装。

Worker 每分钟写一次心跳；健康检查在 Procrastinate 原有检查之后运行 `python -m app.worker.health`，无心跳、超过五分钟未更新或读库失败都会失败。`start_period` 为 120 秒，检查间隔十秒、超时五秒、重试五次。启动时等第一轮心跳，不要把短暂未健康当作升级失败。

运行中异常时，管理员在管理助手「后台任务」卡查看说明，IM 进程独立生成收件箱和飞书提醒（须满足绑定与通知条件）。维护人员先运行 `docker compose ps`、查看 `docker compose logs --tail=100 worker`，排除原因后 `docker compose restart worker`，再核对心跳恢复。unhealthy 本身不会触发自动重启。

完整步骤、暂停容器的处理及提醒限制见[后台任务心跳与排障](deploy.md#后台任务心跳与排障)。这些命令须在自己的部署目录和项目名下执行。
