"""Print the Multica tester-agent brief for one test enterprise (NERE-19).

Run from the repository root:

    uv run --project backend python examples/test-enterprises/agent_brief.py helin-retail

The output is Markdown meant for the ``instructions`` field of a Multica agent
that plays this enterprise's people. It is generated from the dataset so the
brief cannot drift from the data. It reads files only and prints to stdout.
"""

import sys

from validate import DATA, DatasetError, Loaded, check, load

TRIAGE_AGENT = "技术规划"
ROLE_LABELS = {
    "org_admin": "企业管理员",
    "dept_manager": "部门负责人",
    "employee": "员工",
}
LEVEL_LABELS = {
    "none": "不具备",
    "needs_support": "需要协助",
    "routine": "能完成常规任务",
    "independent_complex": "能独立完成复杂任务",
}
EVIDENCE_LABELS = {
    "documented": "记录充分",
    "partial": "记录不足",
    "undocumented": "没有记录",
    "misleading": "记录会误导",
}
DEVICE_LABELS = {
    "pc": "电脑",
    "mobile": "手机",
    "tablet": "平板",
    "shared_terminal": "公用终端",
}
LITERACY_LABELS = {"high": "高", "medium": "中", "low": "低"}
STRUCTURE_LABELS = {
    "functional": "职能制",
    "line_functional": "直线职能制",
    "divisional": "事业部制",
    "matrix": "矩阵制",
    "projectized": "项目制",
    "hybrid": "混合制",
}
ATTITUDE_LABELS = {
    "positive": "积极",
    "neutral": "中立",
    "skeptical": "怀疑",
    "worried_about_evaluation": "担心被考核",
}


class BriefError(Exception):
    """The requested enterprise is missing or its data does not validate."""


def cell(text: str) -> str:
    return text.replace("|", "／").replace("\n", " ")


def brief(data: Loaded) -> str:
    ent = data.enterprise
    employees = {e.employee_code: e for e in data.org.employees}
    roster = {r.employee_code: r for r in data.roster}
    names = {c.key: c.name for c in data.capabilities}
    sources = "、".join(s.name for s in ent.sources)
    label = STRUCTURE_LABELS[ent.org_structure]
    note = ent.org_structure_note
    structure = note if note.startswith(label) else f"{label}。{note}"
    admin = next(r.employee_code for r in data.roster if r.system_role == "org_admin")
    lines = [
        f"## 你的角色：{ent.name} 的测试智能体",
        "",
        (
            f"你扮演 {ent.name} 的员工，像真实用户一样使用「企业员工画像与跨部门协同平台」，"
            f"发现问题后提交 bug，由「{TRIAGE_AGENT}」统一分诊。"
        ),
        f"企业数据在仓库 `examples/test-enterprises/{ent.slug}/`，全部虚构。",
        "",
        "## 企业概况",
        "",
        (
            f"- 行业：{ent.industry}（GB/T 4754 {ent.gbt4754.section} {ent.gbt4754.code} "
            f"{ent.gbt4754.class_name}）"
        ),
        f"- 组织形态：{structure}",
        f"- 部门：{'、'.join(data.org.departments)}",
        f"- 工作记录来源：{sources}" + ("" if ent.uses_jira else "（本企业不用 Jira）"),
        f"- 能力词典：`{ent.capability_template}`",
        "",
        "## 测试规则",
        "",
        (
            "1. 每次测试只扮演一个人。开始时写明扮演谁，按该人的设备、数字素养、"
            "表达风格和态度操作和说话。只用手机的人只用移动端；不会填表的人只用口语描述。"
        ),
        (
            f"2. 第一轮由企业管理员 {admin} {employees[admin].display_name} 建企业、"
            "导入人员、配置数据源和能力词典。系统缺少这些功能时，按 bug 或功能缺口提交。"
        ),
        "3. 只用本目录里的数据。登录密码从测试环境变量读取，不要写进 issue 或评论。",
        (
            "4. 「真实能力」只有你扮演的这个人自己知道。用它判断系统画像对不对，"
            "不要把真实能力整段输入系统；输入内容要符合人设平时会说的话。"
        ),
        (
            "5. 系统把「没有记录」说成「不会」、把指派人当成实际贡献者、把持证当成能力、"
            "把同名的人混在一起，都按画像错误提交。"
        ),
        "6. 不改代码、不改数据文件、不合并 PR。",
        "",
        "## 提交 bug",
        "",
        "M3 每名在职员工分别测试四种更新方式：网页表单、内置机器人、外部 MCP 助手（OAuth 和个人令牌）、skill 生成的 JSON。",
        "每次先过目初始画像，再补充本人合成经历并提交，核对待经理审批且能撤回；机器人必须点预览卡片的确认按钮，聊天说确认不能提交。",
        "手机跑表单和机器人；不得冒用其他员工或读取其他企业的请求。JSON 只能填写当前扮演员工的邮箱，不上传 ground_truth。",
        "",
        f"在 Multica 建 issue，指派给「{TRIAGE_AGENT}」。标题以 `[{ent.slug}]` 开头。正文包含：",
        (
            "扮演的人员（工号、姓名、系统角色）、设备、复现步骤、期望结果、实际结果、"
            "相关数据引用（如 `record:GZP-0001`、`jira:PUR-201`）、影响范围。"
        ),
        "先搜同企业已有 issue，重复的问题在原 issue 下补充，不另建。",
        "",
        "## 人员",
        "",
        "| 工号 | 姓名 | 部门 | 职位 | 系统角色 | 状态 | 设备 | 数字素养 | 态度 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for persona in data.personas.personas:
        code = persona.employee_code
        row, entry, usage = employees[code], roster[code], persona.usage_profile
        devices = "、".join(DEVICE_LABELS[d] for d in usage.devices)
        status = "在职" if entry.status == "active" else f"已离职 {entry.left_on}"
        lines.append(
            f"| {code} | {row.display_name} | {row.department} | {cell(row.title)}"
            f" | {ROLE_LABELS[entry.system_role]} | {status} | {devices}"
            f" | {LITERACY_LABELS[usage.digital_literacy]} | {ATTITUDE_LABELS[usage.attitude]} |"
        )
    lines += ["", "## 每个人的使用画像与真实能力", ""]
    for persona in data.personas.personas:
        code = persona.employee_code
        row, usage = employees[code], persona.usage_profile
        lines += [
            f"### {code} {row.display_name}（{row.department} · {row.title}）",
            "",
            f"- 邮箱登录（工号仅为员工编号）：`{row.work_email}`"
            if roster[code].status == "active"
            else "- 已离职，没有登录账号，只在历史记录里出现",
            f"- 表达风格：{usage.expression_style}",
            f"- 测试关注点：{'；'.join(usage.test_focus)}",
            "- 真实能力：",
        ]
        lines += [
            f"  - {names.get(c.key, c.key)}：{LEVEL_LABELS[c.level]}"
            f"（{EVIDENCE_LABELS[c.evidence]}）。{c.note}"
            for c in persona.ground_truth.capabilities
        ]
        if persona.ground_truth.certifications:
            lines.append(
                f"- 实际持有的证书：{'、'.join(persona.ground_truth.certifications)}"
            )
        lines.append("")
    lines += ["## 本企业的边界情况", ""]
    lines += [
        f"- `{case.tag}`：{'、'.join(case.employees) or '—'}。{case.note}"
        for case in data.personas.edge_cases
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: agent_brief.py <slug>\n")
        return 2
    try:
        path = DATA / argv[0]
        if not (path / "org.yaml").exists():
            raise BriefError(f"unknown enterprise: {argv[0]}")
        data = load(path)
        errors = check(data)
        if errors:
            raise BriefError("; ".join(errors))
    except (BriefError, DatasetError) as exc:
        sys.stderr.write(f"ERROR {exc}\n")
        return 1
    sys.stdout.write(brief(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
