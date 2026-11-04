from uuid import UUID

from sqlmodel import Session, col, func, select, update

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.inbox.models import (
    InboxItem,
    InboxKind,
    PendingQuestion,
    QuestionStatus,
)
from app.domains.inbox.schemas import (
    AnswerInput,
    InboxList,
    InboxPublic,
    QuestionPublic,
    QuestionsList,
)


def notify(
    session: Session, request: ProfileChangeRequest, kind: InboxKind, message: str
) -> None:
    if kind in (InboxKind.SUBMITTED, InboxKind.STATUS_CHANGED):
        now = utc_now()
        session.exec(
            update(InboxItem)
            .where(
                col(InboxItem.org_id) == request.org_id,
                col(InboxItem.recipient_employee_id) == request.subject_employee_id,
                col(InboxItem.request_id) == request.id,
                col(InboxItem.kind) == InboxKind.INITIAL_REVIEW,
                col(InboxItem.done_at).is_(None),
            )
            .values(done_at=now, read_at=func.coalesce(InboxItem.read_at, now))
        )
    session.add(
        InboxItem(
            org_id=request.org_id,
            recipient_employee_id=request.subject_employee_id,
            request_id=request.id,
            kind=kind,
            message=message,
        )
    )


def my_inbox(
    principal: Principal, session: Session, skip: int = 0, limit: int = 50
) -> InboxList:
    from app.domains.change_request.employee import employee_id

    query = select(InboxItem).where(
        col(InboxItem.org_id) == principal.org_id,
        col(InboxItem.recipient_employee_id) == employee_id(principal),
    )
    scoped = query.subquery()
    count, unread, pending = session.exec(
        select(
            func.count(),
            func.count().filter(scoped.c.read_at.is_(None)),
            func.count().filter(
                scoped.c.done_at.is_(None),
                scoped.c.kind.in_(
                    [
                        InboxKind.INITIAL_REVIEW,
                        InboxKind.QUESTION,
                    ]
                ),
            ),
        ).select_from(scoped)
    ).one()
    rows = session.exec(
        query.order_by(col(InboxItem.created_at).desc(), col(InboxItem.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return InboxList(
        data=[InboxPublic.model_validate(row) for row in rows],
        count=count,
        unread_count=unread,
        pending_count=pending,
    )


def mark_read(principal: Principal, session: Session, inbox_id: UUID) -> InboxPublic:
    from app.domains.change_request.employee import EmployeeRecordNotFound, employee_id

    item = session.exec(
        select(InboxItem)
        .where(
            col(InboxItem.org_id) == principal.org_id,
            col(InboxItem.recipient_employee_id) == employee_id(principal),
            InboxItem.id == inbox_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if item is None:
        raise EmployeeRecordNotFound()
    item.read_at = item.read_at or utc_now()
    session.flush()
    return InboxPublic.model_validate(item)


def get_pending_questions(
    principal: Principal, session: Session, skip: int = 0, limit: int = 50
) -> QuestionsList:
    from app.domains.change_request.employee import employee_id

    query = select(PendingQuestion).where(
        PendingQuestion.org_id == principal.org_id,
        PendingQuestion.target_employee_id == employee_id(principal),
        PendingQuestion.status == QuestionStatus.OPEN,
    )
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(PendingQuestion.created_at).desc(), col(PendingQuestion.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return QuestionsList(
        data=[QuestionPublic.model_validate(row) for row in rows], count=count
    )


def read_question(
    principal: Principal, session: Session, question_id: UUID
) -> QuestionPublic:
    return QuestionPublic.model_validate(_question(principal, session, question_id))


def _question(
    principal: Principal, session: Session, question_id: UUID
) -> PendingQuestion:
    from app.domains.change_request.employee import EmployeeRecordNotFound, employee_id

    question = session.exec(
        select(PendingQuestion)
        .where(
            PendingQuestion.org_id == principal.org_id,
            PendingQuestion.target_employee_id == employee_id(principal),
            PendingQuestion.id == question_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if question is None:
        raise EmployeeRecordNotFound()
    return question


def answer_question(
    principal: Principal, session: Session, question_id: UUID, answer: str
) -> QuestionPublic:
    from app.domains.change_request.employee import RequestConflict

    answer = AnswerInput(answer=answer).answer.strip()
    question = _question(principal, session, question_id)
    if question.status != QuestionStatus.OPEN:
        raise RequestConflict("问题已回答或已关闭")
    now = utc_now()
    evidence, _ = append_evidence(
        session,
        org_id=principal.org_id,
        subject_employee_id=question.target_employee_id,
        kind=EvidenceKind.SELF_REPORT,
        source="self_report:question",
        source_ref=str(question.id),
        title="待确认问题的本人回答",
        content={"question": question.question, "text": answer},
        visibility=Visibility.RESTRICTED,
        provenance={"question_id": str(question.id), "user_id": str(principal.user_id)},
        occurred_at=now,
    )
    question.answer_evidence_id = evidence.id
    question.status = QuestionStatus.ANSWERED
    question.answered_at = now
    session.exec(
        update(InboxItem)
        .where(
            col(InboxItem.org_id) == principal.org_id,
            col(InboxItem.recipient_employee_id) == question.target_employee_id,
            col(InboxItem.question_id) == question.id,
        )
        .values(done_at=now, read_at=func.coalesce(InboxItem.read_at, now))
    )
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="question.answered",
        target_type="pending_question",
        target_id=str(question.id),
        details={"evidence_id": str(evidence.id)},
    )
    session.flush()
    return QuestionPublic.model_validate(question)
