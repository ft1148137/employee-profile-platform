"""Add steps by registering a check here and a matching frontend component.

Use spaced order values (M2: 50/60/70/90, M4: 80). No migration is needed;
new required steps automatically make previously finished setups incomplete.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.domains.capability.models import CapabilitySchemaVersion, SchemaStatus
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    OrgMembership,
    RosterImport,
)
from app.domains.source.models import SyncRun


@dataclass(frozen=True)
class SetupStep:
    key: str
    title: str
    description: str
    order: int
    required: bool
    skippable: bool
    check: Callable[[Session, UUID], list[str]]
    implicit: bool = False


def check_org_profile(_session: Session, _org_id: UUID) -> list[str]:
    return []


def check_roster(session: Session, org_id: UUID) -> list[str]:
    employee = session.exec(
        select(Employee.id).where(
            Employee.org_id == org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).first()
    imported = session.exec(
        select(RosterImport.id).where(
            RosterImport.org_id == org_id, RosterImport.status == "applied"
        )
    ).first()
    return ([] if employee else ["还没有在职员工"]) + (
        [] if imported else ["还没有成功导入员工名单"]
    )


def check_capabilities(session: Session, org_id: UUID) -> list[str]:
    schema = session.exec(
        select(CapabilitySchemaVersion.id).where(
            CapabilitySchemaVersion.org_id == org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
        )
    ).first()
    return [] if schema else ["还没有选择能力词典"]


def check_accounts(session: Session, org_id: UUID) -> list[str]:
    eligible = (
        Employee.org_id == org_id,
        Employee.employment_status == EmploymentStatus.ACTIVE,
        col(Employee.employee_code).is_not(None),
        func.trim(Employee.employee_code) != "",
    )
    if session.exec(select(Employee.id).where(*eligible)).first() is None:
        return ["还没有有工号的在职员工"]
    missing = session.exec(
        select(func.count())
        .select_from(Employee)
        .where(
            *eligible,
            ~select(OrgMembership.id)
            .where(
                OrgMembership.org_id == org_id,
                OrgMembership.employee_id == Employee.id,
            )
            .exists(),
        )
    ).one()
    return [f"还有 {missing} 名在职员工没有账号"] if missing else []


def check_sources(session: Session, org_id: UUID) -> list[str]:
    succeeded = session.exec(
        select(SyncRun.id).where(
            SyncRun.org_id == org_id, SyncRun.status == "succeeded"
        )
    ).first()
    return [] if succeeded else ["还没有成功同步或导入过工作记录"]


def check_model(session: Session, org_id: UUID) -> list[str]:
    from app.domains.model.service import read_setting

    setting = read_setting(session, org_id)
    return [] if setting and setting.enabled else ["还没有启用模型，请先配置并启用模型"]


def check_initial_profiles(session: Session, org_id: UUID) -> list[str]:
    from app.domains.change_request.models import ProfileChangeRequest, RequestOrigin
    from app.domains.drafting.service import active_employees
    from app.domains.drafting.settings import drafting_scope
    from app.domains.profile.models import InferenceStatus, ProfileProgress

    excluded = drafting_scope(session, org_id).excluded_employee_ids
    eligible = {e.id for e in active_employees(session, org_id)} - excluded
    requested = set(
        session.exec(
            select(ProfileChangeRequest.subject_employee_id).where(
                ProfileChangeRequest.org_id == org_id,
                ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
            )
        )
    )
    missing = len(eligible - requested)
    running = session.exec(
        select(ProfileProgress.employee_id).where(
            ProfileProgress.org_id == org_id,
            col(ProfileProgress.employee_id).in_(eligible),
            col(ProfileProgress.inference_status).in_(
                [InferenceStatus.PENDING, InferenceStatus.RUNNING]
            ),
        )
    ).first()
    return ([f"还有 {missing} 名员工没有初始画像"] if missing else []) + (
        ["初始画像还在生成"] if running else []
    )


STEPS = [
    SetupStep(
        "org_profile",
        "企业信息与管理员账号",
        "确认企业名称和管理员账号",
        10,
        True,
        False,
        check_org_profile,
        implicit=True,
    ),
    SetupStep(
        "roster",
        "导入员工名单",
        "导入名单并确认在职员工",
        20,
        True,
        False,
        check_roster,
    ),
    SetupStep(
        "capabilities",
        "选择能力词典",
        "为企业启用能力词典",
        30,
        True,
        False,
        check_capabilities,
    ),
    SetupStep(
        "accounts",
        "生成员工账号",
        "为有邮箱的在职员工生成账号",
        40,
        True,
        False,
        check_accounts,
    ),
    SetupStep(
        "sources",
        "接数据来源",
        "连接 Jira，或上传工作记录文件",
        50,
        False,
        True,
        check_sources,
    ),
    SetupStep(
        "identities",
        "确认账号对应",
        "把 Jira 和工作记录里的账号对应到员工",
        60,
        False,
        True,
        check_org_profile,
    ),
    SetupStep(
        "model",
        "配置模型",
        "配置并启用 AI 模型后才能起草",
        70,
        True,
        False,
        check_model,
    ),
    SetupStep(
        "approval_policy",
        "审批规则",
        "确认经理审批要求与员工过目期限",
        80,
        False,
        True,
        check_org_profile,
    ),
    SetupStep(
        "initial_profiles",
        "生成初始画像",
        "为员工生成待本人过目的初始画像",
        90,
        True,
        False,
        check_initial_profiles,
    ),
]
