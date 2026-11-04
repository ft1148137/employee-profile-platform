青栎云（模拟）是一家虚构的 SaaS 厂商，产品面向中小制造企业的采购协同，slug 为 `qingli-saas`。行业归类按 GB/T 4754 为门类 I「信息传输、软件和信息技术服务业」、小类 `6550` 信息处理和存储支持服务。公司、人员、邮箱、账号、客户和任务全部虚构，不包含任何真实企业或个人信息。

**测试用数据，不自动导入系统。** 本目录不是 seed 数据源，`backend/app/seed` 不读取它。只有 `examples/demo-org/` 会被导入。`personas.yaml` 里的「真实能力」是用来核对系统推断结果的参考答案，任何情况下都不要导入。

## 组织形态

矩阵制。职能部门管人、管职级、管招聘；产品线小组管事、管排期。产品线小组由产品经理牵头并担任项目经理，他们是产品与设计部的普通员工，不是任何部门的负责人。研发、测试、实施人员的第二汇报人指向所在产品线的项目经理，形成「部门经理 + 项目经理」两条线。两个产品线小组是 PUR（采购协同 2.0，项目经理 QL010 欧阳岑）和 SUP（供应商门户，项目经理 QL011 苏晚）。

组织规模：5 个部门，28 人（在职 26 人，离职 2 人）。在职人员每个部门恰好 1 名负责人，另有至少 3 名员工。

## 部门

| 部门 | APQC PCF v8.0 一级类别 | 职责 | 负责人 | 在职人数 |
| --- | --- | --- | --- | --- |
| 平台研发部 | 2.0 Develop and Manage Products and Services；8.0 Manage Information Technology (IT) | 后端、前端与基础设施开发，多租户隔离与数据库演进 | QL001 姜柏然 | 7 |
| 产品与设计部 | 1.0 Develop Vision and Strategy；2.0 Develop and Manage Products and Services | 需求调研、产品方案与验收标准、交互设计与设计规范 | QL009 宁致远 | 4 |
| 质量与运维部 | 2.0 Develop and Manage Products and Services；8.0 Manage Information Technology (IT) | 测试设计与自动化、版本发布与回滚、监控与故障处理 | QL013 郑亦舒 | 6 |
| 客户成功部 | 5.0 Deliver Services；6.0 Manage Customer Service | 客户上线实施、历史数据迁移、培训交付、报障受理 | QL018 骆程 | 5 |
| 人力与信息部 | 7.0 Develop and Manage Human Capital；8.0 Manage Information Technology (IT) | 招聘与人事档案、内部 IT 与账号权限、数据合规配置 | QL024 冯屹 | 4 |

职级体系为 P5–P8，外包按供应商职级写作「外包 T3」，实习生写作「实习生」。

## 数据来源

| 来源 | kind | 文件 | 内容 | 数量 |
| --- | --- | --- | --- | --- |
| Jira | jira | `jira/issues.json` | Jira Cloud REST v3 search 子集，项目 PUR 与 SUP | 38 条 issue |
| Git | work_records | `work_records.csv` | 自建 Git 平台的合并请求、代码评审、发布与流水线提交 | 40 条记录 |

能力词典：`templates/capabilities/saas-software.zh-CN.yaml`，16 项、5 个 category（产品与需求、工程开发、质量工程、运维与安全、客户与实施）。

时间范围：Jira 与 Git 记录落在 2025-11-01 至 2026-10-31；自述 `submitted_at` 在 2026-09-08 至 2026-10-28；经理评价 `reviewed_at` 在 2026-10-08 至 2026-10-29。时区统一 `+08:00`（Jira 按其惯例写 `+0800`）。自述 16 条，经理评价 18 条。

## `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，首行表头固定为 12 列，顺序如下。

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一的记录编号，本企业统一用 `G-0001` 到 `G-0040`，与 Jira key 不重复 |
| `source` | 来源名，固定为 `Git`，对应 `enterprise.yaml` 中 kind 为 work_records 的来源 |
| `record_type` | 记录类型：合并请求、代码评审、发布、提交 |
| `occurred_at` | 记录发生时间，ISO 8601 带 `+08:00` |
| `actor_account` | Git 平台里的执行人账号，可能是未在 HR 登记的账号（见 `personas.yaml` 的 `account_truth`） |
| `actor_name` | Git 平台里显示的名字，可能与花名册不同，例如 `Chloe`、`小满`、`Mo Chen` |
| `actor_role` | 执行人在这条记录里的角色：作者、评审人、发布负责人、流水线执行 |
| `participants` | 其他参与者，格式为 `账号:角色`，多个用 `;` 分隔，可为空 |
| `project` | 产品线代号 `PUR` 或 `SUP`，可为空 |
| `title` | 记录标题 |
| `detail` | 一两句纯文本说明，写明对应的 Jira 任务和实际分工，不含任何隐私信息 |
| `status` | Git 平台里的状态：已合并、已完成、待评审 |

## 登录账号

每位在职员工恰好一个登录账号，邮箱等于其 `work_email`（域名 `@qingli-saas.example.com`）。离职人员不建账号。

| 邮箱 | 系统角色 | 员工 |
| --- | --- | --- |
| `ql025@qingli-saas.example.com` | org_admin（企业管理员） | QL025 蒋柚，人力与信息部信息系统管理员 |
| `ql001@qingli-saas.example.com` | dept_manager | QL001 姜柏然，平台研发部 |
| `ql009@qingli-saas.example.com` | dept_manager | QL009 宁致远，产品与设计部 |
| `ql013@qingli-saas.example.com` | dept_manager | QL013 郑亦舒，质量与运维部 |
| `ql018@qingli-saas.example.com` | dept_manager | QL018 骆程，客户成功部 |
| `ql024@qingli-saas.example.com` | dept_manager | QL024 冯屹，人力与信息部 |
| 其余 20 个在职员工邮箱 | employee（不写 role） | QL002–QL007、QL010–QL012、QL014–QL017、QL019–QL022、QL026–QL028 |

企业管理员是人力与信息部的普通员工，不是部门负责人；两位项目经理（QL010、QL011）也只有员工角色。

## 本企业的边界情况

| tag | 涉及人员 | 数据上怎么体现 |
| --- | --- | --- |
| same_name | QL002、QL014 | 两位王浩分属平台研发部与质量与运维部，Jira 账号 `ql-jira-02` / `ql-jira-14`，Git 账号 `wang.hao` / `wang.hao.qa`；MR05 里经理明确说分不清 |
| multi_account | QL004、QL016 | 赵清越三个账号（`ql-jira-04`、`chloe.z`、未登记的 `qingyue.z`）；莫晨两个（`ql-jira-16`、未登记的 `mochen-vendor`） |
| unlinked_account | 无（非人账号） | `ci-bot` 在 G-0002、G-0013、G-0033 出现，不属于任何员工 |
| departed | QL008、QL023 | 陶砚秋 2026-06-30 离职、施屿 2026-04-30 离职，无登录账号但有历史记录 |
| contractor | QL016 | 外包测试工程师，提交走供应商 Git 账号 |
| intern | QL028 | 测试实习生 2026-07-06 入职，产出集中在脚本实现 |
| transfer | QL006 | 2026-03-01 从质量与运维部测试工程师转岗平台研发部后端工程师 |
| concurrent_post | QL021 | 客户成功经理兼产品与设计部，两条汇报线 |
| new_hire_no_history | QL007 | 2026-09-14 入职，不出现在任何 Jira、Git 记录和自述里 |
| assignee_mismatch | QL003、QL006、QL017、QL028 | PUR-212 指派沈砚、实际傅京做（G-0015）；PUR-215 指派简宁、脚本主体实习生写（G-0017） |
| cross_source_duplicate | QL003、QL004 | PUR-204 与 G-0003、SUP-305 与 G-0026 是同一件事的两条记录 |
| review_conflict | QL006、QL022 | 傅京与殷岚的自述和经理评价方向相反 |
| appeal | QL006、QL017 | 两人都认为经理评价漏看了关键证据，测试时会提申诉 |
| manager_unsure | QL002、QL015 | MR05、MR07 两条 `answer: not_sure` |
| compound_surname | QL010 | 欧阳岑 |
| english_name | QL004 | Jira 显示名 `Chloe Zhao`、Git 显示名 `Chloe`，与中文名并存 |
| pm_not_dept_manager | QL010、QL011 | 两位项目经理都不是部门负责人 |

## 证据与真实能力之间的差距（刻意设计）

真实能力分布不是「人人记录充分」。本企业共 74 条 ground truth 能力项，其中记录充分（documented）22 条、记录不足（partial）18 条、完全无记录（undocumented）29 条、记录误导（misleading）5 条。主要差距来自五类设计：

- **指派与贡献错位。** PUR-212、PUR-215 两处任务的指派人和实际完成人不同，连带造成沈砚被高估、傅京与简宁被低估。
- **经理印象滞后或过宽。** MR04 用转岗初期的印象评价现在的傅京；MR03 用「基础设施都是他」的印象给温亦然打了偏高的一档；MR13 停留在殷岚入职首月。
- **自述夸大。** SR13 把常规水平写成能独立处理复杂跨系统排障。
- **证书不等于能力，且证书本身可能失效。** 温亦然的 CKA 已于 2025-08 过期未复认证，蒋柚的 ISO 27001 内审员证书 2026-05 过期，两张都还留在 HR 档案里。
- **记录本身不存在。** 管理岗的决策留在会议里（QL001、QL009、QL013、QL018）；人力岗完全没有来源系统覆盖（QL026、QL027）；转岗前的工作没有痕迹（QL006）；新人入职不满两个月（QL007）。这些情况系统必须输出「未知」，不得推断为「不会」。

## 校验

从仓库根目录执行：

```
uv run --project backend python examples/test-enterprises/validate.py qingli-saas
```
