from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.admin_assistant.models import AdminAssistantSession
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityProposal, ProposalStatus
from app.domains.change_request.handover import hand_over
from app.domains.change_request.models import ProfileChangeRequest, RequestStatus
from app.domains.drafting.service import read_progress
from app.domains.drafting.settings import read_settings
from app.domains.im.models import ImNotification, ImSetting
from app.domains.model.models import ModelSetting
from app.domains.review.appeal import list_disputes
from app.domains.setup.wizard import read_setup
from app.domains.source.identities import list_identities
from app.domains.source.models import SourceConnection, SyncRun

REVIEW_BACKLOG_DAYS = 7


class StatusCard(BaseModel):
    key: Literal["feishu", "model", "sync", "drafting"]
    level: Literal["ok", "warn", "error", "off"]
    title: str
    summary: str
    fix_steps: list[str]
    page: str
    assistant_prompt: str


TodoKind = Literal[
    "appeals",
    "initial_overdue",
    "review_backlog",
    "unmatched_identities",
    "sync_failed",
    "setup_step",
    "setup_skipped",
    "capability_proposals",
]


class SetupTodoStep(BaseModel):
    title: str
    page: str
    assistant_prompt: str


class AdminTodo(BaseModel):
    kind: TodoKind
    count: int = Field(ge=1)
    title: str
    oldest_at: datetime | None
    page: str
    assistant_prompt: str
    steps: list[SetupTodoStep] = Field(default_factory=list)


class AdminOverviewPublic(BaseModel):
    status_cards: list[StatusCard]
    todos: list[AdminTodo]


def build_overview(session: Session, principal: Principal) -> AdminOverviewPublic:
    org_id, now = principal.org_id, utc_now()
    feishu = StatusCard(
        level="ok",
        fix_steps=[],
        key="feishu",
        title="飞书",
        summary="已开启",
        page="/admin/data-ai?tab=im",
        assistant_prompt="帮我检查飞书连接",
    )
    im = session.get(ImSetting, (org_id, "feishu"))
    failed_notifications = session.exec(
        select(func.count())
        .select_from(ImNotification)
        .where(
            ImNotification.org_id == org_id,
            ImNotification.platform == "feishu",
            ImNotification.status == "failed",
            ImNotification.created_at >= now - timedelta(hours=24),
        )
    ).one()
    if im is None or not im.enabled:
        feishu.level, feishu.summary = "off", "尚未开启"
    elif failed_notifications:
        feishu.level, feishu.summary = (
            "warn",
            f"最近 24 小时有 {failed_notifications} 条通知发送失败",
        )
        feishu.fix_steps = ["打开飞书设置，检查连接和机器人权限，再测试连接。"]

    model = StatusCard(
        level="ok",
        fix_steps=[],
        key="model",
        title="AI 模型",
        summary="已启用",
        page="/admin/data-ai?tab=model",
        assistant_prompt="帮我检查模型和额度",
    )
    setting = session.get(ModelSetting, org_id)
    # shortcut: 模型测试结果没有保存，模型配错要等起草失败才在「自动起草」卡显示；需要时在测试接口记审计后再接上
    if setting is None or not setting.enabled:
        model.level, model.summary = "error", "尚未启用模型，起草和机器人暂不可用"
        model.fix_steps = ["打开模型设置，填写并启用模型，再测试连接。"]
    elif setting.paused_until and setting.paused_until > now:
        model.level = "warn"
        model.summary = f"已暂停：{setting.pause_reason or '额度暂不可用'}；恢复时间：{setting.paused_until.isoformat()}"
        model.fix_steps = ["检查模型额度，或等到恢复时间后再试。"]

    sync = StatusCard(
        level="ok",
        fix_steps=[],
        key="sync",
        title="数据同步",
        summary="尚未开始同步",
        page="/admin/data-ai?tab=sources",
        assistant_prompt="帮我检查数据同步",
    )
    connections = session.exec(
        select(SourceConnection.id).where(SourceConnection.org_id == org_id)
    ).all()
    latest_runs = session.exec(
        select(SyncRun)
        .where(SyncRun.org_id == org_id)
        .distinct(col(SyncRun.connection_id))
        .order_by(
            col(SyncRun.connection_id),
            col(SyncRun.created_at).desc(),
            col(SyncRun.id).desc(),
        )
    ).all()
    failed_runs = [run for run in latest_runs if run.status == "failed"]
    latest = max(latest_runs, key=lambda run: (run.created_at, run.id), default=None)
    if not connections:
        sync.level, sync.summary = "off", "还没有数据来源"
    elif latest:
        if latest.status == "failed":
            sync.level, sync.summary = "error", "最近一次同步失败"
            sync.fix_steps = ["打开数据来源，检查连接或导入文件，再重试同步。"]
        elif latest.status in ("queued", "running"):
            sync.summary = "正在同步"
        elif latest.finished_at:
            sync.summary = f"同步已完成：{latest.finished_at.isoformat()}"

    progress = read_progress(session, org_id)
    drafting = StatusCard(
        level="ok",
        fix_steps=[],
        key="drafting",
        title="自动起草",
        summary="起草运行正常",
        page="/admin/data-ai?tab=model",
        assistant_prompt="帮我检查自动起草",
    )
    if not read_settings(session, org_id).enabled:
        drafting.level, drafting.summary = "off", "自动起草尚未开启"
        drafting.assistant_prompt = "帮我打开自动起草"
    elif progress.mode == "none":
        drafting.level, drafting.summary = "off", "自动起草已打开，模型尚未启用"
        drafting.assistant_prompt = "帮我检查模型和额度"
    elif progress.paused_until and progress.paused_until > now:
        drafting.level, drafting.summary = (
            "warn",
            f"起草已暂停，恢复时间：{progress.paused_until.isoformat()}",
        )
        drafting.fix_steps = ["检查模型设置和额度，恢复后再起草。"]
    elif progress.failed:
        drafting.level, drafting.summary = (
            "error",
            f"有 {len(progress.failed)} 人起草失败",
        )
        drafting.fix_steps = ["打开起草进度查看失败情况，再重试失败员工。"]

    todos: list[AdminTodo] = []

    def add(
        kind: TodoKind,
        count: int,
        title: str,
        page: str,
        prompt: str,
        oldest_at: datetime | None = None,
    ) -> None:
        if count:
            todos.append(
                AdminTodo(
                    kind=kind,
                    count=count,
                    title=title,
                    page=page,
                    assistant_prompt=prompt,
                    oldest_at=oldest_at,
                )
            )

    proposal_count = session.exec(
        select(func.count())
        .select_from(CapabilityProposal)
        .where(
            CapabilityProposal.org_id == org_id,
            CapabilityProposal.status == ProposalStatus.OPEN,
        )
    ).one()
    add(
        "capability_proposals",
        proposal_count,
        f"{proposal_count} 个新增能力的提议",
        "/admin/rules?tab=dictionary#dictionary-editor",
        "帮我查看新增能力的提议",
    )
    disputes = list_disputes(principal, session)
    add(
        "appeals",
        len(disputes),
        f"{len(disputes)} 个争议待处理",
        "/employees?filter=appeals",
        "帮我处理争议",
        min((a.created_at for a in disputes), default=None),
    )
    overdue = hand_over(session, principal, confirm=False)
    add(
        "initial_overdue",
        overdue,
        f"{overdue} 人的初始画像超期未过目",
        drafting.page,
        "帮我处理超期未过目的初始画像",
    )
    backlog, oldest = session.exec(
        select(func.count(), func.min(ProfileChangeRequest.submitted_at)).where(
            ProfileChangeRequest.org_id == org_id,
            ProfileChangeRequest.status == RequestStatus.PENDING_REVIEW,
            col(ProfileChangeRequest.submitted_at)
            < now - timedelta(days=REVIEW_BACKLOG_DAYS),
        )
    ).one()
    add(
        "review_backlog",
        backlog,
        f"{backlog} 个请求等待审批超过 {REVIEW_BACKLOG_DAYS} 天",
        "/admin/rules?tab=approval",
        "帮我查看审批积压",
        oldest,
    )
    unmatched = list_identities(
        session, org_id, status="unlinked", source=None, q=None, skip=0, limit=1
    ).count
    add(
        "unmatched_identities",
        unmatched,
        f"{unmatched} 个工作账号还没有匹配员工",
        "/employees?filter=identities",
        "帮我匹配工作账号",
    )
    add(
        "sync_failed",
        len(failed_runs),
        f"{len(failed_runs)} 个数据来源同步失败",
        sync.page,
        "帮我检查同步失败",
        min((r.finished_at or r.created_at for r in failed_runs), default=None),
    )
    setup = read_setup(session, org_id)
    steps = [s for s in setup.steps if s.status == "pending"]
    titles = "、".join(s.title for s in steps[:2]) + ("…" if len(steps) > 2 else "")
    setup_session = session.exec(
        select(AdminAssistantSession.id).where(
            AdminAssistantSession.org_id == org_id,
            AdminAssistantSession.user_id == principal.user_id,
            AdminAssistantSession.purpose == "setup",
        )
    ).one_or_none()
    add(
        "setup_step",
        len(steps),
        f"还有 {len(steps)} 步首次设置没完成：{titles}",
        f"/admin/assistant?session={setup_session}"
        if setup_session
        else "/admin/assistant",
        "帮我完成首次设置",
    )
    pages = {
        "sources": "/admin/data-ai?tab=sources",
        "identities": "/employees?filter=identities",
        "model": "/admin/data-ai?tab=model",
        "approval_policy": "/admin/rules?tab=approval",
    }
    skipped = [
        SetupTodoStep(
            title=s.title,
            page=pages[s.key],
            assistant_prompt=f"帮我继续暂缓的设置：{s.title}",
        )
        for s in setup.steps
        if s.status == "skipped"
    ]
    if skipped:
        titles = "、".join(s.title for s in skipped)
        todos.append(
            AdminTodo(
                kind="setup_skipped",
                count=len(skipped),
                title=f"有 {len(skipped)} 步首次设置暂缓了：{titles}",
                oldest_at=None,
                page="/admin/assistant",
                assistant_prompt=f"帮我继续暂缓的设置：{titles}",
                steps=skipped,
            )
        )
    return AdminOverviewPublic(
        status_cards=[feishu, model, sync, drafting], todos=todos
    )
