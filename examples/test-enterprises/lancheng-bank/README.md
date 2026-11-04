## 岚城银行汀州分行（模拟）

合成测试数据，企业、人员、邮箱、客户编号全部虚构，不含任何真实企业、员工或客户信息。文件**仅用于系统测试，不会被 `app.seed` 自动导入**（seed 加载器只读 `examples/demo-org/`）。本目录按设计不含 `claims.yaml`、`profile_versions.yaml`、`progress.yaml`，画像结论应由系统自己推断，再与 `personas.yaml` 的真实能力对照。

岚城银行是一家虚构的城市商业银行，汀州分行是其下辖的二级分行，管辖若干支行。本数据集只取分行本部四个条线部门、一个支行和综合管理部，共 6 个部门、29 名员工（在职 27 人、离职 2 人）。行业分类按 GB/T 4754 归入门类 J 金融业，小类 6621 商业银行服务。

### 组织形态（matrix，条线 + 分支）

支行按属地管人，分行条线管业务口径，因此支行人员普遍是双线汇报：

- 支行客户经理的 `reports_to` 是云溪支行行长，`secondary_reports_to` 指向分行公司金融部或零售金融部负责人。
- 支行柜员的 `reports_to` 是支行行长，`secondary_reports_to` 指向运营管理部负责人（运营条线）。
- 风险合规部的合规管理岗兼任云溪支行合规员（`secondary_departments` 为云溪支行），同时向风险合规部负责人和支行行长报告。
- 三个项目的负责人与部门负责人不是同一套人：零售产品经理（LC011）任普惠小微线上化项目经理但不是部门负责人；反洗钱专项由反洗钱岗普通员工（LC019）牵头；云溪支行网点转型项目由支行行长（LC024）牵头，他同时是部门负责人。

与规格建议的部门设置一致，未作调整。规格建议 22–28 人，本企业 29 人（在职 27），多出一人是因为风险合规部需要同时覆盖审查、贷后、反洗钱、合规检查四类岗位，且每个部门要满足「1 名在职负责人 + 至少 3 名在职员工」。

### 部门

| 部门 | 负责人 | 在职人数 | 职责 | PCF 一级类别 |
| --- | --- | --- | --- | --- |
| 公司金融部 | LC005 傅承砚 | 5 | 对公客户经营、贷前调查组织、授信申报与条线口径 | 3.0 Market and Sell Products and Services；5.0 Deliver Services |
| 零售金融部 | LC010 韦岑 | 4 | 个贷与财富产品管理、零售条线指导、代销适当性管理 | 3.0 Market and Sell Products and Services；2.0 Develop and Manage Products and Services |
| 风险合规部 | LC015 邵砚秋 | 5 | 信贷审查、贷后管理、反洗钱、合规检查与整改跟踪 | 11.0 Manage Enterprise Risk, Compliance, Remediation, and Resiliency |
| 运营管理部 | LC020 郁泓 | 4 | 柜面运营管理、集中授权、事后监督、差错与现金管理 | 5.0 Deliver Services；6.0 Manage Customer Service |
| 云溪支行 | LC024 聂行远 | 5 | 属地网点的对公与零售经营、柜面服务、网点转型 | 5.0 Deliver Services；3.0 Market and Sell Products and Services |
| 综合管理部 | LC001 蒋允平 | 4 | 人力与培训管理、信息科技运维、行政与安保 | 7.0 Develop and Manage Human Capital；8.0 Manage Information Technology (IT) |

离职的 LC014（零售金融部）和 LC029（云溪支行）不计入在职人数。职级体系用经理序列（经理 / 高级经理 / 资深经理）和柜员序列（柜员 A/B/C 级）。

### 数据来源

本企业不使用 Jira，三个来源全部写入 `work_records.csv`：

| 来源 | kind | record_id 前缀 | 内容 |
| --- | --- | --- | --- |
| 信贷系统 | work_records | `CR-` | 贷前调查、授信审查意见、授信方案、押品复估、贷后检查、风险预警处置、反洗钱尽调、系统参数变更、合规检查 |
| 柜面运营 | work_records | `OP-` | 柜面业务办理、授权记录、业务差错、事后监督、现金清点、重要空白凭证管理、合规检查整改 |
| 培训平台 | work_records | `TR-` | 反洗钱年度培训、合规考试、新产品与岗前培训、师带徒、培训组织 |

共 68 条记录（信贷系统 25、柜面运营 20、培训平台 23），时间落在 2025-11-01 至 2026-10-31，时区一律 `+08:00`。`org.yaml` 的 `account_id` 是全行统一工号（培训平台用它登录），信贷系统账号和柜员号登记在 `roster.yaml` 的 `external_accounts` 里；另有 4 个账号出现在记录中但没有登记，见 `personas.yaml` 的 `account_truth`。

记录里的客户只用合成编号（如 `DG-2026-0137`、`GR-2026-0415`），不含客户名称、账号、证件号或联系方式。

### `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，首行表头固定为 12 列：

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一编号，按来源分为 `CR-`、`OP-`、`TR-` 三段 |
| `source` | 来源名，取 `enterprise.yaml` 中 `kind: work_records` 的 `sources[].name`（信贷系统 / 柜面运营 / 培训平台） |
| `record_type` | 记录类型，如 贷前调查、授信审查意见、贷后检查、授权记录、业务差错、事后监督、合规考试 |
| `occurred_at` | 发生时间，ISO 8601 带 `+08:00` |
| `actor_account` | 来源系统里的执行人账号，可能是未登记账号（见 `account_truth`） |
| `actor_name` | 来源系统里显示的名字，可能与花名册不同（如「云溪临柜终端」「外部讲师（受邀）」） |
| `actor_role` | 执行人在本条记录里的角色，如 调查人、审查人、授权人、经办柜员、主办柜员、监督人、主讲人、考生 |
| `participants` | 其他参与者，`账号:角色` 用 `;` 分隔，可为空 |
| `project` | 项目编号（XWD / AML / YXZQ），可为空 |
| `title` | 标题 |
| `detail` | 一两句纯文本描述，不含客户隐私 |
| `status` | 来源系统里的状态，如 已完成、已整改、已退回、已关闭、已通过 |

### 登录账号

在职 27 人各有 1 个登录账号，邮箱与 `work_email` 相同，域名统一为 `@lancheng-bank.example.com`；离职人员没有登录账号。

| 邮箱 | 系统角色 | 员工 | 说明 |
| --- | --- | --- | --- |
| `lc002@lancheng-bank.example.com` | org_admin | LC002 林漪 | 综合管理部（人力+科技）普通员工，不是部门负责人 |
| `lc001@lancheng-bank.example.com` | dept_manager | LC001 蒋允平 | 综合管理部负责人 |
| `lc005@lancheng-bank.example.com` | dept_manager | LC005 傅承砚 | 公司金融部负责人 |
| `lc010@lancheng-bank.example.com` | dept_manager | LC010 韦岑 | 零售金融部负责人 |
| `lc015@lancheng-bank.example.com` | dept_manager | LC015 邵砚秋 | 风险合规部负责人 |
| `lc020@lancheng-bank.example.com` | dept_manager | LC020 郁泓 | 运营管理部负责人 |
| `lc024@lancheng-bank.example.com` | dept_manager | LC024 聂行远 | 云溪支行行长，兼网点转型项目负责人 |

其余 20 名在职员工是普通员工账号（不写 role）。

### 本企业的边界情况

| tag | 涉及员工 | 数据上怎么体现 |
| --- | --- | --- |
| multi_account | LC013、LC026、LC027 | 童昱有统一工号 + 未登记的信贷账号；柳沅有统一工号 + 信贷账号 + 转岗前柜员号 T0207；秦望有统一工号 + 柜员号 T0231 + 未登记旧柜员号 T0177 |
| transfer | LC026 | 2026-01-05 从运营管理部主办柜员转为云溪支行零售客户经理，转岗前后的记录分在不同账号下 |
| concurrent_post | LC016 | 合规管理岗兼云溪支行合规员，`secondary_departments` 为云溪支行，双线报告 |
| new_hire_no_history | LC009 | 2026-09-14 入职，账号已开但无任何记录、无自述，经理只能答 not_sure |
| review_conflict | LC013、LC026 | 一例自述高于经理评价，一例自述低于经理评价，方向相反 |
| manager_unsure | LC018、LC009、LC025 | 三条 `not_sure` 评价，成因分别是分不清主导者、新人无记录、双线汇报见面少 |
| compound_surname | LC016 | 欧阳澜，复姓两字 |
| unlinked_account | — | 公用临柜终端 `TELLER-PUB-YX01`、外部讲师 `tr.ext.lecturer01`，`owner` 为 null 但在记录里出现 |
| departed | LC014、LC029 | 已离职、无登录账号、留有历史记录；LC029 的职业资格 2025-12-31 过期未换证 |
| assignee_mismatch | LC006 与 LC007；LC028 | 并购项目调查报告署名高级客户经理、实际由协办人完成；公用终端的差错实际经办人是当班柜员 |
| cross_source_duplicate | LC016、LC024 | 同一次云溪支行合规检查在信贷系统（CR-0025）和柜面运营（OP-0013）各留一条记录 |
| appeal | LC013 | 认为经理评价不公，打算对 MR02 提出申诉 |
| pm_not_dept_manager | LC011 | 普惠小微线上化项目经理，不是任何部门负责人 |
| dept_and_project_lead | LC024 | 云溪支行负责人同时是网点转型项目负责人 |

### 证据与真实能力的差距

`personas.yaml` 刻意把「系统能看到的证据」和真实水平错开，68 条记录覆盖除新人 LC009 以外的全部员工，但覆盖并不等于证据充分：

- **记录充分（documented）13 项**，集中在岗位职责与系统留痕高度一致的岗（审查、贷后、事后监督、授权、现金管理、反洗钱）。
- **完全无记录（undocumented）18 项**，典型是客户拓展、制度制定、跨条线协调、转岗前的历史能力和新人的入职前经验。这些必须输出「未知」，不能因为没记录就判成不会。
- **记录会误导（misleading）6 项**：报告署名与实际贡献相反（LC006 / LC007）、持证加学时加夸大自述（LC013）、离职后只剩一条记录（LC014）、经理评价偏高而自述偏低（LC026）、公用终端的差错落到个人头上（LC028）。
- 其余为 **partial**，多见于只以「复核人」「指导人」「参与者」身份出现的管理者，以及只有一条记录的能力。

能力词典见 `templates/capabilities/commercial-banking.zh-CN.yaml`，16 项、5 个类别。涉及代销资质的 `wealth_product_suitability` 在描述里写明了「资质是开展业务的前提，持证不等于具备能力」。

### 校验

从仓库根目录运行：

```
uv run --project backend python examples/test-enterprises/validate.py lancheng-bank
```
