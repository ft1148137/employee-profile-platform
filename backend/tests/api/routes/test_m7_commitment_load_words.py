from uuid import UUID

from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
)
from app.domains.im.models import ImNotification
from app.domains.inbox.models import InboxItem
from tests.api.routes.test_m6_capacity_assignment import case as assignment_fixture
from tests.api.routes.test_m6_capacity_assignment import day
from tests.api.routes.test_m6_capacity_extension import case as extension_fixture
from tests.api.routes.test_m6_capacity_extension import decide, request

assignment_case = assignment_fixture
extension_case = extension_fixture


def seed_dictionary(db: Session, org_id: UUID) -> None:
    schema = CapabilitySchemaVersion(org_id=org_id, version=1, status="active")
    db.add(schema)
    db.flush()
    db.add(
        CapabilityDefinition(
            org_id=org_id,
            schema_version_id=schema.id,
            key="synthetic",
            name="合成能力",
        )
    )
    db.commit()


def state(db: Session, org_id: UUID) -> list[list[dict]]:
    db.expire_all()
    return [
        [
            row.model_dump(mode="json")
            for row in db.exec(
                select(model).where(model.org_id == org_id).order_by(model.id)
            ).all()
        ]
        for model in (
            CapacityDemand,
            CapacityAssignment,
            CapacityExtensionRequest,
            CapacityDemandEvent,
            InboxItem,
            ImNotification,
            AuditEvent,
        )
    ]


def test_assignment_commitment_rejects_load_words_without_writes(
    assignment_case: tuple, db: Session
) -> None:
    api, create, demand, _, _, org = assignment_case
    seed_dictionary(db, org.id)
    row = create()
    before = state(db, org.id)
    result = api(
        f"/assignments/{row['id']}",
        body={
            "due_date": day(35).isoformat(),
            "new_committed_end_date": day(35).isoformat(),
            "commitment_note": "人手已满，下周交付",
        },
        status=422,
        method="PATCH",
    )
    assert (
        result["detail"]
        == "回复将发送给对方部门，请勿填写负荷描述，仅说明能否承接、时间及条件"
    )
    assert state(db, org.id) == before
    api(
        f"/assignments/{row['id']}",
        body={
            "due_date": day(35).isoformat(),
            "new_committed_end_date": day(35).isoformat(),
            "commitment_note": "可以承接，下周交付",
        },
        method="PATCH",
    )
    assert (
        api(f"/demands/{demand.id}", "a")["committed_end_date"] == day(35).isoformat()
    )


def test_extension_commitment_rejects_load_words_without_writes(
    extension_case: tuple, db: Session
) -> None:
    api, create, demand, _, _, org = extension_case
    seed_dictionary(db, org.id)
    row = create(code="w2")
    pending = request(api, row, "2027-01-05", role="w2")
    before = state(db, org.id)
    result = decide(
        api,
        pending,
        "bm",
        422,
        new_committed_end_date="2027-01-05",
        commitment_note="近期空闲，可调整",
    )
    assert (
        result["detail"]
        == "回复将发送给对方部门，请勿填写负荷描述，仅说明能否承接、时间及条件"
    )
    assert state(db, org.id) == before
    decide(
        api,
        pending,
        "bm",
        new_committed_end_date="2027-01-05",
        commitment_note="可以承接，按新日期交付",
    )
    assert api(f"/demands/{demand.id}", "a")["committed_end_date"] == "2027-01-05"
