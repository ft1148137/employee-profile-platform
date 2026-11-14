import json
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.domains.admin_assistant import prompts as admin_prompts
from app.domains.bot import prompts as bot_prompts
from app.domains.bot import store
from app.domains.change_request import employee
from app.domains.change_request.schemas import ItemInput
from app.domains.review import service as reviews
from app.domains.review.schemas import DecisionInput
from scripts.eval_admin_assistant import tasks
from tests.api.routes.test_employee_requests import payload, principal_for
from tests.api.routes.test_reviews import manager

ROOT = Path(__file__).resolve().parents[3]
PROMPTS = (
    ROOT / "backend/app/domains/bot/prompts.py",
    ROOT / "backend/app/domains/admin_assistant/prompts.py",
)
NON_PAGE_QUOTES = {
    "1. ": "Plain text list numbering example.",
    "2. ": "Plain text list numbering example.",
    "这一步不能跳过": "Assistant reply for a mandatory setup step.",
    "按部门分配能力": "Example of a user's instruction.",
}


def missing_page_names(source: str) -> set[str]:
    pages = [p.read_text() for p in (ROOT / "frontend/src").rglob("*.tsx")]
    return {
        name
        for name in re.findall("「([^」]+)」", source)
        if name not in NON_PAGE_QUOTES and not any(name in page for page in pages)
    }


def test_prompt_page_names_match_frontend() -> None:
    for path in PROMPTS:
        assert not (missing := missing_page_names(path.read_text())), (
            f"{path.name}: 页面不存在的名称 {sorted(missing)}"
        )


def test_page_name_guard_rejects_invented_entry() -> None:
    assert missing_page_names("请到画像页的「请求记录」点击「申诉」。") == {"请求记录"}


def test_employee_navigation_and_privacy(db: Session, demo: tuple) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    prompt = bot_prompts.system_prompt(db, p, "feishu")
    assert "「已处理请求」" in prompt and "「申诉」" in prompt
    assert "「我的团队」" in prompt and "「初始画像」列" in prompt
    assert (
        "数据保存在本企业自己部署的服务器上；存放位置、备份和保留期限请问企业管理员"
        in prompt
    )
    assert "不猜" in prompt


def test_department_hierarchy_guidance(db: Session, demo: tuple) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    prompt = admin_prompts.system_prompt(p, "/admin/assistant")
    assert "部门路径" in prompt and "上级部门" in prompt
    assert "已建部门改上级目前没有页面入口" in prompt
    assert "重新上传名单" in prompt


@pytest.mark.parametrize(
    "decisions, expected",
    [
        (["approved", "changes_requested"], "1 项批准、1 项退回"),
        (["changes_requested", "rejected"], "1 项退回、1 项拒绝"),
        (["approved", "rejected"], "1 项批准、1 项拒绝"),
        (["approved", "changes_requested", "changes_requested"], "1 项批准、2 项退回"),
        (["approved", "approved", "rejected"], "2 项批准、1 项拒绝"),
        (["approved", "changes_requested", "rejected"], "1 项批准、1 项退回、1 项拒绝"),
        (["approved", "approved"], "已批准"),
        (["rejected", "rejected"], "已拒绝"),
        (["changes_requested", "changes_requested"], "已退回"),
    ],
)
def test_request_states_summarize_item_decisions(
    db: Session, demo: tuple, decisions: list[str], expected: str
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    session = store.create_session(db, p, "feishu")
    request = employee.save_draft(
        p,
        db,
        None,
        [
            ItemInput(**payload(key, level="routine", owner_statement="本人合成依据"))
            for key in ("automated_testing", "observability", "technical_docs")[
                : len(decisions)
            ]
        ],
        "bot",
    )
    request = employee.submit(p, db, request.id, request.revision, "bot")
    reviews.decide(
        manager(db, demo),
        db,
        request.id,
        request.revision,
        [
            DecisionInput(item_id=item.id, decision=decision, reason="合成审批原因")
            for item, decision in zip(request.items, decisions, strict=True)
        ],
    )
    db.commit()
    session.request_ids = [request.id]
    result = json.loads(bot_prompts.request_states(db, p, session))
    assert len(result) == 1 and result[0]["状态"] == expected
    for other in (
        replace(p, org_id=uuid4()),
        replace(p, employee_id=demo[1]["E01"].id),
    ):
        assert json.loads(bot_prompts.request_states(db, other, session)) == []


def test_navigation_eval_tasks_parse() -> None:
    loaded = {task.id: task for task in tasks()}
    for name in (
        "m47-appeal-entry",
        "m47-team-initial-profile",
        "m47-data-location",
        "m47-department-hierarchy",
    ):
        assert name in loaded
