import logging
from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains import manager_tools
from app.domains.base import utc_now
from app.domains.bot import attachments, delivery, project_drafts, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
    begin_turn,
)
from app.domains.bot.levels import name_levels
from app.domains.bot.models import BotSession
from app.domains.bot.presentation import DEADLINE_MESSAGE, capacity_failure, retry_time
from app.domains.bot.prompts import system_prompt
from app.domains.capacity import assistant as capacity
from app.domains.change_request import employee
from app.domains.change_request.models import ReviewVia
from app.domains.inbox import service as inbox
from app.domains.model import pi_client
from app.domains.model.client import ModelServiceError, ModelUnavailable
from app.domains.model.service import model_config, read_setting, supports_images
from app.domains.org.models import Organization
from app.domains.review import direct
from app.models import User

logger = logging.getLogger(__name__)


def _save(
    db: Session,
    session: BotSession,
    messages: list[s.Message],
    steps: list[s.ToolStep] | None = None,
    principal: Principal | None = None,
    *,
    scope: dict[str, JsonValue] | None = None,
) -> s.TurnResponse:
    response = s.TurnResponse(messages=messages, steps=steps or [])
    payload = response.model_dump(mode="json")
    if scope is None and principal is not None:
        scope = store.session_scope(db, principal)
    if scope is not None:
        payload["scope"] = scope
    store.append_message(
        db,
        session,
        "assistant",
        payload,
    )
    response._principal = principal
    response._scope = scope
    return delivery.protect(db, response)


def turn(
    db: Session,
    p: Principal,
    session_id: UUID,
    text: str,
    *,
    attachment_ids: list[UUID] | None = None,
    stream: pi_client.TurnStream | None = None,
    on_started: Callable[[], None] | None = None,
) -> s.TurnResponse:
    session = store.get_session(db, p, session_id)
    setting = read_setting(db, p.org_id)
    snapshot, model_input, notice = attachments.prepare(
        db, p, "bot", session_id, attachment_ids or [], text
    )
    user_payload = s.UserMessage(text=text, attachments=snapshot).model_dump(
        mode="json", exclude_defaults=True
    )
    admitted = False
    started = monotonic()
    steps: list[s.ToolStep] = []
    cards: list[s.Message] = []
    project_draft_attempted = False
    project_draft_error: str | None = None
    manual_fallback = False
    # Keep the scope that admitted inherited state, not a later persistence snapshot.
    scope = store.session_scope(db, p)
    employee_ids: set[str] = set()

    def report_reads(ids: set[str]) -> None:
        employee_ids.update(ids)
        read_ids: list[JsonValue] = []
        read_ids.extend(sorted(employee_ids))
        scope["read_employee_ids"] = read_ids

    def call(name: str, args: dict[str, JsonValue]) -> JsonValue:
        nonlocal project_draft_attempted, project_draft_error
        title = tools.TITLES.get(
            name,
            tools.COORDINATION_TITLES.get(
                name, capacity.TITLES.get(name, "不允许的工具")
            ),
        )
        if stream:
            stream.check()
            stream.on_tool(title, "start", True)
        reported = False

        def on_read(ids: set[str]) -> None:
            nonlocal reported
            reported = True
            report_reads(ids)

        try:
            result = tools.call(db, p, session, cards, name, args, on_read=on_read)
        except Exception:
            steps.append(s.ToolStep(name=title, ok=False))
            if stream:
                stream.on_tool(title, "end", False)
            raise
        finally:
            if not reported:
                # Unknown tools retain both admission and post-call visibility.
                for snapshot in (visible_scope, store.session_scope(db, p)):
                    ids = snapshot["visible_employee_ids"]
                    if isinstance(ids, list):
                        report_reads({str(id) for id in ids})
        ok = not (isinstance(result, dict) and "error" in result)
        if name == "coordination" and args.get("function") == "prepare_project_draft":
            project_draft_attempted = True
            error = result.get("error") if isinstance(result, dict) else None
            project_draft_error = error if isinstance(error, str) else None
        steps.append(s.ToolStep(name=title, ok=ok))
        if stream:
            stream.on_tool(title, "end", ok)
        return result

    def on_text(text: str) -> None:
        if stream and not project_draft_attempted:
            stream.on_text(text)

    try:
        visible_scope = dict(scope)
        scope.pop("visible_employee_ids")
        scope["read_scope_format"] = store.READ_SCOPE_FORMAT
        report_reads(set())
        session_state = store.state_for_turn(
            db, p, session, scope=visible_scope, on_read=report_reads
        )
        with begin_turn(
            db, p, session, user_payload, stream.cancelled if stream else None
        ):
            admitted = True
            if on_started:
                try:
                    on_started()
                except Exception:
                    logger.warning("Bot processing receipt failed")
            if setting is None or not setting.enabled or not setting.model:
                return _save(
                    db,
                    session,
                    [
                        capacity_failure(
                            db,
                            p,
                            "\n".join(
                                filter(
                                    None,
                                    [
                                        notice,
                                        "工作助手需要先配置模型，你也可以在「我的画像」直接修改",
                                    ],
                                )
                            ),
                        )
                    ],
                    principal=p,
                    scope=scope,
                )
            config = model_config(setting)
            images = []
            if notice and supports_images(config):
                images = attachments.model_images(db, p, "bot", session_id, snapshot)
                notice = ""
            if notice and stream:
                stream.on_text(notice + "\n")
            result = pi_client.run_turn(
                config,
                mode="chat",
                images=images,
                session_state=session_state,
                system_prompt=system_prompt(db, p, session.channel, session),
                input=tools.redact_text(model_input),
                tools=tools.definitions(p, db=db, channel=session.channel),
                on_tool_call=call,
                stream=replace(stream, on_text=on_text, on_tool=lambda *_: None)
                if stream
                else None,
            )
            if stream:
                stream.check()
            # Only completed model turns may leave drafts or replies behind.
            if result.stop_reason != "stop":
                raise ModelUnavailable()
            store.save_scoped_state(db, p, session, result.session_state, scope=scope)
            messages: list[s.Message] = []
            reply_text = project_draft_error if project_draft_attempted else result.text
            if notice or reply_text:
                messages.append(
                    s.TextMessage(text="\n".join(filter(None, [notice, reply_text])))
                )
            messages.extend(cards)
            if stream:
                stream.check()
            return _save(db, session, messages, steps, principal=p, scope=scope)
    except pi_client.TurnCancelled:
        return s.TurnResponse(messages=[])
    except BotBusy:
        message = "当前请求较多，请稍后再试"
    except BotDraftingPriority:
        message = "正在起草画像，请稍后再试"
    except BotRateLimited as error:
        message = f"每小时 30 轮已用完，{retry_time(error.retry_at)} 恢复"
    except BotQuotaPaused as error:
        message = f"模型额度暂时用完，预计 {retry_time(error.retry_at)} 恢复"
    except pi_client.PiSessionInvalid:
        store.save_state(db, session, None)
        logger.warning("session_id=%s code=bad_request", session.id)
        message = "之前的对话上下文已失效，已经重新开始，请再说一次"
    except ModelUnavailable as error:
        manual_fallback = True
        logger.warning(
            "session_id=%s code=%s elapsed_ms=%d",
            session.id,
            error.code,
            (monotonic() - started) * 1000,
        )
        message = (
            DEADLINE_MESSAGE
            if error.code == "deadline"
            else str(error)
            if isinstance(error, ModelServiceError)
            else "模型服务暂时不可用，请稍后再试"
        )
    text = "\n".join(filter(None, [notice, message]))
    messages = [
        capacity_failure(db, p, text) if manual_fallback else s.TextMessage(text=text)
    ]
    if stream and stream.cancelled.is_set():
        db.rollback()
        return s.TurnResponse(messages=[])
    if admitted:
        if stream:
            session.turn_count += 1
            store.append_message(db, session, "user", user_payload)
        response = _save(db, session, messages, steps, principal=p, scope=scope)
        if stream and stream.cancelled.is_set():
            db.rollback()
            return s.TurnResponse(messages=[])
        db.commit()
        return response
    return s.TurnResponse(messages=messages)


def act(
    db: Session,
    p: Principal,
    session_id: UUID,
    action: s.Action,
    *,
    client_name: str = "内置助手",
    commit: bool = True,
    web_project_confirmation: bool = False,
) -> s.TurnResponse:
    if isinstance(action, s.ProjectDraftAction):
        if not web_project_confirmation:
            raise project_drafts.DraftUnavailable()
        db.exec(
            select(Organization)
            .where(Organization.id == p.org_id)
            .with_for_update(read=True)
        ).one()
        user = db.get(User, p.user_id, populate_existing=True) if p.user_id else None
        if user is None or not user.is_active:
            raise store.BotSessionNotFound()
        p = get_org_principal(p.org_id, user, db)
    session = store.get_session(db, p, session_id, lock=True)
    if session.lease_until and session.lease_until > utc_now():
        return s.TurnResponse(messages=[s.TextMessage(text="当前请求较多，请稍后再试")])
    cards = [
        card
        for message in store.messages_for(db, session)
        if message.role == "assistant" and isinstance(message.payload, dict)
        for card in s.TurnResponse.model_validate(message.payload).messages
    ]
    messages: list[s.Message]
    if isinstance(action, s.ProjectDraftAction):
        messages = [project_drafts.confirm(db, p, session, cards, action)]
    elif isinstance(action, s.CapacityAction):
        messages = [capacity.act(db, p, session, cards, action)]
    elif isinstance(action, s.ProfileEditAction):
        edit_card = next(
            (
                c
                for c in cards
                if isinstance(c, s.ProfileEditCard) and action in c.actions
            ),
            None,
        )
        if edit_card is None:
            raise employee.RequestConflict("请重新查看画像修改确认卡")
        team_principal = replace(p, is_org_admin=False)
        tools.team_member(db, team_principal, edit_card.employee_id)
        if action.action == "continue_profile_edit":
            direct.preview_direct_edit(
                team_principal, db, edit_card.employee_id, edit_card.edit
            )
            messages = [s.TextMessage(text="请告诉我需要修改的内容")]
        else:
            direct.direct_edit(
                team_principal,
                db,
                edit_card.employee_id,
                edit_card.edit,
                via="bot",
                client_name=session.channel,
                commit=False,
            )
            messages = [s.TextMessage(text="画像已发布，已通知员工")]
    elif isinstance(action, s.ReviewAction):
        card = next(
            (c for c in cards if isinstance(c, s.ReviewCard) and action in c.actions),
            None,
        )
        if card is None:
            raise employee.RequestConflict("请重新查看审批卡片")
        if action.action == "continue_review":
            review = manager_tools.get_review(p, db, action.request_id)
            if review.revision != action.revision:
                raise employee.RequestConflict("内容已更新，请刷新查看")
            messages = [s.TextMessage(text="请告诉我需要修改的决定或原因")]
        else:
            manager_tools.decide_review_items(
                p,
                db,
                action.request_id,
                action.revision,
                card.decisions,
                via=ReviewVia.WEB if session.channel == "web" else ReviewVia.IM,
                client_name=session.channel,
            )
            messages = [
                s.TextMessage(
                    text="审批决定已保存，请在网页查看结果", review_link="/reviews"
                )
            ]
    elif isinstance(action, s.QuestionAction):
        inbox.read_question(p, db, action.question_id)
        if not any(
            isinstance(c, s.QuestionCard)
            and c.id == action.question_id
            and "answer_question" in c.actions
            for c in cards
        ):
            raise employee.RequestConflict("请重新查看问题")
        question = inbox.answer_question(p, db, action.question_id, action.answer)
        messages = [tools.question_card(s.Question.model_validate(question))]
    else:
        confirming = action.action == "confirm_submit"
        preview = employee.preview(
            p, db, action.request_id, for_submission=action.action != "withdraw"
        )
        if preview.revision != action.revision:
            raise employee.RequestConflict("内容已更新，请重新预览")
        matching_cards = [
            c
            for c in cards
            if isinstance(c, (s.PreviewCard, s.StatusCard)) and action in c.actions
        ]
        if not matching_cards:
            raise employee.RequestConflict("内容已更新，请重新预览")
        immediate_retractions = {
            i.after.capability.key
            for i in preview.items
            if confirming
            and i.submission_notice == employee.IMMEDIATE_RETRACTION_NOTICE
        }
        if action.action == "continue_editing":
            messages = [s.TextMessage(text="请告诉我需要修改的内容")]
        # Legacy previews can share the same confirmation action.
        elif immediate_retractions and not all(
            isinstance(card, s.PreviewCard)
            and immediate_retractions.issubset(
                i.after.capability.key
                for i in card.items
                if i.submission_notice == employee.IMMEDIATE_RETRACTION_NOTICE
            )
            for card in matching_cards
        ):
            messages = [tools.preview_card(db, p, action.request_id)]
        else:
            try:
                request = (
                    employee.submit(
                        p,
                        db,
                        action.request_id,
                        action.revision,
                        "bot",
                        client_name,
                        base_revision=action.base_revision,
                    )
                    if confirming
                    else employee.withdraw(p, db, action.request_id)
                )
                messages = [
                    tools.status_card(
                        name_levels(db, p, s.Request.model_validate(request))
                    )
                ]
            except employee.StaleInitialDraft as exc:
                messages = [
                    s.TextMessage(text=exc.message),
                    tools.preview_card(db, p, action.request_id),
                ]
    response = _save(db, session, messages, None, p)
    if commit:
        db.commit()
    return response
