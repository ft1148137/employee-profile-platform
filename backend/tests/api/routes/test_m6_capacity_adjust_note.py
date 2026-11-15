from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session

from app.domains.capacity.models import CapacitySetting, CapacityWorkload


@pytest.fixture
def capacity(demo: tuple, db: Session) -> Generator[tuple]:
    yield demo
    db.rollback()
    for model in (CapacityWorkload, CapacitySetting):
        db.execute(delete(model).where(model.org_id == demo[1]["E02"].org_id))
    db.commit()


def test_direct_adjustment_rejects_note(capacity: tuple, client: TestClient) -> None:
    url, employees, auth = capacity
    response = client.put(
        f"{url}/capacity-settings",
        headers=auth["admin"],
        json={"enabled": True},
    )
    assert response.status_code == 200, response.text
    adjustment_url = f"{url}/capacity/workloads/{employees['E02'].id}"
    response = client.put(
        adjustment_url, headers=auth["manager"], json={"base_percent": 50}
    )
    assert response.status_code == 200, response.text
    assert response.json()["base_percent"] == 50

    response = client.put(
        adjustment_url,
        headers=auth["manager"],
        json={"base_percent": 60, "note": "Synthetic adjustment note"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "note"]
    response = client.get(f"{url}/capacity/workloads/me", headers=auth["employee"])
    assert response.status_code == 200, response.text
    assert response.json()["base_percent"] == 50
