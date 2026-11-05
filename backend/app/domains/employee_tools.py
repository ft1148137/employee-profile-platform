from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domains.change_request import employee
from app.domains.change_request.schemas import DraftInput, ToolInput
from app.domains.evidence.models import EvidenceKind
from app.domains.inbox import service as inbox
from app.domains.inbox.schemas import AnswerQuestionInput, QuestionsInput
from app.domains.review.appeal import appeal_decision
from app.domains.review.appeal_schemas import AppealInput


class EvidenceInput(ToolInput):
    kind: EvidenceKind | None = None
    include_inactive: bool = False
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class RequestsInput(ToolInput):
    status: Literal["open", "all"] = "open"


class RequestInput(ToolInput):
    request_id: UUID


class DraftRequestInput(DraftInput):
    request_id: UUID | None = None


class SubmitRequestInput(RequestInput):
    revision: int = Field(ge=1)


@dataclass(frozen=True)
class EmployeeTool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., BaseModel]
    writes: bool = False
    bot_allowed: bool = True


# Adapters inject Principal, Session and trusted via/client_name; never model input.
EMPLOYEE_TOOLS = (
    EmployeeTool(
        "appeal_decision",
        "申诉本人被拒绝的改动",
        AppealInput,
        appeal_decision,
        True,
        False,
    ),
    EmployeeTool(
        "get_pending_questions",
        "查看我的待确认问题",
        QuestionsInput,
        inbox.get_pending_questions,
    ),
    EmployeeTool(
        "answer_question",
        "确认回答并存为受限自述证据，不修改画像",
        AnswerQuestionInput,
        inbox.answer_question,
        True,
    ),
    EmployeeTool("get_my_profile", "查看我的正式画像", ToolInput, employee.my_profile),
    EmployeeTool(
        "list_my_evidence", "查看我的证据", EvidenceInput, employee.my_evidence
    ),
    EmployeeTool(
        "get_my_requests", "查看我的画像请求", RequestsInput, employee.my_requests
    ),
    EmployeeTool(
        "get_capability_dictionary",
        "查看当前能力词典和等级",
        ToolInput,
        employee.capability_dictionary,
    ),
    EmployeeTool(
        "draft_update_request",
        "保存我的画像草稿",
        DraftRequestInput,
        employee.save_draft,
        True,
    ),
    EmployeeTool(
        "preview_request", "预览改动并取得修订号", RequestInput, employee.preview
    ),
    EmployeeTool(
        "submit_request",
        "按预览修订号提交我的请求",
        SubmitRequestInput,
        employee.submit,
        True,
        False,
    ),
    EmployeeTool(
        "withdraw_request", "撤回我的请求", RequestInput, employee.withdraw, True, False
    ),
)
