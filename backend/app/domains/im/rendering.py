from pydantic import JsonValue

from app.domains.bot import schemas as s

LABELS = {
    "confirm_submit": "确认提交",
    "continue_editing": "继续修改",
    "withdraw": "撤回",
    "answer_question": "回答",
    "draft": "草稿",
    "pending_owner": "待本人过目",
    "pending_review": "待经理审批",
    "withdrawn": "已撤回",
    "approved": "已批准",
    "rejected": "已拒绝",
    "returned": "已退回",
    "changes_requested": "退回修改",
    "partially_approved": "部分批准",
    "tentative": "暂定",
    "confirmed": "已确认",
    "disputed": "存在争议",
    "unknown": "未知",
    "add": "新增",
    "modify": "修改",
    "retract": "撤销",
    "attach_evidence": "补充依据",
}


def claim_text(claim: s.Claim | None) -> str:
    if claim is None:
        return "暂无判断"
    return f"{claim.statement}；等级：{claim.level or '未给等级'}；状态：{LABELS.get(claim.assessment_status, claim.assessment_status)}"


def item_text(item: s.ChangeItem) -> str:
    lines = [
        f"{item.capability.name}：{LABELS.get(item.op, item.op)}",
        claim_text(item.after),
    ]
    for label, value in (
        ("解释", item.explanation),
        ("不确定之处", item.uncertainty),
        ("本人陈述", item.owner_statement),
        ("异议", item.objection),
    ):
        if value:
            lines.append(f"{label}：{value}")
    return "\n".join(lines)


def render(message: s.Message) -> tuple[str, list[dict[str, JsonValue]]]:
    if isinstance(message, s.TextMessage):
        return message.text, []
    if isinstance(message, s.QuestionCard):
        return (
            f"{'待确认问题' if message.status == 'open' else '问题已处理'}：{message.question}\n原因：{message.reason}",
            [
                {"action": a, "question_id": str(message.id)}
                for a in message.actions[:3]
            ],
        )
    if isinstance(message, s.PreviewCard):
        text = "改动预览\n" + "\n\n".join(
            f"修改前：{claim_text(item.before)}\n修改后：{item_text(item.after)}"
            + (f"\n提醒：{item.warning}" if item.warning else "")
            for item in message.items
        )
        if len(text) > 12000:
            return "预览内容较长，请在网页查看完整内容并确认提交。", []
    else:
        text = (
            f"请求状态：{LABELS.get(message.status, message.status)}\n"
            + "\n\n".join(item_text(i) for i in message.items)
        )
    return text, [a.model_dump(mode="json") for a in message.actions[:3]]
