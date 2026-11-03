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


def check_model(session: Session, org_id: UUID) -> list[str]:
    from app.domains.model.service import read_setting

    setting = read_setting(session, org_id)
    return (
        []
        if setting and setting.enabled
        else ["还没有启用模型；跳过则按关键词规则起草"]
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
        "为有工号的在职员工生成账号",
        40,
        True,
        False,
        check_accounts,
    ),
    SetupStep(
        "model",
        "配置模型",
        "启用模型或跳过使用关键词规则",
        70,
        False,
        True,
        check_model,
    ),
]
