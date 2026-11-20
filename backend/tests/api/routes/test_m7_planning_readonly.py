import json
from functools import cache

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup


@cache
def _business_tables(engine: sa.Engine) -> dict[str, sa.Table]:
    metadata = sa.MetaData()
    metadata.reflect(
        engine,
        only=lambda name, _: (
            name.startswith("capacity_") or name in {"audit_event", "inbox_item"}
        ),
        resolve_fks=False,
    )
    return dict(sorted(metadata.tables.items()))


def business_rows(db: Session, org_id: object) -> dict[str, str]:
    result = {}
    for name, table in _business_tables(db.connection().engine).items():
        rows = (
            db.execute(sa.select(table).where(table.c.org_id == org_id))
            .mappings()
            .all()
        )
        result[name] = json.dumps(
            [dict(row) for row in rows], sort_keys=True, default=str
        )
    return result


def test_planning_never_writes_capacity_or_audit(
    planning_setup: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth, demand = planning_setup
    before = business_rows(db, demand.org_id)
    created = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert created.status_code == 200, created.text
    assert business_rows(db, demand.org_id) == before
    case_url = f"{url}/planning/cases/{created.json()['id']}"
    assert client.get(case_url, headers=auth["b"]).status_code == 200
    assert business_rows(db, demand.org_id) == before
    body = {
        "assignees": [
            {
                "employee_id": str(employees["E04"].id),
                "allocation_percent": 50,
                "start_date": demand.window_start.isoformat(),
                "end_date": demand.window_end.isoformat(),
            }
        ]
    }
    result = client.post(f"{case_url}/options", headers=auth["b"], json=body)
    assert result.status_code == 200, result.text
    assert business_rows(db, demand.org_id) == before
