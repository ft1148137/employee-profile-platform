这里的公司、人名、邮箱、Jira 身份和任务全部虚构，不包含真实员工资料。默认面向技术研发团队（D1），Jira Cloud v3 子集和三级能力标准是 M0 的可逆示例（D2、D3），不代表考核结论。信心标签不是概率，缺少记录保持未知。

从仓库根目录复制 `.env.example` 为 `.env`，执行 `docker compose up -d --wait`。开发模式且 `SEED_DEMO_DATA=true` 时，prestart 在迁移和初始账号之后自动导入。生产配置应关闭演示数据。

手动导入：在 `backend/` 下执行 `uv run python -m app.seed load`；可通过 `--path DIR` 指定数据目录，默认由代码位置定位到本目录。能力模板位于数据目录的 `../../templates/capabilities/tech-rnd.zh-CN.yaml`。非开发模式默认拒绝，确需演示时显式传 `--allow-non-dev`。不调用外部模型或 Jira API。

| 邮箱 | 身份 | 员工 |
| --- | --- | --- |
| `admin@demo.example.com` | 企业管理员 | 无员工关联 |
| `manager.platform@demo.example.com` | 平台研发部经理、员工 | E01 林澈 |
| `employee.platform@demo.example.com` | 员工 | E02 张伟 |
| `employee.algorithm@demo.example.com` | 员工 | E05 张伟 |
| `.env` 的 `FIRST_SUPERUSER`（默认 `admin@example.com`） | 平台超管，额外授予 demo 企业管理员 | 无员工关联 |

前四个账号使用 `.env` 的 `DEMO_USER_PASSWORD`；初始超管保留 `FIRST_SUPERUSER_PASSWORD`。其余员工没有登录账号。重复导入不会改密码或覆盖既有数据。

| 文件 | 格式与引用 |
| --- | --- |
| `org.yaml` | 组织 slug/name、部门列表、员工（employee_code、department、display_name、title、work_email、account_id）、账号（email、employee_code?、role?） |
| `jira/issues.json` | Jira Cloud REST v3 search 子集，顶层 issues 列表；fields 包含 summary、issuetype、status、assignee/reporter、created/resolutiondate、labels、components、纯文本 description |
| `self_reports.yaml` | id、employee_code、submitted_at、text、related_task_keys 列表 |
| `manager_reviews.yaml` | id、employee_code、reviewer_employee_code、capability_key、question、answer、comment、reviewed_at |
| `claims.yaml` | 扁平列表，每条有员工编号、id、capability_key、statement、level?、assessment_status、review_status、confidence、explanation、supersedes?、evidence（ref/relation） |
| `profile_versions.yaml` | 员工编号、revision、published_at、published_by（演示账号邮箱）、claims（本地 id 列表） |
| `progress.yaml` | 每位员工的 fill_status、inference_status、review_status、evidence_quality |
| `../../templates/capabilities/tech-rnd.zh-CN.yaml` | 有序能力列表：key/name/category/description/level_rubric；levels 列表从低到高，key 为 needs_support、routine、independent_complex |

证据引用形式为 `jira:PLAT-101`、`self_report:SR01`、`manager_review:MR01`。更正 Claim 必须先列出旧 Claim，再通过 supersedes 引用；发布版本按 revision 递增排列。等级可空，经理的 not_sure 不映射为等级。

共 4 个部门、12 名员工、12 项能力；37 条 Jira 样例中 36 条导入，`PLAT-999` 因 accountId 未关联而跳过；10 条自述和 8 条经理评价默认 restricted。E02、E05 同名但按 accountId 分开；E08 的自述和经理意见冲突；E12 没有 Claim；5 人有首版画像，E02 有第二版并替代旧 Claim。快照只含判断字段和证据元数据，不复制证据正文。

所有文件在事务开始前经 Pydantic 校验，错误包含文件名与字段路径。自然键保证重复执行不新增行；输出各表 added/existing 数量和 skipped_jira_issues。首次成功导入留一条 `seed.loaded` 审计收据，重复执行保留收据；证据只经 append_evidence 追加。样例文件是初始数据，编辑既有文件不会更新旧对象；请使用新的证据 id / Claim id / revision 表达后续变化。
