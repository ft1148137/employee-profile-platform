禾邻超市（模拟）是 NERE-19 合成测试数据集的第 8 家企业。公司、门店、人员、邮箱、工号与全部记录均为虚构，不含任何真实企业或真实员工信息，也不含任何顾客身份、支付或联系信息。**本目录仅用于系统测试，不会被 seed 加载器自动导入**（加载器只读 `examples/demo-org/`）。

## 企业简介

禾邻超市是一家区域连锁超市，在东城片区经营两家标准超市门店，经营面积 3000 平方米上下，生鲜占比接近四成。行业分类 GB/T 4754 门类 F「批发和零售业」，小类 5212「超级市场零售」。

## 组织形态：区域事业部制（divisional）

东城区域运营部是区域事业部，对辖内门店的销售、损耗和服务指标负责；区域经理既管人也管事，两名区域督导代表区域去门店巡检。门店是独立核算单元，店长向区域经理汇报，店内的值班经理、收银、理货、生鲜加工各岗向店长或值班经理汇报。商品采购部和人力与信息部是总部职能部门，对门店只有标准和制度，没有人事指挥权。

和职能制的区别在于：门店是完整的业务单元（本目录里把两家门店各自建模为一个部门），区域事业部对结果负责；采购和人力不参与门店日常指挥。

另有一个跨部门专项 `SX01 生鲜损耗专项改善`，由区域督导邵禾（HL002）牵头，成员来自区域、两家门店和采购部。专项负责人不是任何部门的负责人，用来验证系统能否区分部门职责与项目职责。

## 部门表

| 部门 | 负责人 | 在职人数 | PCF 一级类别 | 职责 |
| --- | --- | --- | --- | --- |
| 东城区域运营部 | HL001 乔砚秋（区域运营经理） | 5 | 3.0 Market and Sell Products and Services、4.0 Deliver Physical Products | 区域门店销售与损耗指标、督导巡店与整改、生鲜运营标准、跨店人力调度 |
| 滨江店 | HL006 鲁思勉（店长） | 7 | 3.0 Market and Sell Products and Services、6.0 Manage Customer Service | 开闭店、收银前台、理货陈列、生鲜加工、食品安全日常管理 |
| 南湖店 | HL013 蒙晓渠（店长） | 6 | 3.0 Market and Sell Products and Services、6.0 Manage Customer Service | 开闭店、收银前台、理货陈列、生鲜加工、盘点与损耗管理 |
| 商品采购部 | HL020 窦见青（采购经理） | 4 | 4.0 Deliver Physical Products、12.0 Manage External Relationships | 供应商谈判、商品引进、品类结构与价格带、索证索票归档 |
| 人力与信息部 | HL024 项禾苗（部门负责人） | 4 | 7.0 Develop and Manage Human Capital、8.0 Manage Information Technology (IT) | 用工与排班制度、培训组织、门店系统账号与权限 |

在职 26 人，合计 27 人（HL019 曲文锦已于 2026-06-30 离职）。职级体系：管理职能岗 M2–M4 与 P2–P4；门店岗位用零售口径的店长 A/B 级、值班经理、课组长、店员一级（新入职）/二级/三级（熟练）、厂家派驻岗、实习生。

## 数据来源

本企业不使用 Jira。三个来源都写在 `work_records.csv` 里，`source` 列取 `enterprise.yaml` 中 `kind: work_records` 的来源名：

| 来源名 | record_id 前缀 | 内容 | 条数 |
| --- | --- | --- | --- |
| 排班系统 | `PB-` | 周班次排班、换班申请、考勤异常核对、跨店支援调度 | 24 |
| POS 系统 | `POS-` | 按班次的销售汇总、长短款与差错冲正、退换货、损耗调整 | 20 |
| 巡店系统 | `XD-` | 督导巡店打分、食品安全专项检查、盘点抽查、整改闭环、神秘顾客检查 | 18 |

合计 62 条，时间范围 2025-11-03 至 2026-10-29，时区统一 `+08:00`。POS 记录只保留班次级汇总与商品条码层面的信息，不含任何顾客身份、支付凭证或联系方式。

员工在排班系统里的工号是 `org.yaml` 的 `account_id`（如 `hl1008`）；POS 操作号和巡店系统账号登记在 `roster.yaml` 的 `external_accounts` 里（如 `POS-BJ-03`、`xd-sv-01`）。未登记的账号写在 `personas.yaml` 的 `account_truth` 里。

## `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，表头固定为 12 列：

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一的记录编号，按来源分 `PB-` / `POS-` / `XD-` 三段 |
| `source` | 来源名，取值为 排班系统、POS 系统、巡店系统 |
| `record_type` | 记录类型：班次排班、换班申请、考勤异常、临时调店支援、班次销售汇总、收银差错、退换货、损耗调整、巡店记录、整改跟踪、食品安全专项检查、盘点抽查、神秘顾客检查 |
| `occurred_at` | 发生时间，ISO 8601 带 `+08:00` |
| `actor_account` | 来源系统里的执行人账号，可能是未登记账号（见 `account_truth`） |
| `actor_name` | 来源系统里显示的名字，可能与花名册不同（如 `服务台`、`外部检查员`） |
| `actor_role` | 执行人在这条记录里的角色，如 排班编制人、班次负责人、巡店督导、整改责任人 |
| `participants` | 其他参与者，格式 `账号:角色`，多个用 `;` 分隔，可为空 |
| `project` | 项目编号，仅生鲜损耗专项的记录填 `SX01`，其余为空 |
| `title` | 记录标题 |
| `detail` | 一两句纯文本描述，不含任何个人隐私或顾客信息 |
| `status` | 来源系统里的状态：已生效、已批准、已完成、已关闭 |

## 登录账号表

每位在职员工恰好一个登录账号，邮箱等于 `work_email`，域名统一 `@helin-retail.example.com`。离职人员不建账号。

| 邮箱 | 系统角色 | 员工 | 说明 |
| --- | --- | --- | --- |
| `hl025@helin-retail.example.com` | org_admin | HL025 阚宁 | 人力与信息部信息专员，全企业唯一的企业管理员，不是部门负责人 |
| `hl001@helin-retail.example.com` | dept_manager | HL001 乔砚秋 | 东城区域运营部负责人 |
| `hl006@helin-retail.example.com` | dept_manager | HL006 鲁思勉 | 滨江店负责人 |
| `hl013@helin-retail.example.com` | dept_manager | HL013 蒙晓渠 | 南湖店负责人 |
| `hl020@helin-retail.example.com` | dept_manager | HL020 窦见青 | 商品采购部负责人 |
| `hl024@helin-retail.example.com` | dept_manager | HL024 项禾苗 | 人力与信息部负责人 |
| 其余 20 位在职员工 | 普通员工 | HL002–HL005、HL007–HL012、HL014–HL018、HL021–HL023、HL026–HL027 | 账号无 role 字段 |

## 本企业的边界情况清单

| tag | 涉及人员 | 数据上怎么体现 |
| --- | --- | --- |
| same_name | HL008、HL014 | 两名收银岗都叫王静，工号 `hl1008` / `hl1014`，POS 操作号 `POS-BJ-03` / `POS-NH-02` |
| multi_account | HL008、HL016 | 王静同时用排班工号与 POS 操作号；覃志波有现工号 `hl1016` 和未登记旧工号 `hl0988` |
| unlinked_account | 不属于任何员工 | 服务台公用操作号 `POS-BJ-PUB`、第三方神秘顾客账号 `xd-ms-03` |
| transfer | HL016 | 2026-05-01 从滨江店生鲜理货员转岗南湖店生鲜课长，前后岗位痕迹分散在两个工号上 |
| departed | HL019 | 2026-06-30 离职，无登录账号，保留在职期间的排班与 POS 记录 |
| contractor | HL011、HL018 | 品牌厂家派驻促销员，有排班工号、无 POS 权限 |
| intern | HL012 | 2026-07 入职的暑期收银实习生 |
| review_conflict | HL010、HL008 | 自述与经理评价直接矛盾（陈列改造、服务台处理） |
| appeal | HL008 | 认为服务台能力评价偏低且同名同事的差错被算到自己名下，会去申诉 |
| manager_unsure | HL009 | 值班经理对她的保鲜方案能力答 `not_sure` |
| no_forms | HL010、HL017 | 数字素养 low，换班申请与报损单由他人代填 |
| mobile_only | HL010、HL017 | 只用手机，没有门店 PC 账号 |
| colloquial | HL010、HL017、HL011 | 自述全是口语和方言词，常用语音转文字 |
| ethnic_minority_name | HL009 | 维吾尔族姓名「阿依努尔·吐尔逊」，带间隔号 |
| assignee_mismatch | HL006、HL010 | 熟食区温度缺记扣分记在店长名下，实际漏记发生在日配理货员班次 |
| cross_source_duplicate | HL013、HL016、HL017 | 同一次叶菜损耗整改同时出现在巡店、POS、排班三个来源 |
| pm_not_dept_manager | HL002 | 生鲜损耗专项负责人是区域督导，不是任何部门负责人 |

## 刻意设计的证据与真实能力差距

`personas.yaml` 里的 `ground_truth` 不导入系统，用来核对系统推断的画像。本企业的差距集中在三处，都是连锁超市的真实痛点：

1. **门店得分不等于店长能力**。巡店扣分只能挂到门店和店长（HL006），但 2026-06 熟食区温度缺记发生在理货员（HL010）的班次上。系统若直接把扣分读成店长的食品安全能力不足就是错的。
2. **一线能力留不下记录**。生鲜理货员（HL017）手上分级摘洗很强，报损单却是别人替她填的；带过四五个新人也没有任何带教记录。非食采购与品控（HL023）在三个来源里零记录，真实能力却很高。这些条目标为 `undocumented`，系统应输出「未知」，不得推断为「不会」。
3. **账号与身份错位**。同名收银员、服务台公用操作号、转岗前的旧工号、第三方神秘顾客账号，都会让记录归属出错。`account_truth` 写明了每个未登记账号的真实归属。

6 条 `misleading` 的分布：店长 HL006 的食品安全（扣分错位）、收银员 HL008 的收银与服务台两项（同名混淆 + 公用账号）、收银组长 HL014 的收银（记录里她是 actor，冲正其实是值班经理做的）、督导 HL003 的标价合规（整改是门店做的、他只验证）、理货员 HL010 的陈列（自述夸大一档）。另外 HL010 的从业人员健康证明已于 2026-04-30 过期未复检，HR 档案却仍显示在册——持证状态不等于能力，证书过期也只说明档案没更新，不构成能力结论。

统计上：73 条真实能力里 `documented` 27 条、`partial` 16 条、`undocumented` 24 条、`misleading` 6 条。来源数据覆盖 20 人，另外 7 人（HL001 区域经理、HL020/HL023 采购、HL024–HL027 人力与信息部）在三个来源里没有任何记录——这是真实情况：本企业没有合同系统、培训系统和办公系统作为来源，职能岗的工作成果天然不可见。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `enterprise.yaml` | 企业元数据、GB/T 4754 分类、组织形态、来源、部门与 PCF 对应、跨部门专项 |
| `org.yaml` | 部门、员工（employee_code / department / display_name / title / work_email / account_id）、登录账号 |
| `roster.yaml` | 职级、入职与离职、汇报关系、系统角色、用工类型、转岗、外部账号、HR 档案证书 |
| `personas.yaml` | 真实能力（ground truth）、使用画像、账号真相、边界情况清单；首行是「测试用，不导入系统」 |
| `self_reports.yaml` | 14 条员工自述，语气按人设写（口语、保守、带申诉诉求） |
| `manager_reviews.yaml` | 18 条经理评价，含 1 条 `not_sure` 与多条与自述矛盾的回答 |
| `work_records.csv` | 62 条非 Jira 工作记录，来自排班、POS、巡店三个来源 |
| `../../../templates/capabilities/chain-retail.zh-CN.yaml` | 连锁超市能力词典，16 项，5 个 category |

校验：在仓库根目录执行 `uv run --project backend python examples/test-enterprises/validate.py helin-retail`。
