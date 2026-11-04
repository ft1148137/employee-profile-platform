# 测试数据集：10 家模拟企业

为系统测试准备的 10 家虚构企业（NERE-19）。项目做完后，这些人员由测试智能体扮演，像真实用户一样使用系统并提交 bug，由「技术规划」统一分诊。

企业、人员、邮箱、账号、客户、患者、设备和记录全部虚构，邮箱一律 `@<slug>.example.com`。这里不放任何真实员工信息、内部资料或凭据。这些数据**不会**在 `docker compose up` 时自动导入，seed 加载器也不读取本目录。

研究笔记和来源链接见 [research.md](research.md)。

## 规则起草评测

先按开发配置启动独立 PostgreSQL 并执行迁移，再从 `backend/` 运行 `PYTHONPATH=. uv run python ../examples/test-enterprises/evaluate_drafts.py qiguang-aiot --provider openai --model 模型名`（密钥用 `MODEL_API_KEY` 环境变量）。脚本使用与后端测试相同的数据库配置（环境变量优先于 `.env`），临时建立企业，用进程内模拟 Jira 和 CSV 起草，按在职员工/能力 key 输出 Markdown 精确率、召回率、证据状态分组及 misleading 明细，最后删除本企业续跑任务和临时企业。只把 `personas.yaml` 当离线答案，不导入系统，也不使用其中的 account_truth 关联身份。无通过门槛；记录见 [M2 基线](../../docs/m2-baseline.md)和 [M3 模型基线](../../docs/m3-baseline.md)。

## 10 家企业

| # | 企业 | 目录 | 行业 | GB/T 4754 | 组织形态 | 部门 | 在职/合计 | 工作记录来源（条数） | 能力词典 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 青栎云（模拟） | [`qingli-saas/`](qingli-saas/) | SaaS 软件（面向中小制造企业的采购协同） | I 6550 信息处理和存储支持服务 | 矩阵制 | 5 | 26/28 | Jira 38、Git 40 | `saas-software.zh-CN.yaml` |
| 2 | 栖光智联（模拟） | [`qiguang-aiot/`](qiguang-aiot/) | 智能硬件 / AIoT（端侧视觉模组与方案） | M 7320 工程和技术研究和试验发展 | 职能制 | 6 | 26/27 | Jira 36、PLM 32 | `aiot-hardware.zh-CN.yaml` |
| 3 | 衡川汽车零部件（模拟） | [`hengchuan-auto/`](hengchuan-auto/) | 汽车零部件制造（制动、转向机加工件，二级供应商） | C 3670 汽车零部件及配件制造 | 职能制（含车间） | 5 | 27/28 | MES 38、QMS 27 | `auto-parts-manufacturing.zh-CN.yaml` |
| 4 | 岚城银行汀州分行（模拟） | [`lancheng-bank/`](lancheng-bank/) | 城市商业银行分行 | J 6621 商业银行服务 | 矩阵制（条线 + 支行） | 6 | 27/29 | 信贷系统 25、柜面运营 20、培训平台 23 | `commercial-banking.zh-CN.yaml` |
| 5 | 沐安市中心医院（模拟） | [`muan-hospital/`](muan-hospital/) | 三级甲等综合医院 | Q 8411 综合医院 | 直线职能制（科室制） | 5 | 26/28 | 排班 16、病历质控 16、继续医学教育 14、手术记录 16 | `hospital.zh-CN.yaml` |
| 6 | 远衡管理咨询（模拟） | [`yuanheng-consulting/`](yuanheng-consulting/) | 管理咨询 | L 7243 社会经济咨询 | 项目制 | 5 | 25/27 | 工时系统 36、交付物库 28、项目复盘 10 | `management-consulting.zh-CN.yaml` |
| 7 | 筑岳建设工程（模拟） | [`zhuyue-construction/`](zhuyue-construction/) | 房屋建筑施工总承包 | E 4710 住宅房屋建筑 | 混合制（项目部 + 职能部门） | 5 | 26/28 | 施工日志 26、安全检查 16、质量验收 16 | `construction-general-contracting.zh-CN.yaml` |
| 8 | 禾邻超市（模拟） | [`helin-retail/`](helin-retail/) | 连锁超市 | F 5212 超级市场零售 | 事业部制（区域 + 门店） | 5 | 26/27 | 排班系统 24、POS 系统 20、巡店系统 18 | `chain-retail.zh-CN.yaml` |
| 9 | 岚江省电力公司（模拟） | [`lanjiang-grid/`](lanjiang-grid/) | 省级电网供电公司 | D 4420 电力供应 | 直线职能制 | 5 | 24/26 | 工作票系统 30、检修记录系统 21、安全培训考试系统 14 | `power-grid.zh-CN.yaml` |
| 10 | 汀兰大学（模拟） | [`tinglan-university/`](tinglan-university/) | 普通高等学校 | P 8341 普通高等教育 | 混合制（学院 + 职能处室） | 5 | 27/28 | 教务系统 34、科研管理系统 22、学生工作系统 14 | `higher-education.zh-CN.yaml` |

合计 52 个部门、**在职 260 人 / 合计 276 人**（16 人已离职，只在历史记录里出现）。74 条 Jira issue、596 条其他工作记录、150 条自述、181 条经理评价。只有前两家用 Jira，其余 8 家完全不用 Jira，用来验证「没有 Jira 也能形成画像」。

每家企业 4–6 个部门，每个部门 1 名在职部门负责人（`dept_manager`）和至少 3 名在职员工。每家有 1 名企业管理员（`org_admin`），是人力或 IT 部门的普通员工，负责在系统里建企业、导入人员和配置数据源。能力词典在 `templates/capabilities/`，每个行业 16 项，结构与 `tech-rnd.zh-CN.yaml` 一致，也可作为计划书 §6.1 的行业参考模板。

## 真实能力与证据差距

`personas.yaml` 第一行注明「测试用，不导入系统」。每人至少 2 项真实能力（ground truth），全数据集 710 项，用来核对系统推断的画像，对应计划书 §10.1「关键能力核实准确率」。每项标明证据状态：

| 证据状态 | 含义 | 项数 |
| --- | --- | --- |
| `documented` | 记录充分且准确，系统应能得出接近真实的结论 | 252 |
| `partial` | 有记录，但不足以确认等级 | 179 |
| `undocumented` | 真实具备，系统里没有任何记录；系统应输出「未知」，不是「不会」 | 208 |
| `misleading` | 记录会误导：指派人不是实际贡献者、证书过期、自述夸大、经理低估、同名混淆、公用账号 | 71 |

不生成 `claims.yaml`、`profile_versions.yaml`、`progress.yaml`。这些由系统产出，测试就是看它产出得对不对。

## 边界情况分布

每种至少出现在 2 家企业。具体人员和记录见各企业 `personas.yaml` 的 `edge_cases` 和 `README.md`。

| 边界情况 | SaaS | AIoT | 汽配 | 银行 | 医院 | 咨询 | 建筑 | 零售 | 电网 | 高校 | 企业数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 同名不同人 `same_name` | ✓ |  |  |  | ✓ |  | ✓ | ✓ |  |  | 4 |
| 同一人多个外部账号 `multi_account` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 外部账号未关联员工 `unlinked_account` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 转岗 `transfer` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 跨部门兼岗 `concurrent_post` | ✓ |  |  | ✓ | ✓ | ✓ | ✓ |  | ✓ | ✓ | 7 |
| 离职 `departed` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 新入职且无历史记录 `new_hire_no_history` | ✓ | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ | 7 |
| 外包 `contractor` | ✓ |  |  |  |  |  | ✓ | ✓ | ✓ | ✓ | 5 |
| 实习生 `intern` | ✓ | ✓ | ✓ |  | ✓ | ✓ | ✓ | ✓ |  | ✓ | 8 |
| 指派人与实际贡献者不一致 `assignee_mismatch` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 同一事项在多个来源重复 `cross_source_duplicate` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 经理评价与自述矛盾 `review_conflict` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 员工要申诉 `appeal` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 经理「不确定」 `manager_unsure` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| 一线员工不会填表 `no_forms` |  |  | ✓ |  |  |  | ✓ | ✓ | ✓ |  | 4 |
| 只用手机 `mobile_only` |  |  |  |  |  |  | ✓ | ✓ | ✓ |  | 3 |
| 只会口语化描述 `colloquial` |  |  | ✓ |  | ✓ |  | ✓ | ✓ | ✓ | ✓ | 6 |
| 复姓 `compound_surname` | ✓ |  |  | ✓ |  | ✓ | ✓ |  |  | ✓ | 5 |
| 少数民族姓名 `ethnic_minority_name` |  |  |  |  | ✓ |  |  | ✓ | ✓ | ✓ | 4 |
| 英文名 `english_name` | ✓ | ✓ |  |  | ✓ | ✓ |  |  |  | ✓ | 5 |
| 生僻字 `rare_character` |  |  |  |  | ✓ |  |  |  | ✓ | ✓ | 3 |
| 部门负责人兼项目负责人 `dept_and_project_lead` |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  | ✓ | ✓ | 8 |
| 项目经理不是部门经理 `pm_not_dept_manager` | ✓ |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 9 |

生僻字中，医院的「䶮」（U+4D6E）和电网的「㵘」（U+3D18）在 CJK 扩展 A，高校的「𤭢」（U+24B62）在 CJK 扩展 B，UTF-8 占 4 字节，用来测数据库和前端对 BMP 外字符的处理。

## 文件

每家企业一个子目录。与 `examples/demo-org/` 同格式的文件可以直接用 `backend/app/seed/schema.py` 的模型解析。

| 文件 | 内容 | 格式 |
| --- | --- | --- |
| `README.md` | 企业简介、组织形态、部门、数据来源、`work_records.csv` 列定义、登录账号、边界情况 | — |
| `org.yaml` | 部门、员工（`account_id` 是主工作记录来源里的账号）、登录账号与系统角色 | 同 demo-org |
| `self_reports.yaml`、`manager_reviews.yaml` | 员工自述、经理（或项目经理）评价 | 同 demo-org |
| `jira/issues.json` | 仅青栎云、栖光智联 | 同 demo-org |
| `work_records.csv` | 非 Jira 来源的工作记录，12 列固定，列定义见各企业 README | 新增 |
| `enterprise.yaml` | 行业、GB/T 4754 分类、组织形态、数据来源、部门与 APQC PCF 对应、项目 | 新增 |
| `roster.yaml` | 职级、入职/离职、汇报对象（含第二汇报人）、系统角色、用工类型、兼岗、转岗、已登记的外部账号、HR 档案里的证书 | 新增 |
| `personas.yaml` | 工作经历、真实能力、使用画像、未登记账号的真实归属（`account_truth`）、边界情况 | 新增，测试用，不导入系统 |

证据引用写作 `jira:<KEY>`、`record:<record_id>`、`self_report:<id>`、`manager_review:<id>`。

## 可直接上传的 M1 名单

每家目录的 `roster.csv` 是 UTF-8 带 BOM 的六列模板（工号、姓名、部门、职位、上级工号、邮箱），共 260 名在职员工。管理员在首次设置或「管理 → 导入名单」直接选择对应文件。只上传本企业的全量名单，不要混用；同一部署中各企业管理员登录名也要不同。

生成器从 `org.yaml` 取姓名、部门、职位、邮箱，从 `roster.yaml` 只选 `status: active` 并取 `reports_to`。不包含已离职人员、第二汇报人、兼岗、职级或测试用真实能力；角色也不会随文件自动授权。

```bash
uv run --project backend python examples/test-enterprises/export_rosters.py
uv run --project backend python examples/test-enterprises/validate.py
```

生成时调用正式名单解析器，遇错误拒绝导出。校验器还比较 CSV 与 YAML 的生成结果，防止提交过期文件。修改 YAML 后要重新生成并提交 CSV。

## 校验

在仓库根目录运行：

```bash
uv run --project backend python examples/test-enterprises/validate.py            # 全部 10 家 + 数据集级检查
uv run --project backend python examples/test-enterprises/validate.py helin-retail
uv run --project backend python examples/test-enterprises/validate.py --summary  # 打印一览表和边界情况分布
```

`validate.py` 只读文件，不连数据库。它用 seed 模型校验同格式文件，再检查部门与人数下限、汇报关系、登录账号与角色、账号引用、证据引用、真实能力的证据分布、每个边界标签对应的数据条件，以及数据集级的门类不重复、至少 3 家不用 Jira、组织形态覆盖、在职人数不少于 200。

## 接入 Multica AI 团队

`agent_brief.py` 把一家企业的数据生成一份测试智能体说明（Markdown），可直接作为 Multica 智能体的 `instructions`：

```bash
uv run --project backend python examples/test-enterprises/agent_brief.py lanjiang-grid
```

说明包含企业概况、测试规则、bug 提交格式（指派给「技术规划」，标题以 `[<slug>]` 开头）、全部人员的设备/数字素养/态度、每个人的表达风格、测试关注点和真实能力，以及本企业的边界情况。说明由数据生成，数据改了重新生成即可。登录密码不在说明里，测试时从环境变量读取。

建议的团队结构：每家企业一个测试智能体，轮流扮演本企业的人员；一个测试协调智能体任小队负责人，按企业派发测试、合并重复 bug，再把 bug 交给「技术规划」分诊。这些智能体和小队还没有创建，等确认后再建。

## 与 issue 建议表相比的调整

| 调整 | 理由 |
| --- | --- |
| AIoT 公司归 M 7320，定位为以研发和技术开发服务为主、量产委托 EMS | 自产硬件会和汽车零部件同属 C 制造业，而 10 家要分属不同门类。I 6532 物联网技术服务也不行，I 门类已给 SaaS |
| SaaS 公司归 I 6550 信息处理和存储支持服务，不用 6513 应用软件开发 | 6550 的注释包含应用软件租用服务和在线 ERP，更贴近按订阅在线交付的 SaaS |
| 组织形态细化为 `functional`、`line_functional`、`divisional`、`matrix`、`projectized`、`hybrid` | 便于校验和测试：医院、电网用直线职能制；建筑、高校用混合制；银行的条线 + 支行按矩阵制建模 |
| 只有 2 家用 Jira | issue 要求至少 3 家不用 Jira；其余 8 家各用 2–4 个行业系统 |
| 增加数据来源：SaaS 加 Git、AIoT 加 PLM、汽配拆成 MES 与 QMS、医院加手术记录、咨询加项目复盘、建筑加质量验收、电网加安全培训考试 | 让「同一事项在多个来源重复」「指派人与实际贡献者不一致」有真实的落点 |
| 电工进网作业许可证只作为一张过期的 HR 档案证书出现 | 「2017 年已取消」没能找到可访问的官方原文核实（见 research.md），所以不断言现行状态 |
