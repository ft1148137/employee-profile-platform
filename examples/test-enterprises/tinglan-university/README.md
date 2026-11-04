汀兰大学（模拟）是 NERE-19 合成测试数据集中的第 10 家企业，slug `tinglan-university`。企业、人员、邮箱、项目、工作记录全部虚构，不对应任何真实学校、教师或学生。**本目录仅用于系统测试，不会被 `app.seed` 自动导入**；`personas.yaml` 里的真实能力是核对用的 ground truth，不进系统。

## 企业简介

地方综合性大学，本数据集只取两个学院和三个职能处室。行业为普通高等学校，GB/T 4754 分类 P 教育 / 8341 普通高等教育。能力词典为 `templates/capabilities/higher-education.zh-CN.yaml`（16 项，4 个 category）。

## 组织形态

`hybrid`（混合制）：学院与职能处室双线。学院按学科管人管课，职能处室按业务条线管流程。科研项目负责人（PI）由教师担任、与行政职务无关，所以出现三种容易混淆的身份：

- 教授兼科研处副处长（TL002），在学院和处室各有一条汇报线；
- 纵向项目 PI（TL002）、横向项目负责人（TL009）、大创指导教师（TL004）都不是部门负责人；
- 机械工程学院院长（TL008）同时是省级平台建设项目负责人，部门负责人与项目负责人重合。

学校真实的人事管理在人事处，但本数据集不设人事处（只取 5 个部门，校验要求 4–6 个）。企业管理员（org_admin）因此放在信息化办公室，`hr_it_departments` 写 `[信息化办公室]`，由系统运维工程师 TL025 应念担任——她是普通员工，不是部门负责人。

## 部门

| 部门 | 负责人 | 在职人数 | 职责 | PCF 一级类别 |
| --- | --- | --- | --- | --- |
| 计算机科学与技术学院 | TL001 欧阳昀（院长） | 7 | 计算机类教学、学科科研、专业实验室与学生培养 | 5.0 Deliver Services；2.0 Develop and Manage Products and Services |
| 机械工程学院 | TL008 诸葛彦（院长） | 6 | 机械类教学、工程训练与虚拟仿真实验、横向技术服务 | 5.0 Deliver Services；2.0 Develop and Manage Products and Services |
| 学生工作部 | TL014 柏松（部长） | 5 | 辅导员队伍管理、学业帮扶、奖助评审、突发事件处置 | 6.0 Manage Customer Service；5.0 Deliver Services |
| 科研处 | TL019 上官宁（处长） | 5 | 纵向项目组织、横向合同与知识产权、经费合规与成果统计 | 2.0 Develop and Manage Products and Services；13.0 Develop and Manage Business Capabilities |
| 信息化办公室 | TL024 郎津（主任） | 4 | 业务系统建设与运维、账号权限、数据核对 | 8.0 Manage Information Technology (IT) |

合计 27 名在职人员 + 1 名离职人员（TL028 甘棠，原计算机科学与技术学院讲师）。职称与职级按高校体系：助教 / 讲师 / 副教授 / 教授，实验师 / 高级实验师，辅导员三级 / 四级，管理七级至十级，另有研究生助管与劳务派遣岗。

## 科研项目

`enterprise.yaml` 的 `projects` 记录 4 个跨部门项目：纵向项目 KJ2026-01（PI 为 TL002）、横向项目 HX2026-07（负责人 TL009）、平台建设项目 PT2026-03（负责人 TL008）、本科生创新训练项目 DC2026-15（指导教师 TL004）。

## 数据来源

本企业不使用 Jira，三个来源都写入 `work_records.csv`（共 70 条，时间落在 2025-11-01 至 2026-10-31，时区 +08:00）：

| 来源名 | record_id 前缀 | 记录类型 | 条数 |
| --- | --- | --- | --- |
| 教务系统 | `JW-` | 排课申报、调课申请、停课补课、成绩录入、成绩复核、成绩归档、实验教学、教学检查、听课记录、课程大纲修订、毕业设计选题、毕业设计答辩、试卷归档、教学情况核查、系统运维 | 34 |
| 科研管理系统 | `KY-` | 项目申报、项目立项、项目变更、中期检查、结题验收、横向合同、经费审核、专利申请、成果登记、成果奖申报、论文登记、实验室开放项目、大创项目立项、平台建设、学术活动 | 22 |
| 学生工作系统 | `XG-` | 谈心谈话、奖助评审、资助材料审核、宿舍走访、突发事件处置、评优公示、学风检查、主题班会、资源转介、大创项目配套 | 14 |

学生工作记录只保留过程信息（主题、人次、班级数量、时间节点），**不含任何学生姓名、学号、联系方式、健康或家庭信息**。

另有 15 条自述（`self_reports.yaml`，`submitted_at` 在 2026-09-01 至 2026-10-31）和 20 条经理评价（`manager_reviews.yaml`，`reviewed_at` 在 2026-10-01 至 2026-10-31）。

## `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，首行表头固定为 12 列：

| 列名 | 含义 |
| --- | --- |
| `record_id` | 企业内唯一的记录编号，前缀对应来源（`JW-`、`KY-`、`XG-`） |
| `source` | 来源名，取 `enterprise.yaml` 中 `kind: work_records` 的 `sources[].name` |
| `record_type` | 记录类型，如 调课申请、成绩录入、中期检查、奖助评审 |
| `occurred_at` | 发生时间，ISO 8601 带 `+08:00` |
| `actor_account` | 来源系统里的执行人账号（工号、科研系统账号或未登记账号） |
| `actor_name` | 来源系统里的显示名，可能与花名册不同（如 `David Whitlock`、`郝工`、`学工助理`） |
| `actor_role` | 执行人在这条记录里的角色，如 课程负责人、项目负责人、辅导员、系统账号 |
| `participants` | 其他参与者，`账号:角色` 用 `;` 分隔，可为空 |
| `project` | 项目编号，取 `enterprise.yaml` 的 `projects[].code`，可为空 |
| `title` | 记录标题 |
| `detail` | 一两句纯文本描述，不含个人隐私 |
| `status` | 来源系统里的状态，如 已通过、已审批、已完成、已提交、进行中、已关闭 |

字段内一律不使用英文逗号，避免 CSV 引号转义。

## 登录账号

每位在职员工恰好一个登录账号，邮箱等于 `work_email`（域名 `@tinglan-university.example.com`）。离职人员没有登录账号。

| 邮箱 | 身份 | 员工 | 部门 |
| --- | --- | --- | --- |
| `tl025@tinglan-university.example.com` | org_admin | TL025 应念 | 信息化办公室 |
| `tl001@tinglan-university.example.com` | dept_manager | TL001 欧阳昀 | 计算机科学与技术学院 |
| `tl008@tinglan-university.example.com` | dept_manager | TL008 诸葛彦 | 机械工程学院 |
| `tl014@tinglan-university.example.com` | dept_manager | TL014 柏松 | 学生工作部 |
| `tl019@tinglan-university.example.com` | dept_manager | TL019 上官宁 | 科研处 |
| `tl024@tinglan-university.example.com` | dept_manager | TL024 郎津 | 信息化办公室 |
| 其余 21 个邮箱 | 普通员工（不写 role） | TL002–TL007、TL009–TL013、TL015–TL018、TL020–TL023、TL026、TL027 | 各部门 |

邮箱用姓名拼音生成，外籍教师 TL004 用英文名 `david.whitlock@`，是拼音规则的例外。

## 本企业的边界情况

| tag | 涉及员工 | 数据上怎么体现 |
| --- | --- | --- |
| `multi_account` | TL002、TL005、TL009、TL012 | 教务工号 + 科研管理系统账号；TL005 用的是完全未登记的 `KY-OUT-0391` |
| `transfer` | TL012 | 2026-03-01 科研处转机械工程学院，前后两个系统、两个账号，中间没有线索相连 |
| `concurrent_post` | TL002、TL016 | 教授兼科研处副处长；辅导员驻计算机科学与技术学院 |
| `cross_source_duplicate` | TL004、TL016 | 同一个大创项目 DC2026-15 在科研管理系统与学生工作系统各有一条记录 |
| `manager_unsure` | TL005、TL006、TL012 | 三条 `answer: not_sure`，不确定的原因各不相同 |
| `compound_surname` | TL001、TL008、TL019 | 欧阳、诸葛、上官 |
| `rare_character` | TL005、TL011 | 「𤭢」U+24B62 在 BMP 之外（CJK 扩展 B，UTF-8 4 字节）；「铖」在 BMP 内作对照 |
| `english_name` | TL004 | 花名册写「戴伟（David Whitlock）」，来源记录里是纯英文名 |
| `pm_not_dept_manager` | TL002、TL004、TL009 | 三位项目负责人都不是部门负责人 |
| `dept_and_project_lead` | TL008 | 机械工程学院院长兼平台建设项目负责人 |
| `unlinked_account` | 无（账号层面） | `jw-sync`、`xg-assist-07` 在记录里是执行人，但不属于任何员工 |
| `assignee_mismatch` | TL003、TL007 | 成绩录入挂课程负责人名下，实际由研究生助管完成 |
| `review_conflict` | TL017 | 自述称能独立组织评审，部长评价为需要协助 |
| `appeal` | TL017 | 她认为评价漏算了今年的工作，准备附两条记录申诉 |
| `departed` | TL028 | 2026-06-30 离职，无登录账号，两个系统留有历史记录 |
| `intern` | TL007 | 研究生助管按实习生身份登记 |
| `contractor` | TL027 | 网络与信息安全工程师为劳务派遣 |
| `new_hire_no_history` | TL023 | 2026-09-07 入职，无记录、无自述、无评价 |
| `ethnic_minority_name` | TL015 | 维吾尔族姓名，带间隔号 |
| `colloquial` | TL005、TL015 | 自述口语化，一人习惯语音转文字 |

## 证据与真实能力的差距（刻意设计）

`personas.yaml` 的 `ground_truth` 与系统能看到的证据之间留了四类差距：

1. **记录挂错人**：《数据结构》成绩录入记录在课程负责人名下，实际由研究生助管完成，一个被高估、一个被低估（`JW-0004`、`JW-0025`）。
2. **账号认不出人**：实验师的论文与开放基金项目用的是未登记账号 `KY-OUT-0391`、显示名「郝工」，成果在系统里无主，连经理都回答不确定（`KY-0004`、`KY-0014`、`MR18`）。
3. **转岗造成断层**：TL012 在科研处独立组织过 31 份申报，转岗后学院侧看不到任何科研记录，新任院长只能回答不确定（`KY-0009`、`KY-0010`、`MR07`）。
4. **线下工作没有记录**：院长的新教师试讲指导、教师兼任的班级学业导师、处长的权属争议谈判、部门负责人的纸质安全检查表，都是真实具备但系统里零记录的能力，系统应输出「未知」而不是「不会」。

另外还设计了两类资质陷阱：实验师 TL005 的危化品安全培训合格证 2024 年底已过期未复训（能力不应因此判低），知识产权岗 TL021 持专利代理师资格但权属争议仍需处长出面（持证不等于能力）。

## 校验

从仓库根目录运行：

```
uv run --project backend python examples/test-enterprises/validate.py tinglan-university
```

不生成 `claims.yaml`、`profile_versions.yaml`、`progress.yaml`——候选 Claim 与画像版本由系统自己推断，本数据集只提供来源事实与核对用的 ground truth。
