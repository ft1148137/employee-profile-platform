import sys

from sqlmodel import Session, select

from app import crud
from app.core.db import engine
from app.domains.account.service import reset_password as reset_account_password
from app.domains.audit.models import ActorKind
from app.domains.audit.service import record_audit
from app.domains.org.models import OrgMembership


def reset_password(username: str) -> int:
    with Session(engine) as session:
        user = crud.get_user_by_username(session=session, username=username)
        if user is None:
            sys.stderr.write(f"找不到登录名 {username}\n")
            return 1
        session.refresh(user, with_for_update=True)
        password = reset_account_password(user)
        org_ids = session.exec(
            select(OrgMembership.org_id).where(OrgMembership.user_id == user.id)
        ).all()
        for org_id in org_ids or (None,):
            record_audit(
                session,
                org_id=org_id,
                actor_type=ActorKind.CLI,
                actor_id=None,
                action="account.password_reset",
                target_type="user",
                target_id=str(user.id),
                details={"user_id": str(user.id), "source": "cli"},
            )
        active = user.is_active
        session.commit()
    if not active:
        sys.stderr.write("这个账号已停用，重置密码不会启用账号\n")
    sys.stdout.write(
        f"登录名：{password.username}\n新密码：{password.initial_password}\n"
    )
    return 0
