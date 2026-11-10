import logging
from uuid import UUID

from sqlmodel import Session

from app.authz.principal import Principal
from app.domains import manager_tools
from app.domains.base import utc_now
from app.domains.bot import schemas as s
from app.domains.bot import store, tools
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
from app.domains.model.service import model_config, read_setting

logger = logging.getLogger(__name__)


def _save(
    db: Session, session: BotSession, messages: list[s.Message]
) -> s.TurnResponse:
    response = s.TurnResponse(messages=messages)
    store.append_message(db, session, "assistant", response.model_dump(mode="json"))
    return response


def turn(db: Session, p: Principal, session_id: UUID, text: str) -> s.TurnResponse:
    session = store.get_session(db, p, session_id)
    setting = read_setting(db, p.org_id)
    admitted = False
    try:
        with begin_turn(db, p, session, {"text": text}):
            admitted = True
            if setting is None or not setting.enabled or not setting.model:
                return _save(
                    db,
                    session,
                    [
                        s.TextMessage(
                            text="机器人需要先配置模型，你也可以在画像页直接修改"
                        )
                    ],
                )
            cards: list[s.Message] = []
            result = pi_client.run_turn(
                model_config(setting),
                mode="chat",
                session_state=store.load_state(db, session),
                system_prompt=system_prompt(db, p, session.channel),
                input=tools.redact_text(text),
                tools=tools.definitions(),
                on_tool_call=lambda name, args: tools.call(
                    db, p, session, cards, name, args
                ),
            )
            # Only completed model turns may leave drafts or replies behind.
            if result.stop_reason != "stop":
                raise ModelUnavailable()
            store.save_state(db, session, result.session_state)
            messages: list[s.Message] = (
                [s.TextMessage(text=result.text)] if result.text else []
            )
            messages.extend(cards)
            return _save(db, session, messages)
    except BotBusy:
        message = "忙，请稍后再试"
    except BotDraftingPriority:
        message = "正在起草画像，请稍后再试"
    except BotRateLimited as error:
        message = f"每小时 30 轮已用完，{error.retry_at.isoformat()} 恢复"
    except BotQuotaPaused as error:
        message = f"模型额度暂时用完，预计 {error.retry_at.isoformat()} 恢复"
    except ModelServiceError as error:
        message = str(error)
    except pi_client.PiSessionInvalid:
        store.save_state(db, session, None)
        logger.warning("session_id=%s code=bad_request", session.id)
        message = "之前的对话上下文已失效，已经重新开始，请再说一次"
    except ModelUnavailable:
        message = "模型服务暂时不可用，请稍后再试"
    messages = [s.TextMessage(text=message)]
    if admitted:
        response = _save(db, session, messages)
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
        if session.channel == "web" or card is None:
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
                via=ReviewVia.IM,
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
