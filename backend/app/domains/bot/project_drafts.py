import re
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.domains.bot import schemas as s
from app.domains.bot import store
from app.domains.bot.models import BotSession
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity import demand, project
from app.domains.capacity.demand_schemas import ProjectInput
from app.domains.org.models import Employee

LINK_PURPOSE = "feishu-project-draft"
PRIVATE_TEXT = re.compile(
    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"|[\w.+-]+@[\w.-]+|https?://|员工编号|工号|员工\s*id|个人负荷|周负荷|其他工作占比|"
    r"平台分配|超负荷|满负荷|(?:负荷|可用容量|余量)[^。；，,\n]{0,12}\d",
    re.IGNORECASE,
)


class DraftNeedsCorrection(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


class DraftUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "草稿已更新、已处理或已过期，请查看最新草稿")


def fields(db: Session, p: Principal, body: ProjectInput) -> list[s.CardField]:
    demand.require_requesting_department(p, db, body.requesting_department_id)
    departments = {
        row.id: row.name for row in demand.list_capacity_departments(p, db).data
    }
    capabilities = {
        row.key: s.DictionaryCapability.model_validate(row)
        for row in list_capabilities(db, p.org_id, get_schema(db, p.org_id).id)
    }
    prose = "\n".join(
        [body.title, body.description]
        + [text for role in body.roles for text in (role.role_name, role.description)]
    ).casefold()
    names = db.exec(select(Employee.display_name).where(employee_scope_clause(p)))
    if PRIVATE_TEXT.search(prose) or any(
        name and name.casefold() in prose for name in names
    ):
        raise DraftNeedsCorrection("请修改项目名称、角色或说明，只写项目工作和部门需求")

    def capability_label(key: str, minimum_level: str | None) -> str:
        capability = capabilities.get(key)
        if capability is None:
            raise DraftNeedsCorrection("能力已不存在，请从当前能力词典选择后重新起草")
        if minimum_level is None:
            return f"{capability.name}（不限等级）"
        for level in capability.level_rubric.levels if capability.level_rubric else []:
            if level.key == minimum_level:
                return f"{capability.name}（{level.label}）"
        raise DraftNeedsCorrection("最低等级无效，请从当前能力词典选择后重新起草")

    result = [
        s.CardField(label="需求方", value=departments[body.requesting_department_id])
    ]
    for index, role in enumerate(body.roles, 1):
        department = departments.get(role.target_department_id)
        if department is None:
            raise DraftNeedsCorrection("目标部门已不存在，请重新选择部门")
        lines = [
            department,
            f"主能力：{capability_label(role.capability_key, role.minimum_level)}",
            f"{role.headcount} 人，每人投入 {role.allocation_percent}%",
            f"{role.window_start} 至 {role.window_end}",
        ]
        lines.extend(
            f"{'必需' if item.required else '可选'}：{capability_label(item.key, item.minimum_level)}"
            for item in role.extra_capabilities
        )
        if role.depends_on:
            lines.append(
                "前置角色："
                + "、".join(
                    f"{i + 1}. {body.roles[i].role_name}" for i in role.depends_on
                )
            )
        result.append(
            s.CardField(label=f"{index}. {role.role_name}", value="\n".join(lines))
        )
    return result


def prepare(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    body: ProjectInput,
) -> None:
    card = s.ProjectDraftCard(project=body, fields=fields(db, p, body))
    card.actions = [
        s.ProjectDraftAction(action="confirm_project_draft", card_id=card.card_id)
    ]
    if session.channel == "feishu":
        token = encrypt_secret(f"{LINK_PURPOSE}:{session.id}:{card.card_id}")
        card.web_link = f"/assistant?draft={token}&org={p.org_id}"
    cards[:] = [c for c in cards if not isinstance(c, s.ProjectDraftCard)]
    cards.append(card)


def latest(
    cards: list[s.Message],
    card_id: UUID,
) -> s.ProjectDraftCard:
    card = next((c for c in reversed(cards) if isinstance(c, s.ProjectDraftCard)), None)
    if card is None or card.card_id != card_id:
        raise DraftUnavailable()
    return card


def confirm(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    action: s.ProjectDraftAction,
) -> s.ProjectDraftCard:
    card = latest(cards, action.card_id)
    if action not in card.actions or card.project_id is not None:
        raise DraftUnavailable()
    if fields(db, p, card.project) != card.fields:
        raise DraftUnavailable()
    saved = project.submit_project(
        p,
        db,
        card.project,
        via="feishu" if session.channel == "feishu" else "bot",
        client_name=session.channel,
    )
    return card.model_copy(update={"project_id": saved.id, "actions": []})


def feishu_link(db: Session, p: Principal, token: str) -> tuple[UUID, UUID]:
    try:
        purpose, session, card = decrypt_secret(token).split(":")
        if purpose != LINK_PURPOSE:
            raise ValueError()
        session_id, card_id = UUID(session), UUID(card)
    except SecretUnavailable, ValueError:
        raise store.BotSessionNotFound() from None
    if store.get_session(db, p, session_id).channel != "feishu":
        raise store.BotSessionNotFound()
    return session_id, card_id
