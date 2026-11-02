栖光智联（模拟）是 NERE-19 合成测试数据集里的第 2 家企业。公司、人员、邮箱、Jira 身份、PLM 账号和全部记录都是虚构的，不含任何真实企业或真实员工信息。**本目录仅用于系统测试，不会被 `app.seed` 自动导入**（seed 加载器只读 `examples/demo-org/`）。

## 企业简介

做端侧视觉模组与整体方案：以瑞芯微 RK3588 为主控，自研主板、固件和端侧算法包，模型经 RKNN-Toolkit2 转换与量化后跑在 NPU 上。量产委托 EMS 代工厂，公司自身以研发和技术开发服务为主，因此 GB/T 4754 归在 **M 门类 7320 工程和技术研究和试验发展**，而不是电子设备制造。

主要产品线是模组 V3 / V3.1，一年内走完了原理图评审、EMC 与 ESD 整改、可靠性试验、DFM 与治具、小批试产和 CE/CCC 送检的完整节奏。

## 组织形态：职能制（functional）

六个职能部门按专业划分，不设独立项目部。四个项目（MODX、FW、EDGE、CERT）的负责人都由相关职能部门负责人**兼任**，项目成员仍然只向本部门负责人汇报，项目不改变汇报线。这是和第 1 家（矩阵制、产品经理牵头）最明显的对照：这里不存在「项目经理不是部门经理」的情况，取而代之的是**部门负责人兼项目负责人**（edge tag `dept_and_project_lead`），它带来的典型风险是部门负责人的画像把项目成员的产出吸附过来。

| 部门 | 负责人 | 在职人数 | 职责 | PCF 一级类别 |
| --- | --- | --- | --- | --- |
| 硬件研发部 | QG001 蒋沐川 | 5 | 原理图与 PCB、电源与防护、结构与散热 | 2.0 Develop and Manage Products and Services |
| 嵌入式软件部 | QG006 邵衍 | 4（另有 1 名离职） | BSP 与驱动、相机与 ISP、固件集成与 OTA | 2.0 Develop and Manage Products and Services |
| 端侧算法部 | QG011 聂云舒 | 5 | 模型训练与评估、RKNN 转换与量化、NPU 调优、算法包交付 | 2.0；13.0 Develop and Manage Business Capabilities |
| 测试与认证部 | QG016 傅岐 | 4 | 系统与固件测试、EMC 整改、可靠性试验、认证送检 | 2.0；11.0 Manage Enterprise Risk, Compliance, Remediation, and Resiliency |
| 供应链与制造工程部 | QG020 褚明野 | 4 | 元器件与二供、BOM 与 ECN、DFM、EMS 对接与试产 | 4.0 Deliver Physical Products；10.0 Acquire, Construct, and Manage Assets |
| 综合管理部 | QG024 魏芝瑜 | 4 | 人力与培训档案、行政与采购、内部 IT（Jira/PLM 管理） | 7.0 Develop and Manage Human Capital；8.0 Manage Information Technology (IT) |

合计 **27 人（在职 26 人，离职 1 人）**。职级：工程岗 P5–P7，管理岗 M2–M3，实习生单列。

项目：

| 项目 | 名称 | 负责人 | 说明 |
| --- | --- | --- | --- |
| MODX | 模组 V3 硬件平台 | QG001（硬件研发部负责人） | 兼任 |
| FW | 固件统一平台 2.0 | QG006（嵌入式软件部负责人） | 兼任 |
| EDGE | 端侧视觉算法包 V3 | QG011（端侧算法部负责人） | 兼任，固件与测试各派一人 |
| CERT | 模组 V3 认证与可靠性专项 | QG016（测试与认证部负责人） | 兼任，硬件与制造工程各派一人 |

## 数据来源

| 来源 | kind | 内容 | 规模 |
| --- | --- | --- | --- |
| Jira | `jira` | Jira Cloud 子集，项目 EDGE（端侧算法包 20 条）、FW（固件 16 条） | 36 条 issue |
| PLM | `work_records` | 自建 PLM：设计评审、ECN、BOM 变更、EMC 测试、可靠性测试、DFM 评审、认证送检、样机试产、系统同步 | 32 条记录 |

两个来源的覆盖面刻意不对称：**Jira 只记软件与算法任务，硬件、结构、制造、认证的工作全在 PLM**。硬件和制造岗在 Jira 里几乎不存在，职能岗（综合管理部）在两个来源里都近乎空白。这是本企业最重要的设计意图——「有没有记录」在这里强依赖岗位，评估系统很容易把硬件、制造、职能岗判成「没有证据 = 能力低」。

`org.yaml` 的 `account_id` 是 Jira accountId（`qg-jira-0NN`）；PLM 账号写在 `roster.yaml` 的 `external_accounts` 里，未登记的 PLM 账号写在 `personas.yaml` 的 `account_truth` 里。

## `work_records.csv` 列定义

UTF-8 无 BOM，逗号分隔，表头固定为 12 列：

| 列名 | 本企业的取值说明 |
| --- | --- |
| `record_id` | `PLM-0001` 起顺序编号，全企业唯一，不与 Jira key 重复 |
| `source` | 固定为 `PLM`，对应 `enterprise.yaml` 中 kind=work_records 的来源名 |
| `record_type` | 设计评审 / ECN / BOM 变更 / EMC 测试 / 可靠性测试 / DFM 评审 / 认证送检 / 样机试产 / 设计任务 / 系统同步 |
| `occurred_at` | ISO 8601 带 `+08:00`，落在 2025-11-01 至 2026-10-31 |
| `actor_account` | PLM 账号，可能是未登记账号（`ems.lin.h`、`plm.svc.sync`、`kanyu.old`） |
| `actor_name` | PLM 里的显示名，可能与花名册不同（`Kevin Tang`、`林工（EMS）`、`PLM 同步服务`） |
| `actor_role` | 评审主持人 / 变更发起人 / 测试负责人 / 认证负责人 / NPI 负责人 / 设计责任人 / 系统账号 |
| `participants` | 其他参与者，`账号:角色` 用 `;` 分隔，可空 |
| `project` | MODX / EDGE / CERT，可空 |
| `title` | 记录标题 |
| `detail` | 一两句纯文本说明，含频点、余量、时长、良率等可核对的数字 |
| `status` | 已关闭 / 已完成 / 已退回 |

## 登录账号

每位**在职**员工恰好一个登录账号，邮箱等于 `work_email`（域名 `@qiguang-aiot.example.com`）。离职的 QG010 没有账号。

| 邮箱 | 系统角色 | 员工 | 说明 |
| --- | --- | --- | --- |
| `shao.yun@qiguang-aiot.example.com` | `org_admin` | QG025 邵昀 | 综合管理部 IT 管理员，不是部门负责人；全企业唯一的 org_admin |
| `jiang.muchuan@qiguang-aiot.example.com` | `dept_manager` | QG001 蒋沐川 | 硬件研发部 |
| `shao.yan@qiguang-aiot.example.com` | `dept_manager` | QG006 邵衍 | 嵌入式软件部 |
| `nie.yunshu@qiguang-aiot.example.com` | `dept_manager` | QG011 聂云舒 | 端侧算法部 |
| `fu.qi@qiguang-aiot.example.com` | `dept_manager` | QG016 傅岐 | 测试与认证部 |
| `chu.mingye@qiguang-aiot.example.com` | `dept_manager` | QG020 褚明野 | 供应链与制造工程部 |
| `wei.zhiyu@qiguang-aiot.example.com` | `dept_manager` | QG024 魏芝瑜 | 综合管理部 |
| 其余 19 位在职员工 | 无 role | — | 普通员工账号 |

## 能力词典

`templates/capabilities/aiot-hardware.zh-CN.yaml`，16 项、5 个分类：硬件设计（4）、嵌入式软件（3）、端侧算法（4）、测试与认证（3）、制造与变更（2）。三档描述按本行业的具体工作行为写，例如 RKNN 转换的「独立完成复杂任务」要求能处理不支持算子的替换与拆图、设计混合量化配置、定位归一化与校准集分布导致的掉点。`certification_project` 一项明确写了「取得认证资质或证书是开展工作的前提，不等于具备该能力」。

词典只覆盖技术能力，综合管理部四人没有匹配项——这本身是测试点（见下表最后一行）。

## 本企业的边界情况

| tag | 涉及人员 | 数据上的体现 |
| --- | --- | --- |
| `multi_account` | QG007 唐知野、QG012 阚屿 | 唐知野 Jira `qg-jira-007` + PLM `kevin.tang`（已登记）；阚屿 Jira + PLM 旧账号 `kanyu.old`（未登记，只在 account_truth） |
| `unlinked_account` | 无（非员工账号） | `ems.lin.h` EMS 驻场工程师、`plm.svc.sync` 同步服务，都在 PLM 记录里出现但不属于任何员工 |
| `new_hire_no_history` | QG014 卞知吟 | 2026-09-14 入职，两个来源都没有她的账号，也没有自述；真实有两年模型训练经验 |
| `intern` | QG005 郗嘉禾 | 在读本科实习生，有真实产出但署名在带教老师名下 |
| `assignee_mismatch` | QG011/QG012、QG002/QG005、QG020/QG023 | EDGE-114 指派聂云舒实际阚屿做；PLM-0024 署名乔砚之实际实习生画了三版；PLM-0021 署名褚明野实际翁沐写影响评估 |
| `cross_source_duplicate` | QG017、QG019、QG012、QG007、QG023 | 同一次 ESD 整改在 PLM-0012、FW-215、EDGE-120 各有记录；固件与 BOM 对应关系同时在 FW-205 和 PLM-0028 |
| `review_conflict` | QG003、QG005、QG007 | 三组自述与经理评价矛盾，真实答案各不相同（自述夸大 / 经理低估 / 账号分裂导致的低估） |
| `english_name` | QG007 唐知野 | Jira 与 PLM 显示名都是 `Kevin Tang`，中文名只在花名册和邮箱里 |
| `dept_and_project_lead` | QG001、QG006、QG011、QG016 | 四个项目的负责人都是部门负责人本人 |
| `departed` | QG010 韩听澜 | 2026-06-30 离职，无登录账号，Jira 与 PLM 保留历史，后续由冉苜接手 |
| `transfer` | QG019 邓寒 | 2026-01-15 从嵌入式软件部转测试与认证部，两段岗位在两个来源都有痕迹 |
| `appeal` | QG003、QG007、QG023 | 三个申诉里有一个（苏棠）不成立，用来测申诉不等于自动改结论 |
| `manager_unsure` | QG009、QG014 | 经理答 `not_sure`，两人真实都到常规水平，系统应显示未知 |

额外的（未单独打标签的）测试点：

- **过期证书不等于能力下降**：QG003 的 EMC 培训证书（2025-09-30 过期）、QG013 的深度学习培训证书（2025-08-25 过期）、QG021 的 IPC-A-610（2026-07-14 过期）都还留在 HR 档案里。
- **管理者证据稀薄**：QG001 在 PLM 里只以评审主持人出现，没有一条他本人的设计记录；QG006 在 Jira 里几乎只是报告人。
- **服务账号不是人**：`plm.svc.sync` 跑的夜间批处理（PLM-0031）被错挂在 IT 管理员 QG025 的能力上，是一条刻意设计的 `misleading` 证据。
- **词典覆盖不全**：综合管理部四人在技术能力词典下基本只能填 `level: none` / `needs_support` 配 `undocumented`（QG025 多一条被服务账号误导的 `misleading`），用来检查系统会不会把空白呈现成「不会」。

## 证据与真实能力的差距

`personas.yaml` 的 `ground_truth` 不导入系统，用于核对系统推断的画像。本企业 73 条真实能力记录的证据分布：

| evidence | 条数 | 设计意图 |
| --- | --- | --- |
| `documented` | 23 | 记录、自述、经理评价一致，系统应能确认 |
| `partial` | 18 | 有记录但不足以定级（多为「以评审人/报告人身份出现」） |
| `undocumented` | 25 | 真实具备但系统里没有任何记录，`evidence_refs` 为空，系统应输出「未知」 |
| `misleading` | 7 | 署名错位、账号分裂、自述夸大、服务账号记录、过期证书 |

`undocumented` 占比偏高是刻意的：这家公司的 Jira 只覆盖软件，硬件与制造岗的大量工作、以及所有管理者的决策行为都没有系统痕迹。按计划书原则，这些应该呈现为「未知」，不能推断为能力低。

## 校验

从仓库根目录运行：

```
uv run --project backend python examples/test-enterprises/validate.py qiguang-aiot
```

本目录不含 `claims.yaml`、`profile_versions.yaml`、`progress.yaml`——候选 Claim 与画像版本由系统的 Profiling / Review 流程产生，不是数据集的输入。
