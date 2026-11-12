from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.admin_assistant.models import AdminAssistantSession
from app.domains.bot import store as messages


def create_session(db: Session, principal: Principal) -> AdminAssistantSession:
    if not principal.is_org_admin or principal.user_id is None:
        raise messages.BotSessionNotFound()
    row = AdminAssistantSession(org_id=principal.org_id, user_id=principal.user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(
    db: Session, principal: Principal, session_id: UUID, *, lock: bool = False
) -> AdminAssistantSession:
    query = (
        select(AdminAssistantSession)
        .where(
            AdminAssistantSession.org_id == principal.org_id,
            AdminAssistantSession.user_id == principal.user_id,
            AdminAssistantSession.id == session_id,
        )
        .execution_options(populate_existing=True)
    )
    row = db.exec(query.with_for_update() if lock else query).one_or_none()
    if row is None or not principal.is_org_admin:
        raise messages.BotSessionNotFound()
    return row


def ensure_setup_session(db: Session, principal: Principal) -> None:
    from app.domains.admin_assistant.schemas import (
        AdminTurnResponse,
        SetupChecklistCard,
        SetupFormCard,
    )
    from app.domains.bot.schemas import TextMessage
    from app.domains.setup.wizard import read_setup

    if not principal.is_org_admin or principal.user_id is None:
        raise messages.BotSessionNotFound()
    if read_setup(db, principal.org_id).completed:
        return
    row = AdminAssistantSession(
        org_id=principal.org_id, user_id=principal.user_id, purpose="setup"
    )
    inserted = (
        db.connection()
        .execute(
            insert(AdminAssistantSession)
            .values(**row.model_dump())
            .on_conflict_do_nothing(
                index_elements=["org_id", "user_id"],
                index_where=col(AdminAssistantSession.purpose) == "setup",
            )
            .returning(col(AdminAssistantSession.id))
        )
        .scalar_one_or_none()
    )
    if inserted is not None:
        row = db.exec(
            select(AdminAssistantSession).where(
                AdminAssistantSession.org_id == principal.org_id,
                AdminAssistantSession.id == inserted,
            )
        ).one()
        messages.append_message(
            db,
            row,
            "assistant",
            AdminTurnResponse(
                messages=[
                    TextMessage(
                        text="欢迎！我会带你完成企业信息、名单、能力词典、员工账号、数据来源、账号匹配、模型、审批规则和初始画像设置。企业信息与模型是必做项，模型可以暂不配置。模型可用之前，先用下面的安全表单完成这两步；凭据不会发给模型。"
                    ),
                    SetupChecklistCard(),
                    SetupFormCard(step="org_profile"),
                    SetupFormCard(step="model"),
                ]
            ).model_dump(mode="json"),
        )
    db.commit()
