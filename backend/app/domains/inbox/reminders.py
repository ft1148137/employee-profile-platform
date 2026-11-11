"""Fixed-template reminders; only questions and inbox entries are appended."""

import logging
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, col, select

from app.domains.capability.models import CapabilityDefinition
from app.domains.drafting.models import UpdateSetting
from app.domains.drafting.settings import drafting_scope
from app.domains.drafting.update_settings import months_before, read_update_settings
from app.domains.evidence.models import Evidence, EvidenceKind, EvidenceStatus
from app.domains.inbox.models import (
    InboxItem,
    InboxKind,
    PendingQuestion,
    QuestionKind,
    QuestionStatus,
)
from app.domains.inbox.service import add_item
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
)
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
    EvidenceRelation,
    ProfileClaim,
)
from app.domains.source.schedule import add_period
from app.models import User

logger = logging.getLogger(__name__)


def generate_questions(
    session: Session, employee: Employee, now: datetime, months: int, since: datetime
) -> None:
    org_id = employee.org_id
    rows = session.exec(
        select(ProfileClaim)
        .where(
            ProfileClaim.org_id == org_id,
            ProfileClaim.subject_employee_id == employee.id,
        )
        .order_by(col(ProfileClaim.created_at), col(ProfileClaim.id))
    ).all()
    superseded = {c.supersedes_claim_id for c in rows}
    claims = [c for c in rows if c.id not in superseded]
    capabilities = {
        c.id: c
        for c in session.exec(
            select(CapabilityDefinition).where(CapabilityDefinition.org_id == org_id)
        ).all()
    }
    opened = {
        (
            q.kind,
            capabilities[q.capability_definition_id].key
            if q.capability_definition_id
            else None,
        )
        for q in session.exec(
            select(PendingQuestion).where(
                PendingQuestion.org_id == org_id,
                PendingQuestion.target_employee_id == employee.id,
                PendingQuestion.status == QuestionStatus.OPEN,
            )
        ).all()
    }
    evidence_by_claim: dict[UUID, list[tuple[ClaimEvidence, Evidence]]] = {}
    for link, evidence in session.exec(
        select(ClaimEvidence, Evidence)
        .join(
            Evidence,
            (col(Evidence.id) == ClaimEvidence.evidence_id)
            & (col(Evidence.org_id) == ClaimEvidence.org_id),
        )
        .where(
            ClaimEvidence.org_id == org_id,
            col(ClaimEvidence.claim_id).in_([c.id for c in claims]),
        )
    ).all():
        evidence_by_claim.setdefault(link.claim_id, []).append((link, evidence))
    candidates: list[tuple[QuestionKind, ProfileClaim | None, str]] = []
    referenced: set[UUID] = set()
    for claim in claims:
        links = evidence_by_claim.get(claim.id, [])
        referenced.update(e.id for _, e in links)
        capability = capabilities[claim.capability_definition_id]
        active = [(link, e) for link, e in links if e.status == EvidenceStatus.ACTIVE]
        name = capability.name
        latest = max((e.occurred_at for _, e in active if e.occurred_at), default=None)
        if any(link.relation == EvidenceRelation.CONTRADICTS for link, _ in active):
            candidates.append(
                (
                    QuestionKind.CONFLICT,
                    claim,
                    f"关于「{name}」，记录之间有出入，能说说实际情况吗？",
                )
            )
        if (
            claim.assessment_status == AssessmentStatus.CONFIRMED
            and latest
            and latest < months_before(now, months)
        ):
            candidates.append(
                (
                    QuestionKind.STALE,
                    claim,
                    f"「{name}」最近一次记录在 {latest.year} 年 {latest.month} 月，现在还在用吗？",
                )
            )
        sources = {e.source for _, e in active if e.kind == EvidenceKind.WORK_RECORD}
        if (
            claim.assessment_status != AssessmentStatus.UNKNOWN
            and active
            and len(sources) <= 1
        ):
            candidates.append(
                (
                    QuestionKind.SINGLE_SOURCE,
                    claim,
                    f"「{name}」目前只有一个来源，还有其他项目可以说明吗？",
                )
            )
    records = session.exec(
        select(Evidence)
        .where(
            Evidence.org_id == org_id,
            Evidence.subject_employee_id == employee.id,
            Evidence.kind == EvidenceKind.WORK_RECORD,
            Evidence.status == EvidenceStatus.ACTIVE,
            col(Evidence.created_at) >= since,
        )
        .order_by(col(Evidence.created_at).desc(), col(Evidence.id))
    ).all()
    for evidence in records:
        if evidence.id not in referenced:
            candidates.append(
                (
                    QuestionKind.RECORD,
                    None,
                    f"「{evidence.title}」（{evidence.source_ref}）里你负责的是哪部分？",
                )
            )
    candidates.append((QuestionKind.GENERAL, None, "最近有新项目或新技能吗？"))
    priority = list(QuestionKind)
    generated = 0
    for kind, candidate, text in sorted(candidates, key=lambda c: priority.index(c[0])):
        capability_id = candidate.capability_definition_id if candidate else None
        key = (kind, capabilities[capability_id].key if capability_id else None)
        if key in opened:
            continue
        question = PendingQuestion(
            org_id=org_id,
            target_employee_id=employee.id,
            kind=kind,
            claim_id=candidate.id if candidate else None,
            capability_definition_id=capability_id,
            question=text,
            reason="定期画像确认",
        )
        session.add(question)
        session.flush()
        add_item(
            session,
            InboxItem(
                org_id=org_id,
                recipient_employee_id=employee.id,
                kind=InboxKind.QUESTION,
                question_id=question.id,
                message=text,
            ),
        )
        opened.add(key)
        generated += 1
        if generated == 2:
            break


def dispatch_due_reminders(session: Session, now: datetime) -> None:
    # Discovery takes no locks; each tenant claims its own period below.
    org_ids = session.exec(
        select(Organization.id)
        .outerjoin(UpdateSetting, col(UpdateSetting.org_id) == Organization.id)
        .where(
            col(UpdateSetting.org_id).is_(None)
            | (
                (col(UpdateSetting.reminder_schedule) != "off")
                & (col(UpdateSetting.next_reminder_at) <= now)
            )
        )
        .order_by(col(Organization.id))
    ).all()
    session.commit()
    for org_id in org_ids:
        try:
            org = session.exec(
                select(Organization)
                .where(Organization.id == org_id)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if org is None:
                session.rollback()
                continue
            settings = read_update_settings(session, org_id)
            due = settings.next_reminder_at
            if due is None or due > now:
                session.commit()
                continue
            period_months = 3 if settings.reminder_schedule == "quarterly" else 12
            since = months_before(due, period_months)
            while due <= now:
                previous = due
                due = add_period(due, settings.reminder_schedule)
                if due <= now:
                    since = previous
            setting = session.get(UpdateSetting, org_id) or UpdateSetting(org_id=org_id)
            setting.next_reminder_at = due
            session.add(setting)
            session.commit()
            # shortcut: A failure after claiming skips remaining employees this period, add resumable employee jobs if retries are required.
            scope = drafting_scope(session, org_id)
            employees = session.exec(
                select(Employee)
                .join(
                    OrgMembership,
                    (col(OrgMembership.employee_id) == Employee.id)
                    & (col(OrgMembership.org_id) == Employee.org_id),
                )
                .join(User, col(User.id) == OrgMembership.user_id)
                .where(
                    Employee.org_id == org_id,
                    Employee.employment_status == EmploymentStatus.ACTIVE,
                    col(User.is_active).is_(True),
                    col(Employee.id).not_in(scope.excluded_employee_ids),
                )
                .order_by(col(Employee.id))
            ).all()
            for employee in employees:
                generate_questions(
                    session, employee, now, settings.stale_after_months, since
                )
                session.commit()
        except Exception as exc:
            session.rollback()
            # Do not include exception text or tracebacks containing employee data.
            logger.error(
                "Reminder generation failed for org %s (%s)", org_id, type(exc).__name__
            )
