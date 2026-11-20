from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.capacity.models import CapacityAssignment, CapacitySetting
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Department, Employee, OrgMembership, RoleGrant
from tests.api.routes.test_m6_capacity_assignment import case as case
from tests.api.routes.test_m6_capacity_assignment import day


def test_departed_create_is_actionable_only_for_target_manager(
    case: tuple, db: Session
) -> None:
    api, create, demand, people, users, org = case
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()

    for role in ("a", "am", "w2", "cm", "admin", "foreign"):
        response = create(role=role, status=None)
        assert response.status_code == 404, (role, response.text)
        detail = (
            "未找到该企业或你没有权限访问"
            if role == "foreign"
            else "未找到该需求，或你无权查看（仅发起人及双方部门经理可见）。"
            if role in ("w2", "cm")
            else "未找到该员工或申请"
        )
        assert response.json() == {"detail": detail}

    response = create(status=None)
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": {
            "code": "employee_departed",
            "message": "该员工已不在职，请刷新后重新选择",
        }
    }
    assert (
        db.exec(
            select(CapacityAssignment).where(CapacityAssignment.org_id == org.id)
        ).all()
        == []
    )
    assert (
        db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "capacity.assignment_created",
            )
        ).all()
        == []
    )
    assert db.exec(select(InboxItem).where(InboxItem.org_id == org.id)).all() == []

    foreign_org_id = db.exec(
        select(OrgMembership.org_id).where(OrgMembership.user_id == users["foreign"].id)
    ).one()
    foreign_dept = Department(org_id=foreign_org_id, name="外部部门")
    db.add(foreign_dept)
    db.flush()
    foreign_person = Employee(
        org_id=foreign_org_id,
        department_id=foreign_dept.id,
        display_name="外部员工",
        employee_code="FOREIGN",
        employment_status="inactive",
    )
    db.add(foreign_person)
    db.commit()
    foreign_response = api(
        f"/demands/{demand.id}/assignments",
        body={
            "employee_id": str(foreign_person.id),
            "allocation_percent": 20,
            "start_date": day(0).isoformat(),
            "due_date": day(4).isoformat(),
        },
        status=None,
    )
    assert foreign_response.status_code == 404
    assert foreign_response.json() == {"detail": "未找到该员工或申请"}

    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == org.id)
    ).one()
    setting.enabled = False
    db.add(setting)
    db.commit()
    disabled_response = create(status=None)
    assert disabled_response.status_code == 404
    assert disabled_response.json() == {"detail": "规划与负荷功能未开启"}


def test_departed_outside_target_subtree_stays_hidden(case: tuple, db: Session) -> None:
    _, create, _, people, _, org = case
    other = Department(org_id=org.id, name="其他部门")
    db.add(other)
    db.flush()
    people["w"].department_id = other.id
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()

    response = create(status=None)
    assert response.status_code == 404
    assert response.json() == {"detail": "未找到该员工或申请"}


def test_departed_create_for_admin_managing_target(case: tuple, db: Session) -> None:
    _, create, demand, people, users, org = case
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id,
            OrgMembership.user_id == users["admin"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=org.id,
            membership_id=member.id,
            role="dept_manager",
            department_id=demand.target_department_id,
        )
    )
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()

    response = create(role="admin", status=None)
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": {
            "code": "employee_departed",
            "message": "该员工已不在职，请刷新后重新选择",
        }
    }
    assert (
        db.exec(
            select(CapacityAssignment).where(CapacityAssignment.org_id == org.id)
        ).all()
        == []
    )
    assert (
        db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "capacity.assignment_created",
            )
        ).all()
        == []
    )
