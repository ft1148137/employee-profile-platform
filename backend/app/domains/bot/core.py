import logging
from dataclasses import replace
from time import monotonic
from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains import manager_tools
from app.domains.base import utc_now
from app.domains.bot import attachments, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
    begin_turn,
)
from app.domains.bot.models import BotSession
from app.domains.bot.prompts import system_prompt
from app.domains.change_request import employee
from app.domains.change_request.models import ReviewVia
from app.domains.inbox import service as inbox
from app.domains.model import pi_client
from app.domains.model.client import ModelServiceError, ModelUnavailable
from app.domains.model.service import model_config, read_setting, supports_images

logger = logging.getLogger(__name__)


def _save(
    db: Session,
    session: BotSession,
    messages: list[s.Message],
    steps: list[s.ToolStep] | None = None,
) -> s.TurnResponse:
    response = s.TurnResponse(messages=messages, steps=steps or [])
    store.append_message(db, session, "assistant", response.model_dump(mode="json"))
    return response


def turn(
    db: Session,
    p: Principal,
    session_id: UUID,
    text: str,
    *,
    attachment_ids: list[UUID] | None = None,
    stream: pi_client.TurnStream | None = None,
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

    def call(name: str, args: dict[str, JsonValue]) -> JsonValue:
        title = tools.TITLES.get(name, "不允许的工具")
        if stream:
            stream.check()
            stream.on_tool(title, "start", True)
        try:
            result = tools.call(db, p, session, cards, name, args)
        except Exception:
            steps.append(s.ToolStep(name=title, ok=False))
            if stream:
                stream.on_tool(title, "end", False)
            raise
        ok = not (isinstance(result, dict) and "error" in result)
        steps.append(s.ToolStep(name=title, ok=ok))
        if stream:
            stream.on_tool(title, "end", ok)
        return result

    try:
        with begin_turn(
            db, p, session, user_payload, stream.cancelled if stream else None
        ):
            admitted = True
            if setting is None or not setting.enabled or not setting.model:
                return _save(
                    db,
                    session,
                    [
                        s.TextMessage(
                            text="\n".join(
                                filter(
                                    None,
                                    [
                                        notice,
                                        "机器人需要先配置模型，你也可以在画像页直接修改",
                                    ],
                                )
                            )
                        )
                    ],
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
                session_state=store.load_state(db, session),
                system_prompt=system_prompt(db, p, session.channel),
                input=tools.redact_text(model_input),
                tools=tools.definitions(),
                on_tool_call=call,
                stream=replace(stream, on_tool=lambda *_: None) if stream else None,
            )
            if stream:
                stream.check()
            # Only completed model turns may leave drafts or replies behind.
            if result.stop_reason != "stop":
                raise ModelUnavailable()
            store.save_state(db, session, result.session_state)
            messages: list[s.Message] = (
                [s.TextMessage(text="\n".join(filter(None, [notice, result.text])))]
                if result.text or notice
                else []
            )
            messages.extend(cards)
            if stream:
                stream.check()
            return _save(db, session, messages, steps)
    except pi_client.TurnCancelled:
        return s.TurnResponse(messages=[])
    except BotBusy:
        message = "忙，请稍后再试"
    except BotDraftingPriority:
        message = "正在起草画像，请稍后再试"
    except BotRateLimited as error:
        message = f"每小时 30 轮已用完，{error.retry_at.isoformat()} 恢复"
    except BotQuotaPaused as error:
        message = f"模型额度暂时用完，预计 {error.retry_at.isoformat()} 恢复"
    except pi_client.PiSessionInvalid:
        store.save_state(db, session, None)
        logger.warning("session_id=%s code=bad_request", session.id)
        message = "之前的对话上下文已失效，已经重新开始，请再说一次"
    except ModelUnavailable as error:
        logger.warning(
            "session_id=%s code=%s elapsed_ms=%d",
            session.id,
            error.code,
            (monotonic() - started) * 1000,
        )
        message = (
            str(error)
            if isinstance(error, ModelServiceError)
            else "模型服务暂时不可用，请稍后再试"
        )
    messages = [s.TextMessage(text="\n".join(filter(None, [notice, message])))]
    if stream and stream.cancelled.is_set():
        db.rollback()
        return s.TurnResponse(messages=[])
    if admitted:
        if stream:
            session.turn_count += 1
            store.append_message(db, session, "user", user_payload)
        response = _save(db, session, messages, steps)
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
    client_name: str = "内置机器人",
    commit: bool = True,
) -> s.TurnResponse:
    session = store.get_session(db, p, session_id, lock=True)
    if session.lease_until and session.lease_until > utc_now():
        return s.TurnResponse(messages=[s.TextMessage(text="忙，请稍后再试")])
    cards = [
        card
        for message in store.list_messages(db, p, session_id)
        if message.role == "assistant" and isinstance(message.payload, dict)
        for card in s.TurnResponse.model_validate(message.payload).messages
    ]
    messages: list[s.Message]
    if isinstance(action, s.ReviewAction):
        card = next(
            (c for c in cards if isinstance(c, s.ReviewCard) and action in c.actions),
            None,
        )
        if card is None:
            raise employee.RequestConflict("请重新查看审批卡片")
        if action.action == "continue_review":
            review = manager_tools.get_review(p, db, action.request_id)
            if review.revision != action.revision:
                raise employee.RequestConflict("内容已变化，请重新查看")
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
        preview = employee.preview(p, db, action.request_id)
        if preview.revision != action.revision:
            raise employee.RequestConflict("内容已变化，请重新预览")
        if not any(
            isinstance(c, (s.PreviewCard, s.StatusCard)) and action in c.actions
            for c in cards
        ):
            raise employee.RequestConflict("内容已变化，请重新预览")
        if action.action == "continue_editing":
            messages = [s.TextMessage(text="请告诉我需要修改的内容")]
        else:
            request = (
                employee.submit(
                    p, db, action.request_id, action.revision, "bot", client_name
                )
                if action.action == "confirm_submit"
                else employee.withdraw(p, db, action.request_id)
            )
            messages = [tools.status_card(s.Request.model_validate(request))]
    response = _save(db, session, messages)
    if commit:
        db.commit()
    return response
