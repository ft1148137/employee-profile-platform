import re
from dataclasses import replace
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, JsonValue, ValidationError
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.bot import attachments, reviews
from app.domains.bot import schemas as s
from app.domains.bot.levels import name_levels
from app.domains.bot.models import BotSession
from app.domains.capability.proposals import suggest_capability
from app.domains.capability.schemas import SuggestCapability
from app.domains.change_request import employee
from app.domains.change_request.models import RequestStatus
from app.domains.change_request.schemas import ItemInput, ToolInput
from app.domains.drafting.settings import read_auto_draft
from app.domains.employee_tools import EMPLOYEE_TOOLS, DraftRequestInput
from app.domains.evidence.models import Evidence
from app.domains.model.pi_client import ToolDefinition
from app.domains.org.models import Department, Employee, EmploymentStatus
from app.domains.profile.service import read_profile
from app.domains.review import direct

TOOLS = {t.name: t for t in EMPLOYEE_TOOLS if t.bot_allowed}
TITLES = {t.name: t.description for t in TOOLS.values()} | {
    "read_attachment": "读取会话附件",
    "suggest_capability": "建议新增能力",
    "list_team_members": "查看团队成员",
    "get_member_profile": "查看下属画像",
    "prepare_profile_edit": "预览直接修改画像",
    "list_pending_reviews": "查看待审批请求",
    "get_review": "查看审批详情",
    "prepare_review_decision": "预览审批决定",
}
PROJECTIONS: dict[str, type[s.Projection]] = {
    "get_my_profile": s.Profile,
    "list_my_evidence": s.EvidenceList,
    "get_my_requests": s.Requests,
    "get_capability_dictionary": s.Dictionary,
    "draft_update_request": s.Request,
    "preview_request": s.Preview,
    "get_pending_questions": s.Questions,
    "answer_question": s.Question,
}


class TeamListInput(ToolInput):
    q: str | None = Field(default=None, max_length=100)
    department_id: UUID | None = None


class MemberInput(ToolInput):
    employee_id: UUID


class ProfileEditInput(direct.DirectEditInput):
    employee_id: UUID


TEAM_INPUTS: dict[str, type[ToolInput]] = {
    "list_team_members": TeamListInput,
    "get_member_profile": MemberInput,
    "prepare_profile_edit": ProfileEditInput,
}


class TeamMemberNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "找不到该员工")


def team_member(db: Session, p: Principal, employee_id: UUID) -> Employee:
    subject = db.exec(
        select(Employee).where(
            Employee.org_id == p.org_id,
            Employee.id == employee_id,
            col(Employee.department_id).in_(p.managed_department_ids),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).first()
    if subject is None:
        raise TeamMemberNotFound()
    return subject


def team_call(
    db: Session,
    p: Principal,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
) -> JsonValue:
    if not p.managed_department_ids:
        return {"error": "不允许调用此工具"}
    p = replace(p, is_org_admin=False)
    try:
        body = TEAM_INPUTS[name].model_validate(arguments)
    except ValidationError:
        return {"error": "工具参数不正确"}
    if isinstance(body, TeamListInput):
        query = (
            select(Employee, Department)
            .join(
                Department,
                (col(Department.org_id) == Employee.org_id)
                & (col(Department.id) == Employee.department_id),
            )
            .where(
                Employee.org_id == p.org_id,
                col(Employee.department_id).in_(p.managed_department_ids),
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
        )
        if body.q:
            query = query.where(
                col(Employee.display_name).icontains(body.q, autoescape=True)
            )
        if body.department_id:
            query = query.where(Employee.department_id == body.department_id)
        return {
            "data": [
                {
                    "employee_id": str(e.id),
                    "name": e.display_name,
                    "department": d.name,
                    "title": e.title,
                }
                for e, d in db.exec(query.order_by(col(Employee.id)).limit(100)).all()
            ]
        }
    assert isinstance(body, (MemberInput, ProfileEditInput))
    subject = team_member(db, p, body.employee_id)
    if isinstance(body, ProfileEditInput):
        edit = direct.DirectEditInput(
            items=body.items, base_revision=body.base_revision
        )
        preview = direct.preview_direct_edit(p, db, subject.id, edit)
        card = s.ProfileEditCard(
            **preview.model_dump(),
            employee_id=subject.id,
            employee_name=subject.display_name,
            edit=edit,
        )
        card.actions = [
            s.ProfileEditAction(action=a, card_id=card.card_id)
            for a in ("confirm_profile_edit", "continue_profile_edit")
        ]
        card = name_levels(db, p, card)
        cards.append(card)
        return {
            "card_id": str(card.card_id),
            "items": [i.model_dump(mode="json") for i in card.items],
            "revision": card.revision,
        }
    profile = read_profile(db, p, subject.id)
    projected = name_levels(db, p, s.Profile.model_validate(profile))
    return {
        "employee_id": str(subject.id),
        "revision": profile.latest_published.revision
        if profile.latest_published
        else None,
        "items": [item.model_dump(mode="json") for item in projected.items],
    }


def definitions(p: Principal | None = None) -> list[ToolDefinition]:
    team = (
        [
            ToolDefinition(
                name=name,
                description=TITLES[name]
                + "；仅限管辖部门。"
                + (
                    "只校验，不写入；必须点击确认修改才发布并通知员工。"
                    if name == "prepare_profile_edit"
                    else ""
                ),
                parameters=model.model_json_schema(),
            )
            for name, model in TEAM_INPUTS.items()
        ]
        if p and p.managed_department_ids
        else []
    )
    return (
        team
        + [
            attachments.READ_TOOL,
            ToolDefinition(
                name="suggest_capability",
                description=TITLES["suggest_capability"]
                + "；先复述名称和说明，员工明确同意后才调用；只建议，不改画像或词典。",
                parameters=SuggestCapability.model_json_schema(),
            ),
        ]
        + reviews.definitions()
        + [
            ToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.input_model.model_json_schema(),
            )
            for t in TOOLS.values()
        ]
    )


def redact_text(value: str) -> str:
    return re.sub(
        r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+", "[已隐藏]", value, flags=re.IGNORECASE
    )


def redact(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def preview_card(db: Session, p: Principal, request_id: UUID) -> s.PreviewCard:
    preview = name_levels(
        db, p, s.Preview.model_validate(employee.preview(p, db, request_id))
    )
    return s.PreviewCard(
        **preview.model_dump(),
        actions=[
            s.RequestAction(
                action=a, request_id=preview.request_id, revision=preview.revision
            )
            for a in ("confirm_submit", "continue_editing")
        ],
    )


def status_card(request: s.Request) -> s.StatusCard:
    return s.StatusCard(
        **request.model_dump(),
        actions=[
            s.RequestAction(
                action="withdraw", request_id=request.id, revision=request.revision
            )
        ]
        if request.status == "pending_review"
        else [],
    )


def question_card(question: s.Question) -> s.QuestionCard:
    return s.QuestionCard(
        **question.model_dump(),
        actions=["answer_question"] if question.status == "open" else [],
    )


def request_card(cards: list[s.Message], card: s.PreviewCard | s.StatusCard) -> None:
    request_id = card.request_id if isinstance(card, s.PreviewCard) else card.id
    cards[:] = [
        existing
        for existing in cards
        if not (
            isinstance(existing, s.PreviewCard)
            and existing.request_id == request_id
            or isinstance(existing, s.StatusCard)
            and existing.id == request_id
        )
    ]
    cards.append(card)


def pending_overlap(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    items: list[ItemInput],
) -> tuple[list[ItemInput], dict[str, JsonValue]]:
    keys = {item.capability_key for item in items}
    blocked: set[str] = set()
    overlaps: list[JsonValue] = []
    for request in employee.my_requests(p, db).data:
        if request.status != RequestStatus.PENDING_REVIEW:
            continue
        matching = [
            i for i in request.items if i.decision is None and i.capability.key in keys
        ]
        if not matching:
            continue
        blocked.update(i.capability.key for i in matching)
        # Withdrawal retracts these reports; resubmission recreates them from the statements.
        reports = set(
            db.exec(
                select(Evidence.id).where(
                    Evidence.org_id == p.org_id,
                    Evidence.subject_employee_id == p.employee_id,
                    col(Evidence.provenance)["request_id"].astext == str(request.id),
                )
            ).all()
        )
        overlaps.append(
            {
                "request_id": str(request.id),
                "capabilities": [i.capability.name for i in matching],
                "submitted_at": request.submitted_at.astimezone(
                    ZoneInfo("Asia/Shanghai")
                ).isoformat()
                if request.submitted_at
                else None,
                "reviewers": [r.model_dump(mode="json") for r in request.reviewers],
                "original_items": [
                    ItemInput(
                        op=i.op,
                        capability_key=i.capability.key,
                        statement=i.after.statement if i.after else "撤回或补充依据",
                        level=i.after.level if i.after else None,
                        owner_statement=i.owner_statement,
                        objection=i.objection,
                        uncertainty=i.uncertainty,
                        evidence_ids=[
                            e.evidence_id
                            for e in i.evidence
                            if e.evidence_id not in reports
                        ],
                    ).model_dump(mode="json")
                    for i in request.items
                    if i.decision is None
                ],
            }
        )
        if request.id not in session.request_ids:
            session.request_ids = [*session.request_ids, request.id]
            db.add(session)
        request_card(
            cards, status_card(name_levels(db, p, s.Request.model_validate(request)))
        )
    notice: dict[str, JsonValue] = {}
    if overlaps:
        notice = {
            "overlaps": overlaps,
            "message": "重叠能力已有待审请求，本次没有为这些能力保存草稿或生成预览。"
            "请说明下面各能力的提交时间（北京时间）和正在等谁审批，并让员工选择："
            "等原请求审完再补充；或者点击撤回卡，撤回原请求后合并重提。"
            "员工明确选择合并重提时，先请其点击撤回卡；确认撤回成功后，"
            "将 original_items 中原请求的全部待审内容与本次补充合并，"
            "复用 draft_update_request 的 items 参数新建草稿，保留原有等级和依据说明，"
            "再请员工核对预览并点击确认提交。其他不重叠的能力照常保存。",
        }
    return [item for item in items if item.capability_key not in blocked], notice


def call(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
) -> JsonValue:
    if name == "suggest_capability":
        try:
            suggestion = SuggestCapability.model_validate(arguments)
            return suggest_capability(
                p, db, **suggestion.model_dump(), commit=False
            ).model_dump(mode="json")
        except ValidationError:
            return {"error": "工具参数不正确"}
        except HTTPException as exc:
            return {"error": exc.detail}
    if name == "read_attachment":
        try:
            body_attachment = attachments.ReadAttachmentInput.model_validate(arguments)
            return redact(
                attachments.read_attachment(
                    db, p, "bot", session.id, body_attachment.attachment_id
                )
            )
        except ValidationError:
            return {"error": "工具参数不正确"}
        except HTTPException:
            return {"error": "附件不存在"}
    if name in TEAM_INPUTS:
        return redact(team_call(db, p, cards, name, arguments))
    if name in reviews.INPUTS:
        return redact(reviews.call(db, p, session, cards, name, arguments))
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": "不允许调用此工具，请使用卡片动作"}
    try:
        body = tool.input_model.model_validate(arguments)
    except ValidationError:
        return {"error": "工具参数不正确"}
    kwargs = dict(body)
    notice: dict[str, JsonValue] = {}
    if name == "draft_update_request":
        assert isinstance(body, DraftRequestInput)
        remaining, notice = pending_overlap(db, p, session, cards, body.items)
        if notice and not remaining:
            return redact(notice)
        kwargs["items"] = remaining
        kwargs.update(via="bot", client_name="内置机器人")
    result = PROJECTIONS[name].model_validate(tool.handler(p, db, **kwargs))
    result = name_levels(db, p, result)
    if isinstance(result, s.Profile):
        auto = read_auto_draft(db, p)
        result.auto_draft_enabled = auto.enabled and not auto.org_disabled
    if isinstance(result, (s.Request, s.Preview)):
        request_id = result.id if isinstance(result, s.Request) else result.request_id
        if request_id not in session.request_ids:
            session.request_ids = [*session.request_ids, request_id]
            db.add(session)
        request_card(cards, preview_card(db, p, request_id))
    if isinstance(result, s.Requests):
        for request in result.data:
            if request.id not in session.request_ids:
                session.request_ids = [*session.request_ids, request.id]
                db.add(session)
            request_card(
                cards,
                preview_card(db, p, request.id)
                if request.status in ("draft", "pending_owner")
                else status_card(request),
            )
    if isinstance(result, s.Questions):
        cards.extend(question_card(q) for q in result.data)
    if isinstance(result, s.Question):
        cards.append(question_card(result))
    return redact(result.model_dump(mode="json") | notice)
