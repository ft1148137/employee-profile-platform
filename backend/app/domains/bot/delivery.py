"""Protect unsent employee-bot replies using their original read scope."""

from collections.abc import Generator
from contextlib import contextmanager

from fastapi import HTTPException
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.domains.bot import schemas as s
from app.domains.bot import store
from app.domains.org.models import Organization
from app.models import User

SCOPE_CHANGED = "人员范围有变化，请重新提问"


def protect(db: Session, response: s.TurnResponse) -> s.TurnResponse:
    p = response._principal
    if p is None or response._scope is None:
        return response
    user = db.get(User, p.user_id, populate_existing=True) if p.user_id else None
    current = None
    if user and user.is_active:
        try:
            current = store.session_scope(db, get_org_principal(p.org_id, user, db))
        except HTTPException as error:
            if error.status_code != 404:
                raise
    if current is None or store.scope_shrank(response._scope, current):
        return response.model_copy(
            update={"messages": [s.TextMessage(text=SCOPE_CHANGED)], "steps": []}
        )
    return response


@contextmanager
def guard(db: Session, response: s.TurnResponse) -> Generator[s.TurnResponse]:
    try:
        if response._principal is not None:
            # Personnel, role and capacity-setting writes take this row FOR UPDATE.
            db.exec(
                select(Organization)
                .where(Organization.id == response._principal.org_id)
                .with_for_update(read=True)
            ).one()
        # Keep this transaction open through ASGI send / IM render.
        yield protect(db, response)
    finally:
        db.rollback()
