from dataclasses import dataclass
from uuid import UUID

from app.domains.audit.models import ActorKind


class InvalidPrincipal(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    actor_type: ActorKind
    org_id: UUID
    user_id: UUID | None
    employee_id: UUID | None
    is_org_admin: bool
    managed_department_ids: frozenset[UUID]

    def __post_init__(self) -> None:
        if self.actor_type not in (ActorKind.USER, ActorKind.SYSTEM_WORKER):
            raise InvalidPrincipal("Unsupported principal actor type")
        if self.actor_type == ActorKind.USER and self.user_id is None:
            raise InvalidPrincipal("User principal requires a user ID")
        if self.actor_type == ActorKind.SYSTEM_WORKER and (
            self.user_id is not None
            or self.employee_id is not None
            or self.is_org_admin
            or self.managed_department_ids
        ):
            raise InvalidPrincipal("Worker principal cannot carry user roles")

    @classmethod
    def system_worker(cls, org_id: UUID) -> Principal:
        return cls(ActorKind.SYSTEM_WORKER, org_id, None, None, False, frozenset())
