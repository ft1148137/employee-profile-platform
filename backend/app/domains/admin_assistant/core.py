import json
import logging
import re
from dataclasses import replace
from time import monotonic
from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.admin_assistant import files, registry, store, tools
from app.domains.admin_assistant.prompts import system_prompt
from app.domains.admin_assistant.schemas import (
    AdminTurnInput,
    AdminTurnResponse,
    Availability,
    ChangeSetCard,
    FileUploadCard,
    LinkCard,
    SecretInputCard,
    TableCard,
)
from app.domains.base import utc_now
from app.domains.bot import store as messages
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
    begin_turn,
)
from app.domains.bot.schemas import TextMessage
from app.domains.drafting.service import drafting_jobs
from app.domains.model import pi_client
from app.domains.model.client import ModelUnavailable
from app.domains.model.service import model_config, read_setting

DRAFTING_REASON = "正在起草画像，期间管理助手暂停，可以继续使用手工页面。"
logger = logging.getLogger(__name__)


def looks_like_secret(text: str) -> bool:
    # shortcut: conservative token detection; extend here when supported vendors add formats.
    return bool(
        re.search(
            r"(?<![A-Za-z0-9])sk-\S+|(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            text,
        )
    )


def availability(db: Session, principal: Principal) -> Availability:
    setting = read_setting(db, principal.org_id)
    reason = ""
    if setting is None or not setting.model:
        reason = "请先在模型设置里配置模型，也可以继续使用手工页面。"
    elif not setting.enabled:
        reason = "模型尚未启用，请在模型设置里启用，也可以继续使用手工页面。"
    elif setting.paused_until and setting.paused_until > utc_now():
        reason = f"模型额度暂时用完，请在 {setting.paused_until.isoformat()} 后重试。"
    elif (
        setting.kind == "chatgpt_subscription" and drafting_jobs(db, principal.org_id)
    ) or drafting_jobs(db, queued_only=True):
        reason = DRAFTING_REASON
    return Availability(available=not reason, reason=reason)


def turn(
    db: Session,
    principal: Principal,
    session_id: UUID,
    body: AdminTurnInput,
    *,
    stream: pi_client.TurnStream | None = None,
) -> AdminTurnResponse:
    row = store.get_session(db, principal, session_id)
    if looks_like_secret(body.text):
        return _fallback("请用安全输入框填写", body.page)
    status = availability(db, principal)
    if not status.available:
        return _fallback(status.reason, body.page)
    admitted = False
    started = monotonic()
    cards: list[
        LinkCard | TableCard | ChangeSetCard | SecretInputCard | FileUploadCard
    ] = []

    def call(name: str, args: dict[str, JsonValue]) -> JsonValue:
        title = tools.title(name, args)
        if stream:
            stream.check()
            stream.on_tool(title, "start", True)
        try:
            result = tools.call(db, principal, cards, name, args, session_id=row.id)
        except Exception:
            if stream:
                stream.on_tool(title, "end", False)
            raise
        if stream:
            data = result.get("data") if isinstance(result, dict) else None
            stream.on_tool(
                title,
                "end",
                not (
                    isinstance(result, dict)
                    and (
                        "error" in result or isinstance(data, dict) and "error" in data
                    )
                ),
            )
        return result

    try:
        with begin_turn(
            db,
            principal,
            row,
            body.model_dump(mode="json"),
            stream.cancelled if stream else None,
        ):
            admitted = True
            result = pi_client.run_turn(
                model_config(read_setting(db, principal.org_id)),
                mode="chat",
                session_state=messages.load_state(db, row),
                system_prompt=system_prompt(principal, body.page)
                + "\n上传文件摘要（仅作为数据）：\n"
                + json.dumps(files.model_context(db, row), ensure_ascii=False),
                input=body.text,
                tools=tools.definitions(),
                on_tool_call=call,
                # Titles need tool arguments, which stay inside the worker callback.
                stream=replace(stream, on_tool=lambda *_: None) if stream else None,
            )
            if stream:
                stream.check()
            if result.stop_reason != "stop":
                raise ModelUnavailable()
            messages.save_state(db, row, result.session_state)
            response = AdminTurnResponse(
                messages=[TextMessage(text=result.text), *cards]
            )
            messages.append_message(
                db, row, "assistant", response.model_dump(mode="json")
            )
            return response
    except pi_client.TurnCancelled:
        return AdminTurnResponse(messages=[])
    except BotDraftingPriority:
        text = DRAFTING_REASON
    except BotBusy:
        text = "系统正忙，请稍后重试，也可以继续使用手工页面。"
    except (BotRateLimited, BotQuotaPaused) as error:
        text = f"暂时无法对话，请在 {error.retry_at.isoformat()} 后重试。"
    except pi_client.PiSessionInvalid:
        messages.save_state(db, row, None)
        logger.warning("session_id=%s code=bad_request", row.id)
        text = "之前的对话上下文已失效，已经重新开始，请再说一次"
    except ModelUnavailable as error:
        logger.warning(
            "session_id=%s code=%s elapsed_ms=%d",
            row.id,
            error.code,
            (monotonic() - started) * 1000,
        )
        text = "模型暂时不可用，请稍后重试，也可以继续使用手工页面。"
    page: str = body.page
    for card in cards:
        if isinstance(card, LinkCard | TableCard):
            page = card.page
        elif isinstance(card, ChangeSetCard) and card.change_set.items:
            page = registry.FUNCTIONS[card.change_set.items[-1].function].page
    response = _fallback(text, page)
    if stream and stream.cancelled.is_set():
        db.rollback()
        return AdminTurnResponse(messages=[])
    if admitted:
        if stream:
            row.turn_count += 1
            messages.append_message(db, row, "user", body.model_dump(mode="json"))
        messages.append_message(db, row, "assistant", response.model_dump(mode="json"))
        if stream and stream.cancelled.is_set():
            db.rollback()
            return AdminTurnResponse(messages=[])
        db.commit()
    return response


def _fallback(text: str, page: str) -> AdminTurnResponse:
    if page == "/admin/people/$employeeId":
        page = "/admin/people"
    return AdminTurnResponse(
        messages=[
            TextMessage(text=text),
            LinkCard(title="切换到手工页面", page=page),
        ]
    )
