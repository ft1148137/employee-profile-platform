from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import Organization
from app.domains.setup import steps
from app.domains.setup.models import SetupStatus, SetupStepState
from app.domains.setup.schemas import SetupPublic, SetupStepPublic


class SetupStepError(HTTPException):
    pass


def read_setup(session: Session, org_id: UUID) -> SetupPublic:
    states = {
        state.step_key: state.status
        for state in session.exec(
            select(SetupStepState).where(SetupStepState.org_id == org_id)
        ).all()
    }
    result = []
    for step in sorted(steps.STEPS, key=lambda step: step.order):
        status: SetupStatus | Literal["pending"] = (
            SetupStatus.COMPLETED if step.implicit else states.get(step.key, "pending")
        )
        result.append(
            SetupStepPublic(
                key=step.key,
                title=step.title,
                description=step.description,
                order=step.order,
                required=step.required,
                skippable=step.skippable,
                status=status,
                blockers=step.check(session, org_id) if status == "pending" else [],
            )
        )
    return SetupPublic(
        steps=result,
        completed=all(s.status == "completed" for s in result if s.required),
        next_step_key=next((s.key for s in result if s.status == "pending"), None),
    )


def update_step(
    session: Session, principal: Principal, key: str, status: SetupStatus
) -> SetupPublic:
    step = next((s for s in steps.STEPS if s.key == key), None)
    if step is None:
        raise SetupStepError(status_code=404, detail="没有这个设置步骤")
    # Serialize updates per organization so concurrent retries cannot duplicate audits.
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    state = session.get(SetupStepState, (principal.org_id, key))
    completed = step.implicit or (state and state.status == SetupStatus.COMPLETED)
    if status == SetupStatus.SKIPPED:
        if completed:
            raise SetupStepError(status_code=409, detail="这一步已经完成")
        if not step.skippable:
            raise SetupStepError(status_code=422, detail="这一步不能跳过")
    elif completed:
        return read_setup(session, principal.org_id)
    else:
        blockers = step.check(session, principal.org_id)
        if blockers:
            raise SetupStepError(
                status_code=422,
                detail={
                    "code": "setup_step_blocked",
                    "message": "这一步还不能完成",
                    "blockers": blockers,
                },
            )
    if state is None or state.status != status:
        assert principal.user_id is not None
        state = state or SetupStepState(
            org_id=principal.org_id,
            step_key=key,
            status=status,
            updated_by_user_id=principal.user_id,
        )
        state.status = status
        state.updated_by_user_id = principal.user_id
        state.updated_at = utc_now()
        session.add(state)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action=f"setup.step_{status.value}",
            target_type="setup_step",
            target_id=key,
            details={"step_key": key},
        )
    session.commit()
    return read_setup(session, principal.org_id)
