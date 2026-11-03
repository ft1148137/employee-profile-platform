# 安装与维护入口

生产安装请从仓库根目录执行 `./install.sh`，不要先复制演示用 `.env.example`。

- [部署、备份与维护](../docs/deploy.md)：环境要求、安装问题、HTTP/HTTPS、初始化链接、局域网、防火墙、备份恢复、升级回退、密码救援、停止与卸载。
- [企业管理员指南](../docs/admin-guide.md)：只用浏览器完成首次设置和日常管理。
- [M1 验收记录](../docs/m1-acceptance.md)：本机与 CI 验证边界，Let's Encrypt 未实测。

本目录的 `compose.prod.yml` 关闭开发模式；`compose.https-selfsigned.yml` 与 `compose.https-letsencrypt.yml` 提供两种 HTTPS 入口。安装器把所需叠加文件写入 `.env` 的 `COMPOSE_FILE`。不要直接把演示配置用于生产。

日常停止用 `docker compose down`，**不要加 `-v`**。保留原目录、`.env` 和项目名，之后可执行 `docker compose up -d --wait` 恢复运行。
