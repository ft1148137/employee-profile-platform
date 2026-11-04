from collections.abc import Generator
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
    RosterImport,
)
from app.domains.org.roster.diff import (
    CHANGE_KINDS,
    DepartmentNode,
    compute_roster_diff,
)
from app.domains.org.roster.parser import parse_roster
from app.domains.org.roster.schemas import ParsedRoster
from app.models import User

SAMPLE = Path(__file__).resolve().parents[4] / "examples/roster/sample-30.csv"
type Context = tuple[Session, Organization, User, OrgMembership]


@pytest.fixture
def context() -> Generator[Context]:
    with Session(engine) as session:
        org = Organization(slug=str(uuid4()), name="Synthetic")
        user = User(username=str(uuid4()), hashed_password="unused")
        session.add_all([org, user])
        session.flush()
        member = OrgMembership(org_id=org.id, user_id=user.id)
        session.add(member)
        session.flush()
        session.add(
            RoleGrant(org_id=org.id, membership_id=member.id, role=Role.ORG_ADMIN)
        )
        session.flush()
        yield session, org, user, member
        session.rollback()


def snapshot(
    session: Session, org: Organization, roster: ParsedRoster
) -> dict[str, Employee]:
    departments: dict[tuple[str, ...], Department] = {}
    for path in sorted(
        {
            r.department_path[:i]
            for r in roster.rows
            for i in range(1, len(r.department_path) + 1)
        },
        key=len,
    ):
        dept = Department(
            org_id=org.id,
            name=path[-1],
            parent_department_id=departments[path[:-1]].id if len(path) > 1 else None,
        )
        session.add(dept)
        session.flush()
        departments[path] = dept
    employees = {
        r.employee_code: Employee(
            org_id=org.id,
            employee_code=r.employee_code,
            display_name=r.display_name,
            title=r.title,
            work_email=r.email,
            department_id=departments[r.department_path].id,
        )
        for r in roster.rows
    }
    session.add_all(employees.values())
    session.flush()
    session.add_all(
        ReportingLine(
            org_id=org.id,
            employee_id=employees[r.employee_code].id,
            manager_employee_id=employees[r.manager_code].id,
        )
        for r in roster.rows
        if r.manager_code
    )
    session.flush()
    return employees


def flatten(nodes: list[DepartmentNode]) -> list[DepartmentNode]:
    return [item for node in nodes for item in [node, *flatten(node.children)]]


def test_sample_and_readonly_query_bound(context: Context) -> None:
    session, org, *_ = context
    roster = parse_roster(SAMPLE.read_bytes(), SAMPLE.name)
    result = compute_roster_diff(session, org.id, roster)
    assert result.summary == {
        **dict.fromkeys(CHANGE_KINDS, 0),
        "added": 30,
        "departments_created": 15,
        "departments_unused": 0,
    }
    assert not result.blockers and not result.warnings
    tree = flatten(result.departments)
    assert len(tree) == 15 and max(len(n.path) for n in tree) == 3
    assert result.departments[0].name == "星衍科技"
    assert sum(n.employee_count for n in tree) == 30
    assert all(n.is_new and n.department_id is None for n in tree)
    assert len([n for n in tree if n.name == "质量组"]) == 2
    assert len([n for n in tree if n.name == "中心办公室"]) == 3
    assert result.created_departments == sorted(
        result.created_departments, key=lambda p: (len(p), p)
    )
    statements: list[str] = []

    def count(*args: object) -> None:
        statements.append(str(args[2]))

    employees = snapshot(session, org, roster)
    event.listen(engine, "before_cursor_execute", count)
    try:
        for size in (30, 300):
            statements.clear()
            incoming = roster.model_copy(
                update={
                    "rows": [
                        roster.rows[i % 30].model_copy(
                            update={"employee_code": f"NEW{i}"}
                        )
                        for i in range(size)
                    ]
                }
            )
            compute_roster_diff(session, org.id, incoming)
            assert len(statements) == 5 and all(
                s.startswith("SELECT") for s in statements
            )
            assert not session.new and not session.dirty and not session.deleted
        pending = Employee(org_id=org.id, display_name="Pending")
        session.add(pending)
        employees["XY001"].title = "Pending title"
        statements.clear()
        compute_roster_diff(session, org.id, roster)
        assert len(statements) == 5 and pending in session.new
        assert employees["XY001"] in session.dirty
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_all_changes_warnings_and_tenancy(context: Context) -> None:
    session, org, *_ = context
    roster = parse_roster(SAMPLE.read_bytes(), SAMPLE.name)
    employees = snapshot(session, org, roster)
    employees["XY029"].employment_status = EmploymentStatus.INACTIVE
    unused = Department(org_id=org.id, name="Unused")
    session.add(unused)
    session.flush()
    session.add(
        Employee(org_id=org.id, display_name="No code", department_id=unused.id)
    )
    foreign = Organization(slug=str(uuid4()), name="Foreign")
    owner = User(
        username=str(uuid4()), email="taken@example.com", hashed_password="unused"
    )
    linked = User(
        username=str(uuid4()), email="old@example.com", hashed_password="unused"
    )
    session.add_all([foreign, owner, linked])
    session.flush()
    session.add_all(
        [
            Employee(org_id=foreign.id, employee_code="XY002", display_name="Foreign"),
            OrgMembership(
                org_id=org.id, user_id=linked.id, employee_id=employees["XY002"].id
            ),
        ]
    )
    session.flush()
    changed = roster.model_copy(deep=True)
    changed.rows[1] = changed.rows[1].model_copy(
        update={
            "employee_code": "xy002",
            "display_name": "New name",
            "title": "New title",
            "email": "TAKEN@example.com",
            "department_path": ("New", "Team"),
            "manager_code": "XY004",
        }
    )
    changed.rows[4].manager_code = None
    changed.rows = [r for r in changed.rows if r.employee_code != "XY030"]
    changed.rows.append(
        changed.rows[-1].model_copy(update={"employee_code": "XY031", "email": None})
    )
    result = compute_roster_diff(session, org.id, changed)
    assert result.summary == {
        "added": 1,
        "updated": 1,
        "transferred": 1,
        "manager_changed": 2,
        "deactivated": 1,
        "reactivated": 1,
        "departments_created": 2,
        "departments_unused": 2,
    }
    assert len(result.blockers) == 1 and result.active_count == 29
    assert len(result.warnings) == 2
    assert "1 名员工没有工号" in result.warnings[0].message
    assert "第 3 行" in result.blockers[0]
    assert "仍然停用" in result.warnings[-1].message
    multi = [c for c in result.changes if c.employee_code == "xy002"]
    assert {c.kind for c in multi} == {"updated", "transferred", "manager_changed"}
    assert [f.field for c in multi if c.kind == "updated" for f in c.fields] == [
        "display_name",
        "title",
        "email",
    ]
    transfer = next(c for c in multi if c.kind == "transferred").fields[0]
    assert (
        transfer.before == "星衍科技/研发中心/中心办公室"
        and transfer.after == "New/Team"
    )
    manager = next(c for c in multi if c.kind == "manager_changed").fields[0]
    assert manager.before == "欧阳宁（XY001）" and manager.after == "诸葛川（XY004）"
    assert all(c.display_name != "Foreign" for c in result.changes)
    assert ("Unused",) in result.unused_departments
    assert (
        next(
            n for n in flatten(result.departments) if n.path == ("Unused",)
        ).employee_count
        == 1
    )
    assert not session.new and not session.dirty


def test_optional_columns_case_and_manager_edges(context: Context) -> None:
    session, org, *_ = context
    initial = parse_roster(
        "工号,姓名,部门,职位,上级工号,邮箱\nE1,甲,A,工程师,,one@example.com\nE2,乙,A,工程师,E1,two@example.com".encode(),
        "a.csv",
    )
    employees = snapshot(session, org, initial)
    for text, expected in [
        ("工号,姓名,部门,邮箱\ne1,甲,A,one@example.com\ne2,乙,A,two@example.com", {}),
        ("工号,姓名,部门,邮箱\ne1,甲,A,ONE@example.com\ne2,乙,A,TWO@example.com", {}),
        (
            "工号,姓名,部门,职位,上级工号,邮箱\nE1,甲,A,,,one@example.com\nE2,乙,A,,,two@example.com",
            {"updated": 2, "manager_changed": 1},
        ),
        (
            "工号,姓名,部门,上级工号,邮箱\nE1,甲,A,E2,one@example.com\nE2,乙,A,,two@example.com",
            {"manager_changed": 2},
        ),
    ]:
        result = compute_roster_diff(
            session, org.id, parse_roster(text.encode(), "a.csv")
        )
        assert {k: v for k, v in result.summary.items() if v} == expected
    assert (
        employees["E1"].employee_code == "E1"
        and employees["E1"].work_email == "one@example.com"
    )
    invalid = parse_roster(b"invalid", "a.csv")
    with pytest.raises(ValueError, match="解析错误"):
        compute_roster_diff(session, org.id, invalid)


@pytest.mark.parametrize(
    ("active", "removed", "expected"),
    [(30, 5, False), (30, 6, False), (30, 7, True), (40, 8, False), (40, 9, True)],
)
def test_threshold(context: Context, active: int, removed: int, expected: bool) -> None:
    session, org, *_ = context
    roster = parse_roster(
        (
            "工号,姓名,部门,邮箱\n"
            + "\n".join(f"E{i},员工{i},A,e{i}@example.com" for i in range(active))
        ).encode(),
        "a.csv",
    )
    snapshot(session, org, roster)
    roster.rows = roster.rows[removed:]
    result = compute_roster_diff(session, org.id, roster)
    assert result.active_count == active
    assert result.summary["deactivated"] == removed
    assert result.requires_mass_deactivation_confirm is expected


def test_admin_and_code_blockers(context: Context) -> None:
    session, org, user, member = context
    roster = parse_roster(
        "工号,姓名,部门,邮箱\nE1,甲,A,one@example.com\nE2,乙,A,two@example.com".encode(),
        "a.csv",
    )
    employees = snapshot(session, org, roster)
    member.employee_id = employees["E1"].id
    session.add(member)
    session.flush()
    removed = roster.model_copy(update={"rows": roster.rows[1:]})
    assert "E1" in compute_roster_diff(session, org.id, removed).blockers[0]
    assert not compute_roster_diff(session, org.id, roster).blockers
    employees["E1"].employment_status = EmploymentStatus.INACTIVE
    session.flush()
    assert not compute_roster_diff(session, org.id, roster).blockers
    assert compute_roster_diff(session, org.id, removed).blockers
    user.is_active = False
    session.flush()
    assert compute_roster_diff(session, org.id, roster).blockers
    employees["E1"].employee_code = None
    session.flush()
    assert compute_roster_diff(session, org.id, roster).blockers
    user.is_active = True
    employees["E1"].employment_status = EmploymentStatus.ACTIVE
    session.add(Employee(org_id=org.id, employee_code="e2", display_name="Duplicate"))
    session.flush()
    blockers = compute_roster_diff(session, org.id, roster).blockers
    assert len(blockers) == 1 and "E2" in blockers[0] and "e2" in blockers[0]


def test_constraints(context: Context) -> None:
    session, org, user, _ = context
    roster = parse_roster(
        "工号,姓名,部门,上级工号,邮箱\nE1,甲,A,,one@example.com\nE2,乙,A/Team,E1,two@example.com".encode(),
        "a.csv",
    )
    employees = snapshot(session, org, roster)
    parent = Department(org_id=org.id, name="B")
    foreign = Organization(slug=str(uuid4()), name="Foreign")
    session.add_all([parent, foreign])
    session.flush()
    sibling = Department(org_id=org.id, name="Team", parent_department_id=parent.id)
    other = Employee(org_id=foreign.id, employee_code="F1", display_name="Foreign")
    receipt = RosterImport(
        org_id=foreign.id,
        filename="a.csv",
        file_sha256="0" * 64,
        rows=[r.model_dump(mode="json") for r in roster.rows],
        column_mapping=roster.column_mapping,
        issues=[],
        summary={},
        base_revision=0,
        uploaded_by_user_id=user.id,
    )
    session.add_all([sibling, other, receipt])
    session.flush()
    assert receipt.rows[0]["employee_code"] == "E1"
    report = partial(
        ReportingLine,
        org_id=org.id,
        employee_id=employees["E1"].id,
        manager_employee_id=employees["E2"].id,
    )
    invalid: list[tuple[SQLModel, str]] = [
        (Department(org_id=org.id, name="A"), "23505"),
        (
            Department(org_id=org.id, name="Team", parent_department_id=parent.id),
            "23505",
        ),
        (report(manager_employee_id=employees["E1"].id), "23514"),
        (
            report(
                employee_id=employees["E2"].id, manager_employee_id=employees["E1"].id
            ),
            "23505",
        ),
        (report(employee_id=other.id), "23503"),
        (report(manager_employee_id=other.id), "23503"),
        (report(roster_import_id=receipt.id), "23503"),
    ]
    for row, sqlstate in invalid:
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            session.add(row)
            session.flush()
        assert error.value.orig.sqlstate == sqlstate
    session.add(
        ReportingLine(
            org_id=org.id,
            employee_id=employees["E2"].id,
            manager_employee_id=employees["E1"].id,
            valid_to=utc_now(),
        )
    )
    session.flush()
    department = session.get(Department, employees["E1"].department_id)
    department.parent_department_id = department.id
    session.flush()
    with pytest.raises(ValueError, match="循环"):
        compute_roster_diff(session, org.id, roster)
