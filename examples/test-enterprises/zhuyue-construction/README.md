筑岳建设工程（模拟）是一家虚构的房屋建筑施工总承包企业，用于 NERE-19 的系统测试。公司名、人名、邮箱、项目、工作记录全部虚构，不含任何真实企业或真实员工信息。

**测试用，不自动导入。** 本目录不是 seed 数据，`backend/app/seed` 不会读取它。`personas.yaml` 里的「真实能力」是核对系统推断结果的参照答案，任何情况下都不导入系统。

## 企业概况

主营住宅房屋建筑施工总承包（GB/T 4754 门类 E 建筑业，小类 4710 住宅房屋建筑）。在建项目一个（栖湖花园住宅项目），另有一个维保与小型改造项目群。在职 26 人，含离职人员共 28 人。

## 组织形态：混合制（项目部 + 职能部门）

职能部门管人、管标准，项目部管现场生产：

- 栖湖花园项目部的负责人就是项目经理冯承岳（ZY001，一级建造师），**既是部门负责人又是项目负责人**。
- 维保与小型改造项目群由工程管理部的生产经理段嘉懿（ZY015）牵头，他**不是任何部门的负责人**，项目成员跨工程管理部、安全质量部、商务合约部三个部门。
- 安全质量部的专职安全员、质量员按项目派驻：行政关系留在安全质量部，现场工作听项目经理安排，`roster.yaml` 里体现为 `secondary_departments` 和 `secondary_reports_to`。工程管理部的技术管理工程师欧阳敏（ZY016）同样派驻支持项目部。
- 劳务班组长由鸿途劳务（虚构分包单位）派驻，在花名册里是 `employment_type: contractor`。

## 部门

| 部门 | 负责人 | 在职人数 | 主要职责 | PCF 一级类别 |
| --- | --- | --- | --- | --- |
| 栖湖花园项目部 | ZY001 冯承岳（项目经理） | 8 | 现场施工生产组织、劳务班组管理、进度与安全文明施工落实 | 4.0 Deliver Physical Products；10.0 Acquire, Construct, and Manage Assets |
| 安全质量部 | ZY009 卫立群 | 5 | 安全与质量标准、隐患排查整改闭环、检验批与隐蔽工程验收监督、安全员质量员派驻 | 11.0 Manage Enterprise Risk, Compliance, Remediation, and Resiliency；13.0 Develop and Manage Business Capabilities |
| 工程管理部 | ZY014 聂志坤 | 5 | 施工方案与技术管理、进度计划审核、机电与 BIM 支持、维保项目生产组织 | 2.0 Develop and Manage Products and Services；4.0 Deliver Physical Products |
| 商务合约部 | ZY019 詹秀兰 | 4 | 投标报价、分包合约、计量计价、变更签证与索赔 | 9.0 Manage Financial Resources；12.0 Manage External Relationships |
| 人力行政部 | ZY023 卓丽华 | 4 | 招聘与劳动关系、证书与特种作业证台账、实名制、系统账号与权限 | 7.0 Develop and Manage Human Capital；8.0 Manage Information Technology (IT) |

## 数据来源

三个来源都不是 Jira。`enterprise.yaml` 的 `uses_jira` 为 `false`，本目录没有 `jira/` 子目录。

| 来源名 | kind | record_id 前缀 | 说明 |
| --- | --- | --- | --- |
| 施工日志 | work_records | `RZ-` | 公司统一项目管理平台的施工日志模块，手机端和电脑端都能填报，**全员主账号就在这个系统**，`org.yaml` 的 `account_id` 取的是这里的账号 |
| 安全检查 | work_records | `AQ-` | 外购的安全检查与隐患整改闭环系统，记录巡检、整改通知、复查关闭 |
| 质量验收 | work_records | `ZL-` | 外购的质量验收系统，记录检验批验收、隐蔽工程验收、材料报验与返工处置 |

账号口径（刻意保留的真实世界复杂度）：安全检查、质量验收是两套外购系统，只给需要**签署记录**的岗位开了专用账号（前缀 `aq-`、`zl-`），登记在 `roster.yaml` 的 `external_accounts` 里；其他人只在记录的参与人栏位里以统一平台账号出现。监理单位和分包单位的账号由建设单位或分包方自行分配，公司没有登记，见 `personas.yaml` 的 `account_truth`。

## `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，58 条记录（施工日志 26 条、安全检查 16 条、质量验收 16 条），时间范围 2025-11-05 至 2026-10-23，时区一律 `+08:00`。

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一的记录编号，如 `RZ-0001`、`AQ-0007`、`ZL-0014` |
| `source` | 来源名，取 `enterprise.yaml` 中 `kind: work_records` 的 `sources[].name`，即 施工日志 / 安全检查 / 质量验收 |
| `record_type` | 记录类型，如 施工日志、生产例会记录、技术交底记录、日常巡检、隐患整改通知、整改复查、隐蔽工程验收、检验批验收、返工处置单 |
| `occurred_at` | 记录发生时间，ISO 8601 带 `+08:00` |
| `actor_account` | 来源系统里的执行人账号，可能是未登记账号（见 `account_truth`） |
| `actor_name` | 来源系统里显示的名字，可能与花名册不同（如「安全监理工程师」） |
| `actor_role` | 执行人在这条记录里的角色，如 记录人、代录人、检查人、复查人、验收人、报验人、签认人、主持人 |
| `participants` | 其他参与者，`账号:角色` 用 `;` 分隔，可为空 |
| `project` | 项目编号，`QHHY`（栖湖花园住宅项目）或 `WBXM`（维保与小型改造项目群） |
| `title` | 记录标题 |
| `detail` | 一两句纯文本描述，不含真实隐私信息 |
| `status` | 来源系统里的状态，如 已提交、已退回、已完成、已下发、已关闭、已通过 |

## 登录账号

在职 26 人各有且仅有一个登录账号，邮箱等于 `work_email`（域名 `@zhuyue-construction.example.com`）。两名离职人员没有登录账号。

| 邮箱 | 角色 | 员工 | 说明 |
| --- | --- | --- | --- |
| `fu.yaqi@zhuyue-construction.example.com` | org_admin | ZY024 傅雅琪 | 人力行政部人事专员，兼公司系统管理员，不是部门负责人 |
| `feng.chengyue@zhuyue-construction.example.com` | dept_manager | ZY001 冯承岳 | 栖湖花园项目部负责人兼项目经理 |
| `wei.liqun@zhuyue-construction.example.com` | dept_manager | ZY009 卫立群 | 安全质量部 |
| `nie.zhikun@zhuyue-construction.example.com` | dept_manager | ZY014 聂志坤 | 工程管理部 |
| `zhan.xiulan@zhuyue-construction.example.com` | dept_manager | ZY019 詹秀兰 | 商务合约部 |
| `zhuo.lihua@zhuyue-construction.example.com` | dept_manager | ZY023 卓丽华 | 人力行政部 |
| 其余 20 名在职员工 | 普通员工（不写 role） | ZY002–ZY008、ZY010–ZY013、ZY015–ZY018、ZY020–ZY022、ZY024（已列）、ZY025、ZY026 | 邮箱同 `work_email` |

## 本企业的边界情况清单

| tag | 涉及员工 | 数据上怎么体现 |
| --- | --- | --- |
| same_name | ZY003、ZY010 | 项目部土建施工员和派驻安全员都叫王海涛，账号 `wang.haitao` 与 `wang.haitao2`，RZ-0024 两人同时在场 |
| multi_account | ZY006、ZY010 | 孟广才换手机后有新旧两个统一平台账号（旧账号 `menggc-old` 未登记）；王海涛（ZY010）同时有统一平台账号和安全检查系统账号 |
| unlinked_account | — | `hongtu-02`（分包单位共享账号）、`JL-SUP-07`（监理工程师账号）在记录里反复出现，不属于任何员工 |
| transfer | ZY013、ZY017 | 乔雨桐 2025-12 资料员转试验检测员，滕立新 2026-04 项目部转工程管理部；转岗前后的记录岗位名称不同 |
| concurrent_post | ZY010、ZY011、ZY012、ZY016 | 安全员、质量员、技术管理工程师派驻项目部，有 `secondary_departments` 与第二汇报人 |
| departed | ZY027、ZY028 | 2026-05 与 2026-02 离职，无登录账号，三个来源里留有历史记录 |
| new_hire_no_history | ZY022 | 2026-09-07 入职，无任何工作记录、无自述 |
| contractor | ZY006、ZY007 | 两位劳务班组长由分包单位派驻 |
| intern | ZY026 | 信息管理实习生，唯一记录是补录历史纸质日志 |
| assignee_mismatch | ZY002/ZY011、ZY005/ZY004 | ZL-0002、ZL-0012 用项目总工账号提交，实际是质量员做的；RZ-0008 填报人是资料员，实际是机电施工员做的 |
| cross_source_duplicate | ZY003、ZY006、ZY010 | 脚手架连墙件整改（AQ-0007 / RZ-0013 / AQ-0008）与动火作业整改（AQ-0012 / RZ-0021 / AQ-0013）各跨两个来源 |
| review_conflict | ZY003、ZY011、ZY012 | 自述与经理评价相互矛盾，经理的理由都是「系统里记录少」 |
| appeal | ZY011、ZY012 | 两人都认为评价不实，会在系统里提申诉 |
| manager_unsure | ZY007、ZY012、ZY018 | 各有一条 `answer: not_sure` 的经理评价 |
| no_forms | ZY006、ZY007 | 两位班组长 `digital_literacy: low`，表格填不了 |
| mobile_only | ZY006、ZY007 | `devices: [mobile]` |
| colloquial | ZY006、ZY007 | 日志和自述是语音转文字，带方言词和错别字 |
| compound_surname | ZY016 | 欧阳敏，账号 `ouyang.min` |
| dept_and_project_lead | ZY001 | 栖湖花园项目部负责人兼项目经理 |
| pm_not_dept_manager | ZY015 | 维保项目负责人，但只是工程管理部生产经理 |

## 证据与真实能力之间的差距（刻意设计）

- **记录归属错人**：质量验收系统只给项目总工开了提交签认权限，质量员简宁（ZY011）主办的争议检验批和后浇带隐蔽验收都挂在总工账号下。她的真实水平是最高档，经理评价却是「需要协助」，理由是「记录少」。系统如果按提交账号统计能力，会同时高估总工、低估质量员。
- **按记录条数判断能力**：维保项目安全员柴明睿（ZY012）一年只有三条检查记录，生产经理按条数填了「需要协助」。真实水平是能独立闭环。
- **零记录不等于不会**：商务合约部的商务系统不在本数据集的三个来源内，部门经理詹秀兰（ZY019）两项能力都是 `undocumented`；工程管理部经理聂志坤（ZY014）2012–2019 年独立带过三个项目，公司换系统时旧记录没有迁移。系统应输出「未知」。
- **过期证书不等于不会**：木工班组长孟广才（ZY006）的架子工特种作业操作证 2023 年已过期、公司不再安排他做架子作业，但他确实懂支撑体系；项目经理冯承岳（ZY001）的安全生产考核合格证书（B 类）也已过期在换证中。证书状态不应直接当成能力结论。
- **持证不等于有能力**：质量员简宁持二级建造师但从未担任项目经理岗；生产经理段嘉懿持一级建造师且已注册，但没独立带过大体量在建项目；机电工程师滕立新持建筑电工操作证，现岗位不做现场临电管理。
- **自述夸大**：土建施工员王海涛（ZY003）自述能独立组织复杂多专业穿插，实际遇到抢作业面仍需项目总工到场。
- **状态误导**：机电施工员郝明远（ZY004）接手离职同事作业面的日志 RZ-0016 状态是「已退回」（格式不合要求），工作本身完成了。
- **代录与口述**：RZ-0008 由资料员代录机电施工员口述的内容，RZ-0022 由实习生补录 2025 年四季度的纸质日志，两条记录的填报人都不是工作的实际完成人。

## 文件清单

| 文件 | 内容 |
| --- | --- |
| `enterprise.yaml` | 企业元数据、GB/T 4754 分类、组织形态、来源、部门与 PCF 对应、两个项目 |
| `org.yaml` | 部门列表、28 名员工、26 个登录账号，格式同 `examples/demo-org/org.yaml` |
| `roster.yaml` | 职级、入职离职、汇报关系、系统角色、用工类型、兼岗、转岗、外部账号、证书台账 |
| `personas.yaml` | 真实能力（ground truth）、使用画像、账号真相、边界情况；首行是「测试用，不导入系统」 |
| `self_reports.yaml` | 18 条员工自述，`submitted_at` 在 2026-09-01 至 2026-10-31 |
| `manager_reviews.yaml` | 20 条经理评价，`reviewed_at` 在 2026-10-01 至 2026-10-31 |
| `work_records.csv` | 58 条非 Jira 工作记录 |
| `../../../templates/capabilities/construction-general-contracting.zh-CN.yaml` | 本行业能力词典，16 项、5 个 category |

校验：在仓库根目录执行 `uv run --project backend python examples/test-enterprises/validate.py zhuyue-construction`。
