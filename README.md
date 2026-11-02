# 企业员工画像与跨部门协同平台

开源、可自托管的员工资源画像平台，让跨部门协同基于可信能力与事实。Phase 1 聚焦：做过什么、能承担哪些工作、证据是什么、哪里需要确认。

基于 Full Stack FastAPI Template，使用 FastAPI、React、PostgreSQL 和 Docker Compose。当前为 M0 项目底座，不含业务画像功能；不做绩效打分或自动排班。

从 `cp .env.example .env` 开始，替换示例密钥和密码，再按开发指南启动。不要提交 `.env`。

- [文档索引](docs/README.md)
- [开发指南](docs/development.md)
- [部署指南](docs/deployment-docker-compose.md)
- [后端](backend/README.md) / [前端](frontend/README.md)
- [架构决策记录](docs/adr/README.md)

正式 README 和新用户验收指南将在 M0-S9 补充。保留上游 [MIT 许可证](LICENSE)，发布前在 M4 核验许可证。
