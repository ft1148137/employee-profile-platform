# 连接器

连接器代码位于 `backend/app/connectors/`，随后端镜像交付。

模拟 Jira 位于 `app.connectors.jira.mock`，开发 Compose 和 `install.sh --demo` 默认启动。
每个数据集有独立地址：`http://mock-jira:8080/demo-org`、`/qingli-saas`、`/qiguang-aiot`。
Cloud 使用 `/rest/api/3/search/jql` 和 Basic 邮箱/API token；Data Center 使用
`/rest/api/2/search` 和 Bearer PAT 或 Basic 用户名/密码。默认凭据见 `.env.example`。

测试可调用 `create_mock_app(examples_dir)`，启动时加载数据；修改临时数据集后重新创建应用即可模拟增量。
仅支持项目、更新时间和升序排序的 JQL 子集，不提供评论、工时或附件。
