from collections.abc import Sequence
from uuid import UUID

from app.domains.org.models import Department


def department_paths(departments: Sequence[Department]) -> dict[UUID, str]:
    by_id = {department.id: department for department in departments}
    paths = {}
    for department in departments:
        names: list[str] = []
        visited: set[UUID] = set()
        current: Department | None = department
        while current and current.id not in visited:
            visited.add(current.id)
            names.append(current.name)
            current = (
                by_id.get(current.parent_department_id)
                if current.parent_department_id
                else None
            )
        paths[department.id] = "/".join(reversed(names))
    return paths
