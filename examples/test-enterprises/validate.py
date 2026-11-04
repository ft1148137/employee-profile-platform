"""Validate the synthetic test-enterprise dataset (NERE-19).

Run from the repository root:

    uv run --project backend python examples/test-enterprises/validate.py
    uv run --project backend python examples/test-enterprises/validate.py qingli-saas
    uv run --project backend python examples/test-enterprises/validate.py --summary

Files that share a format with ``examples/demo-org`` are checked with the
models in ``backend/app/seed/schema.py``. The extra files (enterprise.yaml,
roster.yaml, personas.yaml, work_records.csv) are checked with the models
below. Nothing is written and nothing is imported into a database.
"""

import csv
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from app.seed.schema import Capability, Jira, ManagerReview, Org, SelfReport
from export_rosters import roster_csv
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent
TODAY = date(2026, 11, 2)
NEW_HIRE_SINCE = date(2026, 8, 1)
LEVELS = ["needs_support", "routine", "independent_complex"]
FORBIDDEN_FILES = ["claims.yaml", "profile_versions.yaml", "progress.yaml"]
PERSONA_HEADER = "# 测试用，不导入系统"
RECORD_COLUMNS = [
    "record_id",
    "source",
    "record_type",
    "occurred_at",
    "actor_account",
    "actor_name",
    "actor_role",
    "participants",
    "project",
    "title",
    "detail",
    "status",
]
EDGE_TAGS = (
    "same_name",
    "multi_account",
    "unlinked_account",
    "transfer",
    "concurrent_post",
    "departed",
    "new_hire_no_history",
    "contractor",
    "intern",
    "assignee_mismatch",
    "cross_source_duplicate",
    "review_conflict",
    "appeal",
    "manager_unsure",
    "no_forms",
    "mobile_only",
    "colloquial",
    "compound_surname",
    "ethnic_minority_name",
    "english_name",
    "rare_character",
    "dept_and_project_lead",
    "pm_not_dept_manager",
)
EdgeTag = Literal[
    "same_name",
    "multi_account",
    "unlinked_account",
    "transfer",
    "concurrent_post",
    "departed",
    "new_hire_no_history",
    "contractor",
    "intern",
    "assignee_mismatch",
    "cross_source_duplicate",
    "review_conflict",
    "appeal",
    "manager_unsure",
    "no_forms",
    "mobile_only",
    "colloquial",
    "compound_surname",
    "ethnic_minority_name",
    "english_name",
    "rare_character",
    "dept_and_project_lead",
    "pm_not_dept_manager",
]
OrgStructure = Literal[
    "functional",
    "line_functional",
    "divisional",
    "matrix",
    "projectized",
    "hybrid",
]


class DatasetError(Exception):
    """A file is missing or cannot be parsed."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# enterprise.yaml


class Gbt4754(Strict):
    section: str = Field(pattern=r"^[A-T]$")
    section_name: str
    code: str = Field(pattern=r"^\d{4}$")
    class_name: str


class Source(Strict):
    name: str
    kind: Literal["jira", "work_records"]
    description: str


class DepartmentNote(Strict):
    name: str
    pcf_categories: list[str] = Field(min_length=1)
    responsibilities: str


class Project(Strict):
    code: str
    name: str
    manager: str
    members: list[str] = Field(min_length=1)
    note: str = ""


class Enterprise(Strict):
    slug: str
    name: str
    industry: str
    gbt4754: Gbt4754
    org_structure: OrgStructure
    org_structure_note: str
    capability_template: str
    uses_jira: bool
    sources: list[Source] = Field(min_length=1)
    hr_it_departments: list[str] = Field(min_length=1)
    departments: list[DepartmentNote]
    projects: list[Project] = Field(default_factory=list)


# roster.yaml


class ExternalAccount(Strict):
    source: str
    account_id: str


class Transfer(Strict):
    effective_on: date
    from_department: str
    from_title: str
    to_department: str


class Certification(Strict):
    name: str
    issued_on: date | None = None
    valid_until: date | None = None
    note: str = ""


class RosterEntry(Strict):
    employee_code: str
    work_email: str
    grade: str
    hired_on: date
    reports_to: str | None
    secondary_reports_to: list[str] = Field(default_factory=list)
    system_role: Literal["org_admin", "dept_manager", "employee"]
    employment_type: Literal["full_time", "contractor", "intern"]
    status: Literal["active", "departed"]
    left_on: date | None = None
    secondary_departments: list[str] = Field(default_factory=list)
    transfers: list[Transfer] = Field(default_factory=list)
    external_accounts: list[ExternalAccount] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)


# personas.yaml


class WorkHistory(Strict):
    period: str
    org: str
    role: str
    summary: str


class TrueCapability(Strict):
    key: str
    level: Literal["none", "needs_support", "routine", "independent_complex"]
    evidence: Literal["documented", "partial", "undocumented", "misleading"]
    evidence_refs: list[str] = Field(default_factory=list)
    note: str


class GroundTruth(Strict):
    capabilities: list[TrueCapability] = Field(min_length=2)
    certifications: list[str] = Field(default_factory=list)
    notes: str = ""


class UsageProfile(Strict):
    digital_literacy: Literal["high", "medium", "low"]
    devices: list[Literal["pc", "mobile", "tablet", "shared_terminal"]] = Field(
        min_length=1
    )
    expression_style: str
    attitude: Literal["positive", "neutral", "skeptical", "worried_about_evaluation"]
    test_focus: list[str] = Field(min_length=1)


class Persona(Strict):
    employee_code: str
    display_name: str
    work_history: list[WorkHistory] = Field(min_length=1)
    ground_truth: GroundTruth
    usage_profile: UsageProfile


class AccountTruth(Strict):
    source: str
    account_id: str
    owner: str | None
    note: str


class EdgeCase(Strict):
    tag: EdgeTag
    employees: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    note: str


class Personas(Strict):
    slug: str
    personas: list[Persona]
    account_truth: list[AccountTruth] = Field(default_factory=list)
    edge_cases: list[EdgeCase] = Field(min_length=1)


# work_records.csv


class WorkRecord(Strict):
    record_id: str = Field(min_length=1)
    source: str
    record_type: str = Field(min_length=1)
    occurred_at: AwareDatetime
    actor_account: str = Field(min_length=1)
    actor_name: str = Field(min_length=1)
    actor_role: str
    participants: str
    project: str
    title: str = Field(min_length=1)
    detail: str
    status: str


@dataclass(frozen=True)
class Loaded:
    """Everything read from one enterprise directory."""

    path: Path
    enterprise: Enterprise
    org: Org
    roster: list[RosterEntry]
    personas: Personas
    self_reports: list[SelfReport]
    manager_reviews: list[ManagerReview]
    jira: Jira | None
    records: list[WorkRecord]
    capabilities: list[Capability]


def load_yaml[T](path: Path, kind: type[T]) -> T:
    try:
        return TypeAdapter(kind).validate_python(yaml.safe_load(path.read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise DatasetError(f"{path.relative_to(ROOT)}: {exc}") from exc


def load_records(path: Path) -> list[WorkRecord]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != RECORD_COLUMNS:
                raise DatasetError(
                    f"{path.relative_to(ROOT)}: header must be {','.join(RECORD_COLUMNS)}"
                )
            rows = list(reader)
    except OSError as exc:
        raise DatasetError(f"{path.relative_to(ROOT)}: {exc}") from exc
    records: list[WorkRecord] = []
    for line, row in enumerate(rows, start=2):
        try:
            records.append(WorkRecord.model_validate(row))
        except ValidationError as exc:
            raise DatasetError(f"{path.relative_to(ROOT)}:{line}: {exc}") from exc
    return records


def load(path: Path) -> Loaded:
    enterprise = load_yaml(path / "enterprise.yaml", Enterprise)
    jira_path = path / "jira/issues.json"
    records_path = path / "work_records.csv"
    template = ROOT / enterprise.capability_template
    return Loaded(
        path=path,
        enterprise=enterprise,
        org=load_yaml(path / "org.yaml", Org),
        roster=load_yaml(path / "roster.yaml", list[RosterEntry]),
        personas=load_yaml(path / "personas.yaml", Personas),
        self_reports=load_yaml(path / "self_reports.yaml", list[SelfReport]),
        manager_reviews=load_yaml(path / "manager_reviews.yaml", list[ManagerReview]),
        jira=load_yaml(jira_path, Jira) if jira_path.exists() else None,
        records=load_records(records_path) if records_path.exists() else [],
        capabilities=load_yaml(template, list[Capability]),
    )


def check(data: Loaded) -> list[str]:
    errors: list[str] = []
    slug = data.path.name
    org = data.org
    ent = data.enterprise

    def require(ok: bool, message: str) -> None:
        if not ok:
            errors.append(f"{slug}: {message}")

    def dupes(values: list[str], label: str) -> set[str]:
        counts = Counter(values)
        repeated = sorted(v for v, n in counts.items() if n > 1)
        require(not repeated, f"duplicate {label}: {repeated}")
        return set(values)

    # Generated upload roster must remain byte-for-byte reproducible.
    roster_path = data.path / "roster.csv"
    require(
        roster_path.exists() and roster_path.read_bytes() == roster_csv(data.path),
        "roster.csv differs from org.yaml/roster.yaml; run export_rosters.py",
    )

    # Files and headers
    for name in [
        "README.md",
        "enterprise.yaml",
        "org.yaml",
        "roster.yaml",
        "personas.yaml",
        "self_reports.yaml",
        "manager_reviews.yaml",
    ]:
        require((data.path / name).exists(), f"missing {name}")
    for name in FORBIDDEN_FILES:
        require(not (data.path / name).exists(), f"{name} must not be generated")
    require(
        (data.path / "personas.yaml").read_text().startswith(PERSONA_HEADER),
        f"personas.yaml must start with '{PERSONA_HEADER}'",
    )
    require(
        ent.uses_jira == (data.jira is not None),
        "uses_jira must match presence of jira/issues.json",
    )
    require(
        ent.uses_jira or (data.path / "work_records.csv").exists(),
        "non-Jira enterprise needs work_records.csv",
    )
    readme = (data.path / "README.md").read_text()
    if data.records:
        require(
            all(column in readme for column in RECORD_COLUMNS),
            "README.md must document every work_records.csv column",
        )

    # Identity of the enterprise
    require(ent.slug == slug, "enterprise.yaml slug must equal directory name")
    require(org.slug == slug, "org.yaml slug must equal directory name")
    require(data.personas.slug == slug, "personas.yaml slug must equal directory name")
    require(ent.name == org.name, "enterprise.yaml and org.yaml names differ")
    require("模拟" in org.name, "organization name must be marked 模拟")
    domain = f"@{slug}.example.com"

    # Departments
    departments = dupes(org.departments, "department")
    require(
        4 <= len(departments) <= 6, f"needs 4-6 departments, has {len(departments)}"
    )
    require(
        {d.name for d in ent.departments} == departments,
        "enterprise.yaml departments must match org.yaml departments",
    )
    require(
        set(ent.hr_it_departments) <= departments,
        "hr_it_departments must be existing departments",
    )

    # Employees, roster, personas
    employees = {e.employee_code: e for e in org.employees}
    dupes([e.employee_code for e in org.employees], "employee_code")
    dupes([e.account_id for e in org.employees], "account_id")
    dupes([e.work_email.strip().lower() for e in org.employees], "work_email")
    roster = {r.employee_code: r for r in data.roster}
    dupes([r.employee_code for r in data.roster], "roster employee_code")
    personas = {p.employee_code: p for p in data.personas.personas}
    dupes([p.employee_code for p in data.personas.personas], "persona employee_code")
    require(set(roster) == set(employees), "roster.yaml must list every employee")
    require(set(personas) == set(employees), "personas.yaml must list every employee")
    for code, employee in employees.items():
        require(employee.department in departments, f"{code} unknown department")
        require(
            employee.work_email == f"{code.lower()}@{ent.slug}.example.com",
            f"{code} email login",
        )
        require(roster[code].work_email == employee.work_email, f"{code} roster email")
        persona = personas.get(code)
        require(
            persona is None or persona.display_name == employee.display_name,
            f"{code} persona display_name differs from org.yaml",
        )
    active = {c for c, r in roster.items() if r.status == "active"}
    require(len(active) >= 20, f"needs >=20 active people, has {len(active)}")
    heads: dict[str, str] = {}
    for code, entry in roster.items():
        row = employees.get(code)
        if row is None:
            continue
        if entry.status == "departed":
            require(entry.left_on is not None, f"{code} departed without left_on")
            require(entry.system_role == "employee", f"{code} departed keeps a role")
        else:
            require(entry.left_on is None, f"{code} active with left_on")
        if entry.left_on:
            require(entry.left_on > entry.hired_on, f"{code} left before hired")
        require(entry.hired_on <= TODAY, f"{code} hired in the future")
        if entry.system_role == "dept_manager" and entry.status == "active":
            require(
                row.department not in heads,
                f"{row.department} has more than one head",
            )
            heads[row.department] = code
        for other in [entry.reports_to, *entry.secondary_reports_to]:
            require(
                other is None or (other in employees and other != code),
                f"{code} reports_to {other} unknown",
            )
        for dept in entry.secondary_departments:
            require(
                dept in departments and dept != row.department,
                f"{code} secondary department {dept}",
            )
        for move in entry.transfers:
            require(
                {move.from_department, move.to_department} <= departments,
                f"{code} transfer departments",
            )
        if entry.transfers:
            last = max(entry.transfers, key=lambda t: t.effective_on)
            require(
                last.to_department == row.department,
                f"{code} last transfer must end in current department",
            )
    for dept in departments:
        head = heads.get(dept)
        require(head is not None, f"{dept} has no active dept_manager")
        members = [
            c
            for c in active
            if employees[c].department == dept
            and roster[c].system_role != "dept_manager"
        ]
        require(len(members) >= 3, f"{dept} needs >=3 active staff besides head")
    for code in active:
        entry = roster[code]
        if entry.system_role != "dept_manager":
            require(entry.reports_to is not None, f"{code} needs reports_to")
        seen = {code}
        cursor = entry.reports_to
        while cursor and cursor in roster:
            require(cursor not in seen, f"{code} reporting cycle")
            if cursor in seen:
                break
            seen.add(cursor)
            cursor = roster[cursor].reports_to

    # Accounts
    dupes([a.email for a in org.accounts], "account email")
    admins = [a for a in org.accounts if a.role and a.role.value == "org_admin"]
    require(len(admins) == 1, f"needs exactly one org_admin account, has {len(admins)}")
    for admin in admins:
        admin_code = admin.employee_code
        require(admin_code in employees, "org_admin must be linked to an employee")
        if admin_code in employees:
            require(
                employees[admin_code].department in ent.hr_it_departments,
                "org_admin must work in an HR or IT department",
            )
    by_employee: dict[str, list[str]] = {}
    for account in org.accounts:
        require(account.email.endswith(domain), f"{account.email} domain")
        require(account.employee_code is not None, f"{account.email} has no employee")
        if account.employee_code is None:
            continue
        by_employee.setdefault(account.employee_code, []).append(account.email)
        holder = roster.get(account.employee_code)
        holder_row = employees.get(account.employee_code)
        if holder is None or holder_row is None:
            continue
        expected = None if holder.system_role == "employee" else holder.system_role
        actual = account.role.value if account.role else None
        require(actual == expected, f"{account.email} role {actual} != {expected}")
        require(
            account.email == holder_row.work_email, f"{account.email} != work_email"
        )
    for code in employees:
        count = len(by_employee.get(code, []))
        require(
            count == (1 if code in active else 0),
            f"{code} needs {'one' if code in active else 'no'} login account",
        )

    # Capability template
    capabilities = {c.key for c in data.capabilities}
    dupes([c.key for c in data.capabilities], "capability key")
    require(len(capabilities) >= 12, "capability template needs >=12 entries")
    require(
        ent.capability_template.startswith("templates/capabilities/")
        and ent.capability_template.endswith(".zh-CN.yaml"),
        "capability_template path",
    )
    for capability in data.capabilities:
        require(
            [lvl.key for lvl in capability.level_rubric.levels] == LEVELS,
            f"{capability.key} levels must be {LEVELS}",
        )

    # Accounts that appear in source data
    sources = {s.name: s.kind for s in ent.sources}
    dupes([s.name for s in ent.sources], "source name")
    require(
        ("jira" in sources.values()) == ent.uses_jira,
        "sources must list Jira iff uses_jira",
    )
    declared: dict[str, str] = {e.account_id: c for c, e in employees.items()}
    for code, entry in roster.items():
        for ext in entry.external_accounts:
            require(ext.source in sources, f"{code} account source {ext.source}")
            require(ext.account_id not in declared, f"{ext.account_id} declared twice")
            declared[ext.account_id] = code
    truth = {t.account_id: t for t in data.personas.account_truth}
    dupes([t.account_id for t in data.personas.account_truth], "account_truth id")
    for item in data.personas.account_truth:
        require(item.source in sources, f"account_truth source {item.source}")
        require(item.account_id not in declared, f"{item.account_id} is declared")
        require(
            item.owner is None or item.owner in employees,
            f"account_truth owner {item.owner}",
        )

    def owner(account: str) -> str | None:
        if account in declared:
            return declared[account]
        return truth[account].owner if account in truth else None

    used: dict[str, set[str]] = {}

    def use(account: str, where: str) -> None:
        require(
            account in declared or account in truth,
            f"{where}: account {account} neither declared nor in account_truth",
        )
        used.setdefault(account, set()).add(where)

    refs: dict[str, tuple[str, str | None]] = {}
    issues = data.jira.issues if data.jira else []
    jira_keys = dupes([i.key for i in issues], "jira key")
    for issue in issues:
        use(issue.fields.assignee.accountId, f"jira:{issue.key}")
        use(issue.fields.reporter.accountId, f"jira:{issue.key}")
        refs[f"jira:{issue.key}"] = ("Jira", owner(issue.fields.assignee.accountId))
    record_ids = dupes([r.record_id for r in data.records], "record_id")
    require(not (record_ids & jira_keys), "record_id collides with a Jira key")
    for record in data.records:
        where = f"record:{record.record_id}"
        require(sources.get(record.source) == "work_records", f"{where} source")
        use(record.actor_account, where)
        for part in filter(None, record.participants.split(";")):
            part_account, sep, role = part.partition(":")
            require(
                bool(sep and role), f"{where} participant '{part}' needs account:role"
            )
            use(part_account, where)
        refs[where] = (record.source, owner(record.actor_account))
    for item in data.personas.account_truth:
        require(item.account_id in used, f"account_truth {item.account_id} unused")
    actors: dict[str, set[str]] = {}
    for acct_id, places in used.items():
        who = owner(acct_id)
        if who:
            actors.setdefault(who, set()).update(places)
    if ent.uses_jira:
        require(len(issues) >= 30, f"needs >=30 Jira issues, has {len(issues)}")
    else:
        require(
            len(data.records) >= 40, f"needs >=40 work records, has {len(data.records)}"
        )

    # Self reports and manager reviews
    dupes([r.id for r in data.self_reports], "self_report id")
    dupes([r.id for r in data.manager_reviews], "manager_review id")
    require(len(data.self_reports) >= 10, "needs >=10 self reports")
    require(len(data.manager_reviews) >= 10, "needs >=10 manager reviews")
    tasks = jira_keys | record_ids
    for report in data.self_reports:
        require(report.employee_code in employees, f"{report.id} employee")
        missing = set(report.related_task_keys) - tasks
        require(not missing, f"{report.id} unknown related_task_keys {sorted(missing)}")
        refs[f"self_report:{report.id}"] = ("self_report", report.employee_code)
    project_managers: dict[str, set[str]] = {}
    for project in ent.projects:
        for member in project.members:
            project_managers.setdefault(member, set()).add(project.manager)
    for review in data.manager_reviews:
        require(review.employee_code in employees, f"{review.id} employee")
        require(review.capability_key in capabilities, f"{review.id} capability_key")
        reviewee = roster.get(review.employee_code)
        allowed = set(project_managers.get(review.employee_code, set()))
        if reviewee:
            allowed |= {
                m for m in [reviewee.reports_to, *reviewee.secondary_reports_to] if m
            }
        require(
            review.reviewer_employee_code in allowed,
            f"{review.id} reviewer must be a manager or project manager of the employee",
        )
        refs[f"manager_review:{review.id}"] = ("manager_review", review.employee_code)

    # Projects
    dupes([p.code for p in ent.projects], "project code")
    for project in ent.projects:
        crew = {project.manager, *project.members}
        require(crew <= set(employees), f"project {project.code} unknown people")

    # Ground truth
    gaps: Counter[str] = Counter()
    for code, persona in personas.items():
        keys = [c.key for c in persona.ground_truth.capabilities]
        require(len(keys) == len(set(keys)), f"{code} duplicate ground-truth keys")
        for fact in persona.ground_truth.capabilities:
            gaps[fact.evidence] += 1
            require(fact.key in capabilities, f"{code} ground truth key {fact.key}")
            for ref in fact.evidence_refs:
                require(ref in refs, f"{code} ground truth ref {ref} unknown")
            require(
                fact.evidence != "undocumented" or not fact.evidence_refs,
                f"{code} {fact.key} undocumented but has refs",
            )
    require(gaps["documented"] >= 5, "needs >=5 documented ground-truth entries")
    require(gaps["undocumented"] >= 5, "needs >=5 undocumented ground-truth entries")
    require(gaps["misleading"] >= 3, "needs >=3 misleading ground-truth entries")

    # Edge cases
    reviewed = Counter(r.employee_code for r in data.manager_reviews)
    reported = Counter(r.employee_code for r in data.self_reports)
    unsure = {r.employee_code for r in data.manager_reviews if r.answer == "not_sure"}
    managed = {p.manager for p in ent.projects}
    head_codes = set(heads.values())
    for case in data.personas.edge_cases:
        label = f"edge {case.tag}"
        for code in case.employees:
            require(code in employees, f"{label} unknown employee {code}")
        for ref in case.refs:
            require(ref in refs, f"{label} unknown ref {ref}")
        people = [c for c in case.employees if c in employees]
        tag = case.tag
        if tag == "same_name":
            names = {employees[c].display_name for c in people}
            require(len(people) >= 2 and len(names) == 1, f"{label} needs same names")
        elif tag == "multi_account":
            for code in people:
                accounts = {a for a in used if owner(a) == code}
                require(len(accounts) >= 2, f"{label} {code} needs >=2 used accounts")
        elif tag == "unlinked_account":
            require(
                any(t.owner is None for t in data.personas.account_truth),
                f"{label} needs an account_truth entry with owner null",
            )
        elif tag == "transfer":
            require(all(roster[c].transfers for c in people), f"{label} no transfers")
        elif tag == "concurrent_post":
            require(
                all(roster[c].secondary_departments for c in people),
                f"{label} no secondary_departments",
            )
        elif tag == "departed":
            require(all(roster[c].status == "departed" for c in people), label)
        elif tag == "new_hire_no_history":
            for code in people:
                require(
                    roster[code].hired_on >= NEW_HIRE_SINCE, f"{label} {code} hired"
                )
                require(code not in actors, f"{label} {code} has work records")
                require(not reported[code], f"{label} {code} has self reports")
        elif tag in {"contractor", "intern"}:
            require(all(roster[c].employment_type == tag for c in people), label)
        elif tag == "assignee_mismatch":
            require(
                any(r.startswith(("jira:", "record:")) for r in case.refs),
                f"{label} needs a jira/record ref",
            )
        elif tag == "cross_source_duplicate":
            kinds = {refs[r][0] for r in case.refs if r in refs}
            require(len(kinds) >= 2, f"{label} needs refs from >=2 sources")
        elif tag == "review_conflict":
            require(all(reviewed[c] and reported[c] for c in people), label)
        elif tag == "appeal":
            require(all(reviewed[c] for c in people), f"{label} needs a review")
        elif tag == "manager_unsure":
            require(all(c in unsure for c in people), f"{label} needs not_sure")
        elif tag == "no_forms":
            require(
                all(
                    personas[c].usage_profile.digital_literacy == "low" for c in people
                ),
                f"{label} needs low digital literacy",
            )
        elif tag == "mobile_only":
            require(
                all(personas[c].usage_profile.devices == ["mobile"] for c in people),
                f"{label} devices must be [mobile]",
            )
        elif tag == "dept_and_project_lead":
            require(all(c in head_codes and c in managed for c in people), label)
        elif tag == "pm_not_dept_manager":
            require(all(c in managed and c not in head_codes for c in people), label)
        if tag not in {
            "unlinked_account",
            "assignee_mismatch",
            "cross_source_duplicate",
        }:
            require(bool(people), f"{label} needs employees")
    return errors


def summarize(items: list[Loaded]) -> str:
    lines = [
        "| slug | 名称 | 门类 | 组织形态 | 部门 | 人数(在职/合计) | Jira | 工作记录 | 能力项 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for data in items:
        ent = data.enterprise
        active = sum(r.status == "active" for r in data.roster)
        issues = len(data.jira.issues) if data.jira else 0
        lines.append(
            f"| {ent.slug} | {ent.name} | {ent.gbt4754.section} {ent.gbt4754.section_name}"
            f" | {ent.org_structure} | {len(data.org.departments)}"
            f" | {active}/{len(data.roster)} | {issues or '不用'} | {len(data.records)}"
            f" | {len(data.capabilities)} |"
        )
    tags = {
        t: sorted(
            d.path.name for d in items if t in {e.tag for e in d.personas.edge_cases}
        )
        for t in EDGE_TAGS
    }
    lines += ["", "| 边界情况 | 企业数 | 企业 |", "| --- | --- | --- |"]
    lines += [f"| {t} | {len(v)} | {', '.join(v)} |" for t, v in tags.items()]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    summary = "--summary" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    dirs = sorted(p for p in DATA.iterdir() if p.is_dir() and (p / "org.yaml").exists())
    if wanted:
        dirs = [d for d in dirs if d.name in wanted]
    errors: list[str] = []
    loaded: list[Loaded] = []
    for path in dirs:
        try:
            data = load(path)
        except DatasetError as exc:
            errors.append(str(exc))
            continue
        loaded.append(data)
        errors += check(data)
    if not wanted:
        sections = [d.enterprise.gbt4754.section for d in loaded]
        if len(sections) != len(set(sections)):
            errors.append(f"dataset: GB/T 4754 sections repeat: {sorted(sections)}")
        if len(dirs) != 10:
            errors.append(f"dataset: expected 10 enterprises, found {len(dirs)}")
        if sum(not d.enterprise.uses_jira for d in loaded) < 3:
            errors.append("dataset: at least 3 enterprises must not use Jira")
        structures = {d.enterprise.org_structure for d in loaded}
        if not {"divisional", "matrix", "projectized"} <= structures or not (
            structures & {"functional", "line_functional"}
        ):
            errors.append(f"dataset: org structures not covered: {sorted(structures)}")
        active = sum(r.status == "active" for d in loaded for r in d.roster)
        if active < 200:
            errors.append(f"dataset: needs >=200 active people, has {active}")
        for tag in EDGE_TAGS:
            count = sum(tag in {e.tag for e in d.personas.edge_cases} for d in loaded)
            if count < 2:
                errors.append(f"dataset: edge case {tag} in {count} enterprises (<2)")
        templates = [d.enterprise.capability_template for d in loaded]
        if len(templates) != len(set(templates)):
            errors.append("dataset: each enterprise needs its own capability template")
    for error in errors:
        sys.stderr.write(f"ERROR {error}\n")
    if summary and loaded:
        sys.stdout.write(summarize(loaded) + "\n")
    people = sum(len(d.roster) for d in loaded)
    sys.stderr.write(
        f"{len(loaded)} enterprise(s), {people} people, {len(errors)} error(s)\n"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
