import pytest
from sqlmodel import Session

from app.domains.admin_assistant import prompts as admin_prompts
from app.domains.bot import prompts as bot_prompts
from scripts.eval_admin_assistant import tasks
from tests.admin_assistant.test_registry import principal


@pytest.mark.parametrize("entry", ["admin", "web", "feishu"])
def test_appeal_guidance(db: Session, demo: tuple, entry: str) -> None:
    p = principal(db, demo, "admin" if entry == "admin" else "employee")
    prompt = (
        admin_prompts.system_prompt(p, "/admin/assistant")
        if entry == "admin"
        else bot_prompts.system_prompt(db, p, entry)
    )
    for text in (
        "自己的画像页",
        "「已处理请求」",
        "「申诉」",
        "被经理拒绝的改动",
        "只有被拒绝的改动",
        "没有被拒项时页面上看不到这个按钮，这是正常的",
        "对已批准或经理直接修改的判断有异议",
        "提交修改请求由经理审批",
        "不能代为申诉",
    ):
        assert text in prompt, f"{entry}: 缺少申诉说明 {text}"


def test_admin_appeal_eval_task_parses() -> None:
    task = next(task for task in tasks() if task.id == "m47-admin-appeal-entry")
    assert task.actor == "admin"
    assert task.entry == "admin_assistant"
    assert task.steps[0]["text"] == "我想申诉，在哪儿操作？"
    assert task.expect == {"unchanged": True}
