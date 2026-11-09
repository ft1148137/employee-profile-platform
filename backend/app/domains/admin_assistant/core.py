import re
from uuid import UUID

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
from app.domains.model.client import ModelServiceError, ModelUnavailable
from app.domains.model.service import model_config, read_setting

DRAFTING_REASON = "正在起草画像，期间管理助手暂停，可以继续使用手工页面。"


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
    db: Session, principal: Principal, session_id: UUID, body: AdminTurnInput
) -> AdminTurnResponse:
    row = store.get_session(db, principal, session_id)
    if looks_like_secret(body.text):
        return _fallback("请用安全输入框填写", body.page)
    status = availability(db, principal)
    if not status.available:
        return _fallback(status.reason, body.page)
    history = messages.load_history(db, row, transform=files.model_message)
    admitted = False
    cards: list[
        LinkCard | TableCard | ChangeSetCard | SecretInputCard | FileUploadCard
    ] = []
    try:
        with begin_turn(db, principal, row, body.model_dump(mode="json")):
            admitted = True
            result = pi_client.run_turn(
                model_config(read_setting(db, principal.org_id)),
                mode="chat",
                history=history,
                system_prompt=system_prompt(principal, body.page),
                input=body.text,
                tools=tools.definitions(),
                on_tool_call=lambda name, args: tools.call(
                    db, principal, cards, name, args, session_id=row.id
                ),
            )
            if result.stop_reason != "stop":
                raise ModelUnavailable()
            response = AdminTurnResponse(
                messages=[TextMessage(text=result.text), *cards]
            )
            messages.append_message(
                db, row, "assistant", response.model_dump(mode="json")
            )
            return response
    except BotDraftingPriority:
        text = DRAFTING_REASON
    except BotBusy:
        text = "系统正忙，请稍后重试，也可以继续使用手工页面。"
    except (BotRateLimited, BotQuotaPaused) as error:
        text = f"暂时无法对话，请在 {error.retry_at.isoformat()} 后重试。"
    except ModelServiceError, ModelUnavailable:
        text = "模型暂时不可用，请稍后重试，也可以继续使用手工页面。"
    page: str = body.page
    for card in cards:
        if isinstance(card, LinkCard | TableCard):
            page = card.page
        elif isinstance(card, ChangeSetCard) and card.change_set.items:
            page = registry.FUNCTIONS[card.change_set.items[-1].function].page
    response = _fallback(text, page)
    if admitted:
        messages.append_message(db, row, "assistant", response.model_dump(mode="json"))
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
