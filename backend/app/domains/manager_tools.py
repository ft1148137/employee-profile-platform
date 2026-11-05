from uuid import UUID

from sqlmodel import Session, SQLModel, select

from app.authz.principal import Principal
from app.domains.change_request.employee import EmployeeRecordNotFound
from app.domains.change_request.models import ProfileChangeRequest, ReviewVia
from app.domains.change_request.schemas import ToolInput
from app.domains.employee_tools import EmployeeTool, RequestInput
from app.domains.org.service import list_employees
from app.domains.profile.models import ProgressReviewStatus
from app.domains.review import service
from app.domains.review.routing import can_review
from app.domains.review.schemas import (
    DecideInput,
    DecisionInput,
    DecisionResult,
    PendingReviews,
    ReviewDetail,
)


class DecideReviewInput(DecideInput):
    request_id: UUID


class TeamOverview(SQLModel):
    employee_count: int
    profile_status_counts: dict[ProgressReviewStatus, int]
    pending_review_count: int


def list_pending_reviews(principal: Principal, session: Session) -> PendingReviews:
    reviews = service.list_pending_reviews(principal, session)
    data = [review for review in reviews.data if review.can_decide]
    return PendingReviews(data=data, count=len(data))


def _require_reviewer(principal: Principal, session: Session, request_id: UUID) -> None:
    # MCP hides other people's requests even when the caller knows their IDs.
    request = session.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == principal.org_id,
            ProfileChangeRequest.id == request_id,
        )
    ).one_or_none()
    if request is None:
        raise EmployeeRecordNotFound()
    if principal.employee_id == request.subject_employee_id:
        raise service.ReviewForbidden()
    if can_review(session, principal, request.subject_employee_id) is None:
        raise EmployeeRecordNotFound()


def get_review(
    principal: Principal, session: Session, request_id: UUID
) -> ReviewDetail:
    _require_reviewer(principal, session, request_id)
    return service.get_review(principal, session, request_id)


def decide_review_items(
    principal: Principal,
    session: Session,
    request_id: UUID,
    revision: int,
    decisions: list[DecisionInput],
    comment: str | None = None,
    via: ReviewVia = ReviewVia.MCP,
    client_name: str | None = None,
) -> DecisionResult:
    _require_reviewer(principal, session, request_id)
    return service.decide(
        principal, session, request_id, revision, decisions, comment, via, client_name
    )


def get_team_overview(principal: Principal, session: Session) -> TeamOverview:
    counts = dict.fromkeys(ProgressReviewStatus, 0)
    skip = 0
    while True:
        page = list_employees(
            session, principal, department_id=None, q=None, skip=skip, limit=100
        )
        for employee in page.data:
            counts[employee.progress.review_status] += 1
        skip += len(page.data)
        if not page.data or skip >= page.count:
            break
    return TeamOverview(
        employee_count=skip,
        profile_status_counts=counts,
        pending_review_count=list_pending_reviews(principal, session).count,
    )


MANAGER_TOOLS = (
    EmployeeTool(
        "list_pending_reviews",
        "查看当前用户待审批的画像请求；MCP 需要员工身份，无员工身份的管理员请在网页审批",
        ToolInput,
        list_pending_reviews,
        bot_allowed=False,
    ),
    EmployeeTool(
        "get_review",
        "查看审批改动前后、依据与不确定之处，取得决定时必填的 revision；不能审批本人的请求",
        RequestInput,
        get_review,
        bot_allowed=False,
    ),
    EmployeeTool(
        "decide_review_items",
        "先向经理逐项复述决定和原因，经理明确同意后才调用。"
        "必须先用 get_review 取得 revision；过期时重新读取并再次确认。"
        "逐项批准、退回或拒绝，退回和拒绝必须写原因；不能审批本人的请求",
        DecideReviewInput,
        decide_review_items,
        writes=True,
        bot_allowed=False,
    ),
    EmployeeTool(
        "get_team_overview",
        "只读查看员工列表可见范围（本人、获授权部门及下级部门）的画像状态计数和当前用户待审批数",
        ToolInput,
        get_team_overview,
        bot_allowed=False,
    ),
)
