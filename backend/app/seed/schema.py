from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from app.domains.org.models import Role
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimReviewStatus,
    Confidence,
    EvidenceQuality,
    EvidenceRelation,
    FillStatus,
    InferenceStatus,
    ProgressReviewStatus,
)


class SeedError(ValueError):
    """Invalid seed input or unsafe environment."""


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmployeeRow(Row):
    employee_code: str
    department: str
    display_name: str
    title: str
    work_email: str
    account_id: str


class Account(Row):
    email: str
    employee_code: str | None = None
    role: Role | None = None


class Org(Row):
    slug: str
    name: str
    departments: list[str]
    employees: list[EmployeeRow]
    accounts: list[Account]


Level = Literal["needs_support", "routine", "independent_complex"]


class RubricLevel(Row):
    key: Level
    label: str
    description: str


class Rubric(Row):
    levels: list[RubricLevel]


class Capability(Row):
    key: str
    name: str
    category: str
    description: str
    level_rubric: Rubric


class Named(Row):
    name: str


class Identity(Row):
    accountId: str
    displayName: str


class JiraFields(Row):
    summary: str
    issuetype: Named
    status: Named
    assignee: Identity
    reporter: Identity
    created: AwareDatetime
    resolutiondate: AwareDatetime | None
    labels: list[str]
    components: list[Named]
    description: str


class JiraIssue(Row):
    key: str
    fields: JiraFields


class Jira(Row):
    issues: list[JiraIssue]


class SelfReport(Row):
    id: str
    employee_code: str
    submitted_at: AwareDatetime
    text: str
    related_task_keys: list[str]


class ManagerReview(Row):
    id: str
    employee_code: str
    reviewer_employee_code: str
    capability_key: str
    question: str
    answer: Level | Literal["not_sure"]
    comment: str
    reviewed_at: AwareDatetime


class EvidenceRef(Row):
    ref: str
    relation: EvidenceRelation


class Claim(Row):
    id: str
    employee_code: str
    capability_key: str
    statement: str
    level: Level | None = None
    assessment_status: AssessmentStatus
    review_status: ClaimReviewStatus
    confidence: Confidence
    explanation: str
    supersedes: str | None = None
    evidence: list[EvidenceRef]


class Version(Row):
    employee_code: str
    revision: int = Field(ge=1)
    published_at: AwareDatetime
    published_by: str
    claims: list[str]


class Progress(Row):
    employee_code: str
    fill_status: FillStatus
    inference_status: InferenceStatus
    review_status: ProgressReviewStatus
    evidence_quality: EvidenceQuality


class Bundle(Row):
    org: Org
    capabilities: list[Capability]
    jira: Jira
    self_reports: list[SelfReport]
    manager_reviews: list[ManagerReview]
    claims: list[Claim]
    profile_versions: list[Version]
    progress: list[Progress]


def read_file[T](path: Path, kind: type[T]) -> T:
    try:
        return TypeAdapter(kind).validate_python(yaml.safe_load(path.read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise SeedError(f"{path}: {exc}") from exc


def read_bundle(path: Path) -> Bundle:
    data = Bundle(
        org=read_file(path / "org.yaml", Org),
        capabilities=read_file(
            path.parent.parent / "templates/capabilities/tech-rnd.zh-CN.yaml",
            list[Capability],
        ),
        jira=read_file(path / "jira/issues.json", Jira),
        self_reports=read_file(path / "self_reports.yaml", list[SelfReport]),
        manager_reviews=read_file(path / "manager_reviews.yaml", list[ManagerReview]),
        claims=read_file(path / "claims.yaml", list[Claim]),
        profile_versions=read_file(path / "profile_versions.yaml", list[Version]),
        progress=read_file(path / "progress.yaml", list[Progress]),
    )

    def require(ok: bool, location: str) -> None:
        if not ok:
            raise SeedError(
                f"{location}: duplicate key or unresolved/invalid reference"
            )

    def unique(keys: list[str], location: str) -> set[str]:
        require(len(keys) == len(set(keys)), location)
        return set(keys)

    employees = unique(
        [e.employee_code for e in data.org.employees],
        "org.yaml.employees.employee_code",
    )
    unique([e.account_id for e in data.org.employees], "org.yaml.employees.account_id")
    unique([a.email for a in data.org.accounts], "org.yaml.accounts.email")
    departments = unique(data.org.departments, "org.yaml.departments")
    capabilities = {c.key: c for c in data.capabilities}
    unique([c.key for c in data.capabilities], "tech-rnd.zh-CN.yaml.key")
    for capability in data.capabilities:
        require(
            [v.key for v in capability.level_rubric.levels]
            == ["needs_support", "routine", "independent_complex"],
            f"tech-rnd.zh-CN.yaml.{capability.key}.level_rubric.levels",
        )
    for employee in data.org.employees:
        require(
            employee.department in departments,
            f"org.yaml.{employee.employee_code}.department",
        )
    for account in data.org.accounts:
        require(
            account.employee_code is None or account.employee_code in employees,
            f"org.yaml.{account.email}.employee_code",
        )
        require(
            account.role != Role.DEPT_MANAGER or account.employee_code is not None,
            f"org.yaml.{account.email}.role",
        )
    jira_keys = unique([j.key for j in data.jira.issues], "jira/issues.json.issues.key")
    known_accounts = {e.account_id for e in data.org.employees}
    refs = {
        f"jira:{j.key}": next(
            e.employee_code
            for e in data.org.employees
            if e.account_id == j.fields.assignee.accountId
        )
        for j in data.jira.issues
        if j.fields.assignee.accountId in known_accounts
    }
    for filename, rows in (
        ("self_reports", data.self_reports),
        ("manager_reviews", data.manager_reviews),
    ):
        unique([r.id for r in rows], f"{filename}.yaml.id")
        for row in rows:
            require(
                row.employee_code in employees,
                f"{filename}.yaml.{row.id}.employee_code",
            )
            refs[f"{filename.removesuffix('s')}:{row.id}"] = row.employee_code
    for report in data.self_reports:
        require(
            set(report.related_task_keys) <= jira_keys,
            f"self_reports.yaml.{report.id}.related_task_keys",
        )
    for review in data.manager_reviews:
        require(
            review.reviewer_employee_code in employees,
            f"manager_reviews.yaml.{review.id}.reviewer_employee_code",
        )
        require(
            review.capability_key in capabilities,
            f"manager_reviews.yaml.{review.id}.capability_key",
        )
    claims: dict[str, Claim] = {}
    unique([c.id for c in data.claims], "claims.yaml.id")
    for claim in data.claims:
        require(
            claim.employee_code in employees and claim.capability_key in capabilities,
            f"claims.yaml.{claim.id}",
        )
        require(
            claim.level is None
            or claim.level
            in {v.key for v in capabilities[claim.capability_key].level_rubric.levels},
            f"claims.yaml.{claim.id}.level",
        )
        require(
            all(refs.get(e.ref) == claim.employee_code for e in claim.evidence),
            f"claims.yaml.{claim.id}.evidence",
        )
        require(
            claim.supersedes is None
            or (
                claim.supersedes in claims
                and claims[claim.supersedes].employee_code == claim.employee_code
                and claims[claim.supersedes].capability_key == claim.capability_key
            ),
            f"claims.yaml.{claim.id}.supersedes",
        )
        claims[claim.id] = claim
    versions: set[tuple[str, int]] = set()
    for version in data.profile_versions:
        key = (version.employee_code, version.revision)
        require(
            key not in versions
            and (version.revision == 1 or (key[0], key[1] - 1) in versions),
            f"profile_versions.yaml.{key}.revision",
        )
        require(
            version.employee_code in employees
            and version.published_by in {a.email for a in data.org.accounts},
            f"profile_versions.yaml.{key}.published_by",
        )
        require(
            all(
                c in claims and claims[c].employee_code == key[0]
                for c in version.claims
            ),
            f"profile_versions.yaml.{key}.claims",
        )
        versions.add(key)
    require(
        unique([p.employee_code for p in data.progress], "progress.yaml.employee_code")
        == employees,
        "progress.yaml.employee_code",
    )
    return data
