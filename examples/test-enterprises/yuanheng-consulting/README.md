## 远衡管理咨询（模拟）

NERE-19 合成测试企业 #6。企业、人员、邮箱、客户名称、项目、账号全部虚构，不含任何真实企业或真实员工信息。**本目录是测试数据，不会被 `app.seed` 自动导入**；`examples/demo-org/` 才是演示数据。本目录也不包含 `claims.yaml`、`profile_versions.yaml`、`progress.yaml` —— 画像结论要由系统自己推断，不预置。

一家 27 人（在职 25 人）的中型管理咨询公司，行业分类 GB/T 4754 门类 L（租赁和商务服务业）小类 7243 社会经济咨询。能力词典：`templates/capabilities/management-consulting.zh-CN.yaml`，16 项、5 个 category。

### 组织形态：项目制（projectized）

人归 Practice（部门）管，活按 Engagement（项目）组织。顾问的 `reports_to` 指向所属 Practice 负责人，`secondary_reports_to` 指向当期项目的项目经理（Engagement Manager）。职级阶梯 Analyst → Consultant → Senior Consultant → Manager → Senior Manager → Partner，职能序列（人力、运营、财务）单独走专员职级。

项目跨 Practice 抽调人员：ENG-ZQ 从运营与供应链咨询部借调蒋柏延、从知识与研究中心借调班以珩；ENG-PX 的三名成员分属三个 Practice；ENG-SZ 由人力与运营支持部的甘棠兼岗做项目运营支持。

角色关系有意做了两种对照：

- 合伙人司徒明昭（YH001）**既是**战略与组织咨询部负责人**又是** ENG-ZQ 项目经理（`dept_and_project_lead`）。
- 陆维桢、高韵之、曾翊、段佳霖、欧阳清和五人是项目经理，但都不是任何部门负责人（`pm_not_dept_manager`）。内部项目 ENG-KM 的项目经理欧阳清和，其成员里还包含她自己的部门负责人屈砚秋。

### 部门（Practice）

| 部门 | 在职人数 | 负责人 | PCF 一级类别 | 职责 |
| --- | --- | --- | --- | --- |
| 战略与组织咨询部 | 6 | YH001 司徒明昭 | 1.0 Develop Vision and Strategy；5.0 Deliver Services | 战略规划、集团管控与组织设计咨询 |
| 运营与供应链咨询部 | 5 | YH007 裴云澍 | 5.0 Deliver Services；13.0 Develop and Manage Business Capabilities | 流程诊断、运营效率与供应链计划咨询 |
| 数字化咨询部 | 6 | YH012 梁书彦 | 5.0 Deliver Services；8.0 Manage Information Technology (IT) | 数字化蓝图、IT 治理与数据治理咨询 |
| 知识与研究中心 | 4 | YH018 屈砚秋 | 13.0 Develop and Manage Business Capabilities | 行业研究、基准数据库与方法论资产 |
| 人力与运营支持部 | 4 | YH022 卞秋澄 | 7.0 Develop and Manage Human Capital；8.0 Manage Information Technology (IT)；9.0 Manage Financial Resources | 招聘与职级、项目资源排程、工时与合约财务、内部系统 |

离职的 YH026 卓亦衡（原数字化咨询部）、YH027 温语岑（原运营与供应链咨询部）不计入在职人数。

### 项目（Engagement）

| 代码 | 项目 | 项目经理 | 成员 |
| --- | --- | --- | --- |
| ENG-ZQ | 正桥集团集团管控与组织重设计 | YH001 司徒明昭（兼部门负责人） | YH002、YH004、YH009、YH020 |
| ENG-HX | 华序零售三年战略规划 | YH003 高韵之 | YH004、YH005、YH006、YH019 |
| ENG-LT | 澜涛食品供应链降本与计划体系优化 | YH008 曾翊 | YH009、YH010、YH011、YH027 |
| ENG-SZ | 明曜制造数字化转型蓝图与 IT 治理 | YH013 段佳霖 | YH014、YH015、YH016、YH024、YH026 |
| ENG-PX | 岱川银行网点运营效率诊断 | YH002 陆维桢 | YH005、YH010、YH021 |
| ENG-KM | 知识资产库与行业基准库建设（内部） | YH019 欧阳清和 | YH018、YH020、YH021、YH024 |

客户名称（正桥集团、华序零售、澜涛食品、明曜制造、岱川银行）全部虚构。

### 数据来源

本企业不使用 Jira，所有工作记录都在 `work_records.csv`，共 74 条，三个来源：

| 来源 | kind | record_id 前缀 | 条数 | 内容 |
| --- | --- | --- | --- | --- |
| 工时系统 | work_records | `TS-` | 36 | 顾问按周填报项目工时，项目经理审批；`actor_account` 是工时系统工号（`ts1001`–`ts1027`），也是 `org.yaml` 的 `account_id` |
| 交付物库 | work_records | `DL-` | 28 | 报告、模型、访谈纪要、底稿、方法模板，记录作者与审核人；账号是邮箱前缀或英文名，登记在 `roster.yaml` 的 `external_accounts` |
| 项目复盘 | work_records | `RV-` | 10 | 阶段复盘、结项复盘、质量回顾；复盘模块与工时系统共用一套登录账号，所以这里的账号也是 `ts10xx` |

时间范围 2025-11-01 至 2026-10-31，时区统一 `+08:00`。自述 `submitted_at` 落在 2026-09 至 2026-10，经理评价 `reviewed_at` 落在 2026-10。

### `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，表头固定为 12 列：

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一编号，`TS-`/`DL-`/`RV-` 加四位序号 |
| `source` | 来源名，取 `enterprise.yaml` 中 `kind: work_records` 的 `sources[].name`：工时系统、交付物库、项目复盘 |
| `record_type` | 记录类型：周工时填报、咨询报告、工作底稿、访谈纪要、测算模型、标准文档、流程文档、会议材料、方法模板、研究摘要、归档记录、阶段复盘、结项复盘、质量回顾 |
| `occurred_at` | ISO 8601 时间，带 `+08:00` |
| `actor_account` | 来源系统里的执行人账号；可能是未登记账号（见 `personas.yaml` 的 `account_truth`） |
| `actor_name` | 来源系统里的显示名；英文名（Eric Zeng、Kelvin Duan、Ivan Zhuo）和共享账号名（项目运营组（共享账号）、实习账号02、交付物库归档服务）与花名册不同 |
| `actor_role` | 执行人在该条记录里的角色：填报人、作者、归档人、复盘主持人 |
| `participants` | 其他参与者，`账号:角色` 用 `;` 分隔，可空；角色有审批人、复核人、审核人、知悉、参与人、点评人 |
| `project` | 项目代码（ENG-ZQ 等），内部工时和内部模板为空 |
| `title` | 记录标题 |
| `detail` | 一两句纯文本描述，不含任何真实隐私信息 |
| `status` | 来源系统状态：已审批、已退回、待审批、已审核、已归档、已完成、进行中 |

### 登录账号

每位在职员工恰好一个登录账号，`email` 等于其 `work_email`，域名统一 `@yuanheng-consulting.example.com`。两名离职人员没有登录账号。

| 邮箱 | 角色 | 员工 |
| --- | --- | --- |
| `lou.zhiwei@yuanheng-consulting.example.com` | `org_admin` | YH023 娄知微（人力与运营支持部的普通员工，不是部门负责人） |
| `situ.mingzhao@yuanheng-consulting.example.com` | `dept_manager` | YH001 司徒明昭（战略与组织咨询部） |
| `pei.yunshu@yuanheng-consulting.example.com` | `dept_manager` | YH007 裴云澍（运营与供应链咨询部） |
| `liang.shuyan@yuanheng-consulting.example.com` | `dept_manager` | YH012 梁书彦（数字化咨询部） |
| `qu.yanqiu@yuanheng-consulting.example.com` | `dept_manager` | YH018 屈砚秋（知识与研究中心） |
| `bian.qiucheng@yuanheng-consulting.example.com` | `dept_manager` | YH022 卞秋澄（人力与运营支持部） |
| 其余 19 位在职员工 | 普通员工（不写 role） | YH002–YH006、YH008–YH011、YH013–YH017、YH019–YH021、YH024、YH025 |

### 本企业的边界情况

| tag | 涉及人员 | 数据上怎么体现 |
| --- | --- | --- |
| `departed` | YH026、YH027 | 有 `left_on`、无登录账号，但在三个来源里都有历史记录；YH026 名下 31 份交付物在离职当天被批量归档（DL-0023） |
| `new_hire_no_history` | YH017 | 2026-09-14 入职，不出现在任何记录和 participants 里，也没有自述，只有一条 `not_sure` 评价 |
| `intern` | YH006 | 暑期实习延长为学期内兼职，有工时和交付物，交付物用未登记的公共账号提交 |
| `appeal` | YH010、YH015 | 一位被「已退回」状态拖低、修订版未入库；一位被经理以「转岗才半年」代替事实判断 |
| `manager_unsure` | YH017、YH021 | 两条 `answer: not_sure` 的经理评价 |
| `english_name` | YH008、YH013、YH026 | 来源显示名与交付物库账号是 Eric Zeng / Kelvin Duan / Ivan Zhuo，花名册是中文名 |
| `dept_and_project_lead` | YH001 | 部门负责人兼 ENG-ZQ 项目经理 |
| `pm_not_dept_manager` | YH002、YH003、YH008、YH013、YH019 | 五位项目经理都不是部门负责人 |
| `multi_account` | YH006、YH013、YH026 | 每人在来源数据里用过两个账号；只有 YH006 的第二个账号未在 HR 登记 |
| `unlinked_account` | 无（账号层面） | `dl-svc-archive`（交付物库归档服务）、`ts-pool-ops`（运营组共享填报账号）`owner: null`，但在记录里出现过 |
| `transfer` | YH015 | 2026-04-01 从知识与研究中心转入数字化咨询部，工时记录里能看到转岗首周 |
| `concurrent_post` | YH024 | 主岗人力与运营支持部，`secondary_departments` 为数字化咨询部 |
| `assignee_mismatch` | YH013、YH014、YH026 | DL-0012 作者字段是 YH013，主体章节实际由 YH026、YH014 执笔，RV-0005 有记载 |
| `cross_source_duplicate` | YH013 | IT 治理章程同时出现在 TS-0022、DL-0012、RV-0005 三个来源 |
| `review_conflict` | YH005、YH014 | 自述与经理评价直接矛盾 |
| `compound_surname` | YH001、YH019 | 司徒、欧阳 |

### 证据与真实能力的差距（有意设计）

`personas.yaml` 里的 `ground_truth` 不导入系统，用来核对系统推断得准不准。差距是刻意铺开的，不是所有人都「记录充分」：

- **记录充分（documented）31 项**：如蒋柏延的流程诊断（工时、交付物、自述、经理评价四方一致）、欧阳清和的基准分析。这些是系统应该能确认的。
- **记录不足（partial）** ：如司徒明昭的客户高层沟通只有一份材料留痕、邵予初的情景模型没进交付物库、屈砚秋定调的基准方法署名都是下属。
- **完全无记录（undocumented）12 项**：YH002 的行业研究、YH007 的供应链计划设计、YH012 的数据治理、YH015 转岗前的检索工作、YH017 入职前的两个蓝图项目、YH023 的薪酬检索等。`evidence_refs` 一律为空，系统应输出「未知」，不能因为空白推断为「不会」。
- **记录会误导（misleading）9 项**：
  - YH005 自述夸大、YH014 自述夸大且经理评价又压得过低；
  - YH010 的交付物库只留下被退回的 v0.4，修订版未入库；
  - YH011 的工时退回只是填报格式问题，被经理当成能力不足；
  - YH013 署名了实际由 YH026、YH014 执笔的治理章程，YH026 的真实贡献因此被隐藏；
  - YH015 的独立产出被「转岗才半年」盖过；
  - YH021 的任务范围一直被限定，经理答 `not_sure`、她自述也保守；
  - YH006 的交付物挂在公共账号下；
  - YH009 在 ENG-ZQ 的工时挂在组织设计项目下，但他只做流程部分。
- HR 档案（`roster.yaml` 的 `certifications`）与真实情况也不一致：YH012 的 PMP 2024-05 过期未更新、YH014 的 CDMP 2025-12-31 过期仍按有效显示。证书是前提，不等于能力。

### 校验

从仓库根目录运行：

```
uv run --project backend python examples/test-enterprises/validate.py yuanheng-consulting
```
