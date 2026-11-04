衡川汽车零部件（模拟）是为 NERE-19 合成的测试企业。公司、人名、邮箱、工号、工单号、客户抱诉和检验数据全部虚构，不包含任何真实企业或员工信息。**本目录仅用于系统测试，不会被 seed 加载器自动导入。**

## 企业简介

二级汽车零部件供应商，为制动系统和转向系统的一级供应商提供机加工件，主要产品是电子驻车制动卡钳支架和转向齿条。单厂运营，按 IATF 16949 运行质量体系，客户按 PPAP 要求审批新件。在职 27 人、合计 28 人（含 1 名 2026-06 离职的操作工）。

GB/T 4754 分类：门类 C 制造业，小类 3670 汽车零部件及配件制造。

## 组织形态

职能制（`functional`），含生产车间。要点：

- 车间按班组管生产，一班、二班班长向车间主任汇报，调机技师直接向车间主任汇报。
- 质量部独立于生产线汇报，对不合格品处置和放行有否决权；首件不合格时可锁定机床。
- 工艺技术部出工艺文件和控制计划，但不指挥车间人员。
- 跨部门工作用两个专项承载：量产导入专项由质量部的客户质量工程师牵头（他不是任何部门负责人），客户抱诉 8D 专项由质量部经理亲自牵头（他同时是部门负责人）。专项不改变汇报线，只决定谁可以评价项目成员。

## 部门

| 部门 | 负责人 | 在职人数 | 职责 | PCF 一级类别 |
| --- | --- | --- | --- | --- |
| 机加工车间 | HC001 周立安（车间主任） | 10 | 卡钳支架、转向齿条的批量生产、换型与报工 | 4.0 Deliver Physical Products |
| 质量部 | HC012 乔砚文（质量部经理） | 5 | 首件与过程检验、不合格品控制、客户抱诉与 8D、PPAP、体系内审 | 4.0 Deliver Physical Products；11.0 Manage Enterprise Risk, Compliance, Remediation, and Resiliency |
| 工艺技术部 | HC017 邢文勉（工艺技术部经理） | 4 | 工艺路线与控制计划、CAM 编程、夹具刀具方案、新件导入 | 2.0 Develop and Manage Products and Services |
| 设备动力部 | HC021 聂长庚（设备动力部经理） | 4 | 机床与公用动力设备的点检、保养、维修与精度恢复 | 10.0 Acquire, Construct, and Manage Assets |
| 人力行政部 | HC025 甘慧（人力行政部经理） | 4 | 招聘与技能矩阵、资质台账、薪酬培训、MES/QMS 账号与基础 IT | 7.0 Develop and Manage Human Capital；8.0 Manage Information Technology (IT) |

离职人员 HC011 隋明辉原属机加工车间，不计入在职人数。

## 项目（专项）

| 编号 | 名称 | 负责人 | 成员 | 说明 |
| --- | --- | --- | --- | --- |
| EPB-2 | 电子驻车卡钳支架二期量产导入 | HC013 陈懿 | HC001、HC016、HC018、HC019、HC020 | 项目经理不是部门负责人（`pm_not_dept_manager`） |
| RACK8D | 转向齿条端面跳动超差 8D 专项 | HC012 乔砚文 | HC002、HC010、HC014、HC018、HC022 | 负责人既是部门负责人又是专项负责人（`dept_and_project_lead`） |

## 数据来源

| 来源 | kind | 内容 | 记录编号 |
| --- | --- | --- | --- |
| MES | work_records | 工单报工、换型、设备点检、设备维修工单、刀具更换、厂内物流 | `MES-0001` 起，共 38 条 |
| QMS | work_records | 首件检验、巡检、不合格品处理、8D 报告、PPAP 提交、内审、MSA、过程能力分析、控制计划评审、客户抱诉 | `QMS-0001` 起，共 27 条 |

本企业不使用 Jira（`uses_jira: false`，无 `jira/issues.json`）。`work_records.csv` 共 65 条记录，时间范围 2025-11-04 至 2026-10-28，时区统一 `+08:00`。

员工在 MES 里的工号就是 `org.yaml` 的 `account_id`（主来源）：车间 `M1xxx`、质量部 `M2xxx`、工艺技术部 `M3xxx`、设备动力部 `M4xxx`、人力行政部 `M5xxx`。QMS 账号登记在 `roster.yaml` 的 `external_accounts` 里，形如 `qms.ke`。人力行政部 4 人在两个来源里都没有记录，这是刻意留出的「完全没有工作记录」样本。

## `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，表头 12 列，顺序固定：

```
record_id,source,record_type,occurred_at,actor_account,actor_name,actor_role,participants,project,title,detail,status
```

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一的记录编号，`MES-0001` 或 `QMS-0001` 形式 |
| `source` | 来源名，取 `enterprise.yaml` 中 `kind: work_records` 的来源：`MES` 或 `QMS` |
| `record_type` | 记录类型：工单报工、换型、设备点检、设备维修工单、刀具更换、厂内物流、首件检验、巡检、不合格品处理、8D 报告、PPAP 提交、内审、测量系统分析、过程能力分析、控制计划评审、客户抱诉 |
| `occurred_at` | 记录发生时间，ISO 8601 带 `+08:00` |
| `actor_account` | 来源系统里的执行人账号，可能是公共终端（`LINE3-PUB`）、临时工号（`T-TEMP-07`）或未登记的旧工号（`WG-WANG`） |
| `actor_name` | 来源系统里的显示名，可能与花名册不同，例如旧工号显示「王工」、客户门户显示「Ivy Chen」 |
| `actor_role` | 执行人在这条记录里的角色：工作负责人、操作工、调机技师、磨工、叉车司机、点检员、维修人、检验员、测量员、审核组长、审核组成员、评审人、提交人、对外联络人、报告负责人、分析人、工艺支持 |
| `participants` | 其他参与者，`账号:角色` 用 `;` 分隔，可为空 |
| `project` | 工单号（`WO-2511-018`）、设备单号（`EQ-2511-007`）或专项编号（`EPB-2`、`RACK8D`），可为空 |
| `title` | 记录标题 |
| `detail` | 一两句纯文本说明，不含真实隐私；署名人与实际执行人不一致时在这里写明 |
| `status` | 来源系统状态：已完成、已关闭、已退回 |

## 登录账号

每位在职员工恰好一个登录账号，邮箱等于 `work_email`（`<拼音>@hengchuan-auto.example.com`）。离职人员不建账号。

| 邮箱 | 身份 | 员工 |
| --- | --- | --- |
| `hc026@hengchuan-auto.example.com` | 企业管理员（org_admin） | HC026 骆青桐，人力行政部人事专员，不是部门负责人 |
| `hc001@hengchuan-auto.example.com` | 部门负责人（dept_manager） | HC001 周立安，机加工车间 |
| `hc012@hengchuan-auto.example.com` | 部门负责人（dept_manager） | HC012 乔砚文，质量部 |
| `hc017@hengchuan-auto.example.com` | 部门负责人（dept_manager） | HC017 邢文勉，工艺技术部 |
| `hc021@hengchuan-auto.example.com` | 部门负责人（dept_manager） | HC021 聂长庚，设备动力部 |
| `hc025@hengchuan-auto.example.com` | 部门负责人（dept_manager） | HC025 甘慧，人力行政部 |
| 其余 21 个邮箱 | 普通员工（不写 role） | HC002–HC010、HC013–HC016、HC018–HC020、HC022–HC024、HC027、HC028 |

HC026 虽是企业管理员，但在组织上是人力行政部的普通员工，向 HC025 汇报。

## 本企业的边界情况

| tag | 涉及员工 | 数据上怎么体现 |
| --- | --- | --- |
| `unlinked_account` | 无（账号不属于任何人） | `LINE3-PUB` 三号线公共报工终端、`T-TEMP-07` 已离场派遣人员临时工号，在 MES 里都有报工记录 |
| `multi_account` | HC010、HC013、HC018 | HC010 有现工号 `M1030` 与未登记旧工号 `WG-WANG`；HC013 有已登记 `qms.chen.yi` 与未登记客户门户账号 `ivy.chen`；HC018 同时用 `M3012`（MES）和 `qms.ding`（QMS） |
| `transfer` | HC018、HC024 | HC018 于 2026-02 从质量部检验员转工艺工程师；HC024 于 2025-12 从加工中心操作工转 TPM 点检员；QMS 和 MES 里都能看到前后岗位角色的变化 |
| `intern` | HC009 | 技校机加工实习生，2026-09-07 入职，只以 `participants` 身份出现在带教记录里 |
| `departed` | HC011 | 2026-06-30 离职，无登录账号，MES 里留有 2025-12 至 2026-06 的报工记录 |
| `assignee_mismatch` | HC002、HC004、HC009、HC010、HC012、HC014、HC016、HC018 | 工单报在班长名下而实际操作人另有其人；换型由班长签认但零点设定是调机技师做的；8D 报告署质量部经理名而根因分析是工艺工程师做的；首检记录人是过程检验员而复杂判定由三坐标测量员完成 |
| `cross_source_duplicate` | HC010、HC018、HC022 | 齿条端面跳动这一件事在 MES（`MES-0024` 刀柄更换、`MES-0030` 主轴精度恢复）和 QMS（`QMS-0015` 8D、`QMS-0021` Cpk 复测）里各记一遍 |
| `review_conflict` | HC006、HC014 | HC006 自述说再难的磨活都能独立干，经理认为测量还需协助；HC014 自述说复杂判定是别人做的，经理却答「能独立完成复杂任务」 |
| `appeal` | HC004、HC007、HC016 | 三人分别就「参数调整被算到班长名下」「被评的是不会填表而不是不会干活」「复杂首件的判定应归自己」提出申诉 |
| `manager_unsure` | HC005、HC008、HC018 | 三条 `answer: not_sure` 的经理评价，理由分别是没单独做过、方案归属不清、报告署名在别的部门 |
| `no_forms` | HC006、HC007、HC008 | 数字素养 `low`，报工和点检记录由班长或组长代填，纸面签字才是本人 |
| `colloquial` | HC002、HC006、HC007、HC008、HC010 | 自述口语化：HC007 用语音转文字且错别字多，HC006 的自述请人代打，HC010 把旧工号说成「那个老号」 |
| `dept_and_project_lead` | HC012 | 质量部经理同时是 RACK8D 专项负责人 |
| `pm_not_dept_manager` | HC013 | EPB-2 量产导入负责人是客户质量工程师，不是任何部门负责人 |

## 证据与真实能力的差距

`personas.yaml` 里的 `ground_truth` 不导入系统，用来核对系统推断的画像。本企业刻意安排的差距：

- **署名错位**：车间的工单和换型大量报在班长名下，质量部的首检报告统一署过程检验员名，8D 报告署部门经理名。系统如果只看 `actor_account`，会把 HC004、HC010、HC016、HC018 的能力算到 HC002、HC012、HC014 头上。
- **账号断裂**：HC010 的一半调机记录在 2023 年未迁移的旧工号 `WG-WANG` 下，HC013 的客户沟通记录在未登记的 `ivy.chen` 下。合并前系统会低估两人。
- **不会填表不等于不会干活**：HC006、HC007、HC008 的记录都是别人代录的，真实手艺（端面磨削、毛刺判别、超长件搬运）在系统里几乎没有痕迹。
- **过期资质当现行能力**：HC006 的熔化焊接与热切割作业特种作业操作证 2023 年已过期，HC013 的 VDA 6.3 过程审核员资格 2026-03 已过期未复训。两人的 HR 档案里都还挂着。
- **职能岗完全没有记录**：HC025、HC026、HC027、HC028 在 MES 和 QMS 里都不出现。系统应全部输出「未知」。HC027 的 `msa_spc_analysis` 和 HC028 的 `iatf_internal_audit` 真实为 `none`，用来检验界面能否区分「未知」和「不具备」。
- **超出数据范围的历史**：HC024 转岗前六年的操作工报工记录早于 2025-11，不在本数据集内，系统会看不到，不应因此判他不会操作机床。

统计：71 条真实能力记录中，`documented` 31 条、`partial` 11 条、`undocumented` 21 条、`misleading` 8 条。

## 文件

| 文件 | 说明 |
| --- | --- |
| `enterprise.yaml` | 企业元数据、GB/T 4754 分类、组织形态、来源、部门与 PCF 对应、专项 |
| `org.yaml` | 部门列表、28 名员工、27 个登录账号（格式同 `examples/demo-org/org.yaml`） |
| `roster.yaml` | 职级、入职与离职、汇报关系、系统角色、用工类型、转岗、外部账号、证书 |
| `personas.yaml` | 真实能力、使用画像、账号真相、边界情况；首行为 `# 测试用，不导入系统` |
| `self_reports.yaml` | 15 条员工自述，`submitted_at` 在 2026-09-01 至 2026-10-31 |
| `manager_reviews.yaml` | 18 条经理评价，`reviewed_at` 在 2026-10-01 至 2026-10-31 |
| `work_records.csv` | 65 条 MES 与 QMS 记录 |

能力词典：`templates/capabilities/auto-parts-manufacturing.zh-CN.yaml`，16 项、4 个 category（机加工操作与生产、质量与客户要求、工艺与工装、设备与安全）。

本目录不含 `claims.yaml`、`profile_versions.yaml`、`progress.yaml`——候选 Claim 和画像版本由系统自己推断，这里只提供事实和证据。

校验：在仓库根目录执行 `uv run --project backend python examples/test-enterprises/validate.py hengchuan-auto`。
