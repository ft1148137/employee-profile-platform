from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from app.domains.capability.search import PeopleSearchRow
from app.domains.capacity.models import (
    DemandEventKind,
    DemandOutcome,
    DemandStatus,
    ProposalStatus,
)
from app.domains.capacity.schemas import CapacityInput, Note, ReasonCode, WorkloadPublic


class ReferenceInput(CapacityInput):
    target_department_id: UUID
    capability_key: str = Field(min_length=1, max_length=200)
    minimum_level: str | None = Field(default=None, max_length=200)


class DetailedCapacityReference(BaseModel):
    department_name: str
    capability_name: str
    count: int
    available: int = 0
    full: int = 0
    overloaded: int = 0
    unconfirmed: int = 0


class CountCapacityReference(BaseModel):
    department_name: str
    capability_name: str
    count: int


LIMITED_REFERENCE_TEXT = (
    "该部门具备这项能力的人较少，能否承接请提交需求，由对方部门经理答复"
)


class LimitedCapacityReference(BaseModel):
    department_name: str
    capability_name: str
    limited: Literal[True]


type CapacityReference = (
    CountCapacityReference | DetailedCapacityReference | LimitedCapacityReference
)


class DemandFields(ReferenceInput):
    headcount: int = Field(strict=True, ge=1, le=50)
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    window_start: date
    window_end: date
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


class DemandInput(DemandFields):
    requesting_department_id: UUID

    @model_validator(mode="after")
    def valid_window(self) -> DemandInput:
        if self.window_end < self.window_start:
            raise ValueError("结束日期不能早于开始日期")
        if self.requesting_department_id == self.target_department_id:
            raise ValueError("目标部门不能是需求方部门本身")
        return self


class ExtraCapability(CapacityInput):
    key: str = Field(min_length=1, max_length=200)
    minimum_level: str | None = Field(default=None, max_length=200)
    required: StrictBool


class ProjectRoleInput(DemandFields):
    role_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
    ]
    extra_capabilities: list[ExtraCapability] = Field(
        default_factory=list, max_length=5
    )
    depends_on: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list, max_length=11
    )

    @model_validator(mode="after")
    def distinct_capabilities(self) -> ProjectRoleInput:
        keys = {item.key for item in self.extra_capabilities}
        if self.capability_key in keys or len(keys) != len(self.extra_capabilities):
            raise ValueError("主能力和额外能力不能重复")
        return self

    def as_demand(self, requesting_department_id: UUID) -> DemandInput:
        return DemandInput(
            requesting_department_id=requesting_department_id,
            **self.model_dump(
                exclude={"role_name", "extra_capabilities", "depends_on"}
            ),
        )


class ProjectInput(CapacityInput):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    description: str = Field(default="", max_length=2000)
    requesting_department_id: UUID
    roles: list[ProjectRoleInput] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def previous_roles_only(self) -> ProjectInput:
        for index, role in enumerate(self.roles):
            role.as_demand(self.requesting_department_id)
            if any(dep >= index for dep in role.depends_on):
                raise ValueError("前置角色必须是同一项目中前面已填写的角色")
            if len(set(role.depends_on)) != len(role.depends_on):
                raise ValueError("同一前置角色不能重复选择")
        return self


class DemandListInput(CapacityInput):
    side: Literal["outgoing", "incoming"] = "outgoing"
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    status: DemandStatus | None = None
    actionable_only: bool = False


class DemandIdInput(CapacityInput):
    demand_id: UUID


class ReplyInput(CapacityInput):
    option_id: UUID | None = None
    outside_basis: StrictBool = False
    outcome: DemandOutcome
    reason_code: ReasonCode | None = None
    start_date: date | None = None
    end_date: date | None = None
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    revision: int = Field(strict=True, ge=1)

    @model_validator(mode="before")
    @classmethod
    def discard_client_basis(cls, value: object) -> object:
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k != "basis"}
        return value

    @model_validator(mode="after")
    def valid_dates(self) -> ReplyInput:
        if (
            self.option_id is None
            and self.outcome != DemandOutcome.NOT_NOW
            and (self.start_date is None or self.end_date is None)
        ):
            raise ValueError("能做或能做一部分时必须填写承诺起止日期")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("请同时填写开始和结束日期")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class RespondInput(CapacityInput):
    action: Literal["accept", "follow_up", "withdraw"]
    reason_code: ReasonCode | None = None
    text: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
        ]
        | None
    ) = None
    revision: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def follow_up_text(self) -> RespondInput:
        if self.action == "follow_up" and not self.text:
            raise ValueError("追问时必须填写说明")
        return self


class ReplyDemandInput(ReplyInput, DemandIdInput):
    pass


class PlanReplyInput(ReplyInput):
    lender_department_id: UUID | None = None
    borrow_headcount: int | None = Field(default=None, strict=True, ge=1)


class ChangeProposalInput(CapacityInput):
    option_id: UUID
    revision: int = Field(strict=True, ge=1)
    reason: Note
    outside_basis: StrictBool = False
    lender_department_id: UUID | None = None
    borrow_headcount: int | None = Field(default=None, strict=True, ge=1)


class PlanReplyDemandInput(PlanReplyInput, ReplyDemandInput):
    pass


class RespondDemandInput(RespondInput, DemandIdInput):
    pass


class DemandBasisPerson(PeopleSearchRow):
    workload: WorkloadPublic


class DemandOtherPerson(CapacityInput):
    employee_id: UUID
    display_name: str


class DemandBasis(CapacityInput):
    data: list[DemandBasisPerson]
    others: list[DemandOtherPerson]
    count: int
    reference: DetailedCapacityReference


class CommitmentChange(CapacityInput):
    previous_end_date: date
    revision: int


class DemandEventPublic(CapacityInput):
    kind: DemandEventKind
    text: str
    created_at: datetime
    via: str
    outcome: DemandOutcome | None = None
    start_date: date | None = None
    end_date: date | None = None
    basis: CapacityReference | None = None
    responder_name: str | None = None
    previous_end_date: date | None = None
    revision: int | None = None


class ProposalApprovalPublic(CapacityInput):
    party: str
    decision: str
    reason: str | None
    reason_code: str | None = None
    created_at: datetime


class ProposalSegment(CapacityInput):
    start_date: date
    end_date: date
    allocation_percent: int


class BorrowLineInput(ProposalSegment):
    lender_department_id: UUID


class BorrowCandidate(CapacityInput):
    employee_id: UUID
    display_name: str


class BorrowRequest(ProposalSegment):
    id: UUID
    description: str
    capability_name: str
    borrower_department: str
    status: str
    reason: str | None = None
    reason_code: str | None = None
    candidates: list[BorrowCandidate] = Field(default_factory=list)


class BorrowDecisionInput(CapacityInput):
    action: Literal["select", "reject", "accept", "decline"]
    employee_id: UUID | None = None
    reason: Note | None = None
    reason_code: ReasonCode | None = None


ParticipantChangeStatus = Literal["unchanged", "replaced", "added", "removed"]


class ProposalParticipant(CapacityInput):
    number: int
    segments: list[ProposalSegment]


class ChangeProposalParticipant(ProposalParticipant):
    status: ParticipantChangeStatus


ParticipantPublic = ChangeProposalParticipant | ProposalParticipant


class CommitmentArrangement(CapacityInput):
    participants: list[ParticipantPublic]
    lines: list[ProposalLinePublic] | None = None


class ProposalSummary(CapacityInput):
    id: UUID
    status: ProposalStatus
    fail_reason: str | None
    kind: Literal["initial", "change"] = "initial"
    previous: CommitmentArrangement | None = None
    revision: int
    hold_expires_at: datetime
    committed_at: datetime | None
    headcount: int
    participants: list[ParticipantPublic]
    allocation_percents: list[int]
    start_date: date
    end_date: date
    conditions: str
    approvals: list[ProposalApprovalPublic]
    borrow_progress: str | None = None


class ProposalLineInput(CapacityInput):
    employee_id: UUID
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    start_date: date
    end_date: date
    outside_basis: StrictBool = False

    @model_validator(mode="after")
    def valid_dates(self) -> ProposalLineInput:
        if self.start_date > self.end_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class ProposalLinePublic(ProposalLineInput):
    id: UUID
    employee_name: str


class ProposalDetail(ProposalSummary):
    lines: list[ProposalLinePublic]


class ProposalWithdrawInput(CapacityInput):
    revision: int = Field(strict=True, ge=1)


class ProposalDecision(BaseModel):
    id: UUID
    kind: str
    status: ProposalStatus
    fail_reason: str | None
    created_at: datetime
    approvals: list[ProposalApprovalPublic]


class DemandHistoryEvent(CapacityInput):
    kind: DemandEventKind
    text: str | None
    created_at: datetime
    outcome: DemandOutcome | None
    reason_code: str | None


class SnapshotOptionDecision(BaseModel):
    kind_label: str
    verdict_label: str


class SnapshotDecision(BaseModel):
    id: UUID
    created_at: datetime
    options: list[SnapshotOptionDecision] = Field(default_factory=list)


class DemandHistory(BaseModel):
    proposals: list[ProposalDecision]
    events: list[DemandHistoryEvent]
    snapshots: list[SnapshotDecision] = Field(default_factory=list)


class ProjectRoleSummary(BaseModel):
    id: UUID
    role_name: str
    target_department_name: str
    status: DemandStatus
    window_start: date
    window_end: date
    committed_start_date: date | None
    committed_end_date: date | None


class DemandProject(BaseModel):
    id: UUID
    title: str
    role_name: str
    prerequisites: list[ProjectRoleSummary] = Field(default_factory=list)


class DemandExtraCapability(ExtraCapability):
    capability_name: str
    minimum_level_label: str | None


class DemandPublic(DemandInput):
    id: UUID
    capability_name: str
    status: DemandStatus
    can_reply: bool = False
    revision: int
    created_at: datetime
    extra_capabilities: list[DemandExtraCapability] = Field(default_factory=list)
    events: list[DemandEventPublic] = Field(default_factory=list)
    committed_start_date: date | None = None
    committed_end_date: date | None = None
    basis_url: str | None = None
    proposal: ProposalDetail | ProposalSummary | None = None
    project: DemandProject | None = None


class DemandList(BaseModel):
    data: list[DemandPublic]
    count: int


class ProjectSummary(BaseModel):
    id: UUID
    title: str
    requesting_department_name: str
    created_at: datetime


class ProjectIdInput(CapacityInput):
    project_id: UUID


class ProjectList(BaseModel):
    data: list[ProjectSummary]
    count: int


class ProjectRole(ProjectRoleSummary):
    outcome: DemandOutcome | None = None
    demand: DemandPublic | None = None


class ScheduledProjectRole(BaseModel):
    role: ProjectRoleSummary
    planned_start: date
    planned_end: date
    earliest_start: date | None
    forecast_start: date | None
    forecast_end: date | None
    forecast_unconfirmed: bool = False


type ProjectImpactReason = Literal[
    "partial", "not_now", "late_commitment", "dependency"
]


class ProjectImpact(BaseModel):
    role: ProjectRoleSummary
    reasons: list[ProjectImpactReason]
    downstream: list[ProjectRoleSummary]


class ProjectSchedule(BaseModel):
    stages: list[list[ScheduledProjectRole]]
    impacts: list[ProjectImpact]
    window_end: date
    forecast_end: date | None
    delay_days: int | None
    forecast_unconfirmed: bool = False


class ProjectDetail(ProjectSummary):
    description: str | None = None
    roles: list[ProjectRole]
    schedule: ProjectSchedule
