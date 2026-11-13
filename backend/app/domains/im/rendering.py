from pydantic import JsonValue

from app.core.config import settings
from app.domains.bot import schemas as s

LABELS = {
    "confirm_profile_edit": "确认修改",
    "continue_profile_edit": "继续修改",
    "confirm_review": "确认决定",
    "continue_review": "继续修改",
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
    return f"{claim.statement}；等级：{claim.level_name or '未定级'}；状态：{LABELS.get(claim.assessment_status, claim.assessment_status)}"


def item_text(item: s.ChangeItem) -> str:
    lines = [
        f"{item.capability.name}：{LABELS.get(item.op, item.op)}",
        f"{item.after.statement}；等级：{item.after.level_name or '未定级'}"
        if item.after
        else "撤回此判断",
    ]
    return "\n".join(lines)


def render(message: s.Message) -> tuple[str, list[dict[str, JsonValue]]]:
    if isinstance(message, s.TextMessage):
        return message.text, []
    if isinstance(message, s.ProfileEditCard):
        text = (
            f"直接修改画像：{message.employee_name}\n"
            + "\n\n".join(
                f"{item.capability_name}\n修改前：{claim_text(s.Claim.model_validate(item.before)) if item.before else '暂无判断'}"
                f"\n修改后：{claim_text(s.Claim.model_validate(item.after))}"
                for item in message.items
            )
            + "\n确认后立即发布并通知员工。"
        )
        if len(text) > 12000:
            return "修改内容较长，请缩小改动后重新生成确认卡。", []
        return text, [a.model_dump(mode="json") for a in message.actions]
    if isinstance(message, s.ReviewCard):
        decisions = {d.item_id: d for d in message.decisions}
        text = (
            f"审批决定复述（修订 {message.revision}）\n"
            + "\n\n".join(
                f"第 {index + 1} 项：{item.after.capability.name}\n"
                f"修改前：{claim_text(item.before)}\n修改后：{claim_text(item.after.after)}\n"
                f"依据标题：{'、'.join(e.title for e in item.evidence) or '暂无'}\n"
                + ("仅 Jira 来源（最多暂定）\n" if item.jira_only else "")
                + f"决定：{LABELS[decisions[item.after.id].decision]}\n原因：{decisions[item.after.id].reason or '未填写'}"
                for index, item in enumerate(message.items)
            )
            + f"\n详情在网页审批页查看：{settings.FRONTEND_HOST.rstrip('/')}/reviews/{message.id}"
        )
        if len(text) > 12000:
            return "审批内容较长，请在网页审批页查看并决定。", []
        return text, [a.model_dump(mode="json") for a in message.actions]
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
            f"{item.after.capability.name}\n修改前：{item.before.statement if item.before else '暂无判断'}；等级：{(item.before.level_name or '未定级') if item.before else '未定级'}\n修改后：{item_text(item.after)}"
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
