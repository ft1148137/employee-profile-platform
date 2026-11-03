import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlmodel import Session

from app.core.config import settings
from app.core.db import init_db
from app.seed.loader import load_demo
from tests.test_account_migration import migrate

PREVIOUS = "f52a917c0b32"


@pytest.mark.parametrize("nonempty_note", [False, True])
def test_change_request_roundtrip(
    migration_engine: Engine, nonempty_note: bool
) -> None:
    with Session(migration_engine) as session:
        init_db(session)
        session.rollback()
        load_demo(session, Path(settings.DEMO_DATA_DIR))
    migrate("downgrade", PREVIOUS)
    ids = [uuid4() for _ in range(6)]
    statuses = [
        "accepted",
        "pending_manager",
        "pending_manager",
        "pending_employee",
        "rejected",
        "candidate",
    ]
    with migration_engine.begin() as c:
        context_sql = text(
            "SELECT e.org_id,e.id,s.id FROM employee e JOIN capability_schema_version s ON s.org_id=e.org_id WHERE e.employee_code='E02'"
        )
        org, employee, schema = c.execute(context_sql).one()
        params = {"org": org, "ids": ids, "employee": employee}
        cap_sql = text(
            "SELECT id FROM capability_definition WHERE org_id=:org ORDER BY position"
        )
        evidence_sql = text(
            "SELECT id FROM evidence WHERE org_id=:org AND subject_employee_id=:employee LIMIT 1"
        )
        caps = c.execute(cap_sql, params).scalars().all()
        evidence = c.execute(evidence_sql, params).scalar_one()
        for i, (id_, status) in enumerate(zip(ids, statuses, strict=True)):
            c.execute(
                text("""INSERT INTO profile_claim (id,org_id,created_at,subject_employee_id,capability_definition_id,statement,level,assessment_status,review_status,confidence,explanation,generated_by,supersedes_claim_id)
                VALUES (:id,:org,'2026-01-02T03:04:05Z',:employee,:cap,'Synthetic statement','routine','tentative',:status,'medium','Synthetic explanation',CAST(:generated AS jsonb),:supersedes)"""),
                {
                    "id": id_,
                    "org": org,
                    "employee": employee,
                    "cap": caps[8 if i in (0, 3) else i],
                    "status": status,
                    "generated": json.dumps(
                        {"actor_type": "seed", "seed_ref": str(id_)}
                    ),
                    "supersedes": ids[0] if i == 3 else None,
                },
            )
            c.execute(
                text(
                    "INSERT INTO claim_evidence (org_id,claim_id,evidence_id,relation,note) VALUES (:org,:id,:evidence,'supports',:note)"
                ),
                {
                    "org": org,
                    "id": id_,
                    "evidence": evidence,
                    "note": "Do not lose this" if nonempty_note and i == 1 else None,
                },
            )
        c.execute(
            text("""INSERT INTO profile_version (id,org_id,created_at,subject_employee_id,revision,schema_version_id,snapshot,published_at,published_by)
            VALUES (:id,:org,now(),:employee,3,:schema,CAST(:snapshot AS jsonb),now(),'{"actor_type":"seed"}')"""),
            {
                "id": uuid4(),
                "org": org,
                "employee": employee,
                "schema": schema,
                "snapshot": json.dumps({"claims": [{"id": str(ids[2])}]}),
            },
        )
        claims_sql = text(
            "SELECT * FROM profile_claim WHERE org_id=:org AND id=ANY(:ids) ORDER BY id"
        )
        links_sql = text(
            "SELECT * FROM claim_evidence WHERE org_id=:org AND claim_id=ANY(:ids) ORDER BY claim_id"
        )
        original = c.execute(claims_sql, params).mappings().all()
        links = c.execute(links_sql, params).mappings().all()
    if nonempty_note:
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate("upgrade", "head")
        assert (
            str(ids[1]).encode() in error.value.stderr and b"note" in error.value.stderr
        )
        with migration_engine.connect() as c:
            assert (
                c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == PREVIOUS
            )
        return
    migrate("upgrade", "head")
    with migration_engine.connect() as c:
        retained = c.execute(claims_sql, params).mappings().all()
        assert {r["id"] for r in retained} == {ids[0], ids[2]}
        request_sql = text(
            "SELECT * FROM profile_change_request WHERE org_id=:org AND id=:id"
        )
        item_sql = text("SELECT * FROM change_item WHERE org_id=:org AND id=:id")
        evidence_sql = text(
            "SELECT evidence_id,relation FROM change_item_evidence WHERE org_id=:org AND item_id=:id"
        )
        for i, status in (
            (1, "pending_review"),
            (3, "pending_owner"),
            (4, "rejected"),
            (5, "draft"),
        ):
            key = params | {"id": ids[i]}
            request = c.execute(request_sql, key).mappings().one()
            item = c.execute(item_sql, key).mappings().one()
            before = next(row for row in original if row["id"] == ids[i])
            assert request["status"] == status and request["origin"] == "manual"
            assert (
                request["author"] == request["generated_by"] == before["generated_by"]
            )
            assert (
                request["created_at"]
                == request["updated_at"]
                == item["created_at"]
                == before["created_at"]
            )
            assert item["after"] == {
                key: before[key]
                for key in ("statement", "level", "assessment_status", "confidence")
            }
            assert (
                item["explanation"] == before["explanation"]
                and item["uncertainty"] is None
            )
            assert item["op"] == ("modify" if i == 3 else "add")
            assert item["target_claim_id"] == (ids[0] if i == 3 else None)
            assert c.execute(evidence_sql, key).one() == (evidence, "supports")
    migrate("downgrade", PREVIOUS)
    with migration_engine.connect() as c:
        restored = c.execute(claims_sql, params).mappings().all()
        expected = [dict(row) for row in original]
        for row in expected:
            if row["id"] in {ids[0], ids[2]}:
                row["review_status"] = "accepted"
        assert restored == expected
        assert c.execute(links_sql, params).mappings().all() == links
        progress_sql = text(
            "SELECT review_status FROM profile_progress WHERE org_id=:org AND employee_id=:employee"
        )
        assert c.execute(progress_sql, params).scalar_one() == "pending_employee"
    migrate("upgrade", "head")
    with Session(migration_engine) as session:
        # A migrated demo must recognize the original seed refs and positions.
        result = load_demo(session, Path(settings.DEMO_DATA_DIR))
        assert result["counts"]["profile_change_request"]["existing"] == 4
        # Downgrade maps each initial item to a manual request; seed recreates the initial request.
        again = load_demo(session, Path(settings.DEMO_DATA_DIR))
        assert all(row["added"] == 0 for row in again["counts"].values())
