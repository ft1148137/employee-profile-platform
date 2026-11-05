from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.change_request.models import ProfileChangeRequest, RequestStatus
from app.domains.review.routing import can_review


def review_access(
    session: Session, principal: Principal, request: ProfileChangeRequest
) -> bool:
    return (
        principal.org_id == request.org_id
        and principal.user_id is not None
        and principal.employee_id != request.subject_employee_id
        and request.status == RequestStatus.PENDING_REVIEW
        and can_review(session, principal, request.subject_employee_id) is not None
    )
