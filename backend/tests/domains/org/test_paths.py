from uuid import uuid4

from app.domains.org.models import Department
from app.domains.org.paths import department_paths


def test_paths_stop_at_cycles_and_missing_parents() -> None:
    org_id = uuid4()
    a = Department(org_id=org_id, name="A")
    b = Department(org_id=org_id, name="B", parent_department_id=a.id)
    a.parent_department_id = b.id
    orphan = Department(org_id=org_id, name="Orphan", parent_department_id=uuid4())
    assert department_paths([a, b, orphan]) == {
        a.id: "B/A",
        b.id: "A/B",
        orphan.id: "Orphan",
    }
    assert department_paths([]) == {}
