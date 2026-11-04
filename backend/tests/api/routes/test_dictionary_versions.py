from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.domains.capability import service
from app.domains.capability.files import HEADERS, dictionary_xlsx, parse_dictionary
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.capability.schemas import DictionaryDefinition
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.evidence.models import Evidence
from app.domains.model.models import ModelSetting
from app.domains.org.models import OrgMembership
from app.domains.profile.models import ProfileClaim, ProfileVersion
from tests.utils.migrations import migrate


def exported(client: TestClient, url: str, headers: dict) -> list[dict]:
    response = client.get(f"{url}/capabilities/export", headers=headers)
    assert response.status_code == 200
    return yaml.safe_load(response.content)


def upload(
    client: TestClient, url: str, headers: dict, definitions: list[dict]
) -> dict:
    response = client.post(
        f"{url}/capabilities/preview",
        headers=headers,
        files={
            "file": (
                "custom.yaml",
                yaml.safe_dump(definitions, allow_unicode=True).encode(),
            )
        },
    )
    assert response.status_code == 200, response.text
    assert not response.json()["errors"], response.text
    return response.json()


def save(client: TestClient, url: str, headers: dict, preview: dict) -> object:
    return client.post(
        f"{url}/capabilities/versions",
        headers=headers,
        json={
            "definitions": preview["definitions"],
            "expected_version": preview["expected_version"],
        },
    )


def test_versions_preserve_records_remap_requests_and_block_stale_levels(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    org_id = employees["E02"].org_id
    original = {
        model: [
            r.model_dump(mode="json")
            for r in db.exec(
                select(model).where(model.org_id == org_id).order_by(model.id)
            ).all()
        ]
        for model in (ProfileClaim, ProfileVersion, Evidence)
    }
    frozen = [
        r.model_dump(mode="json")
        for r in db.exec(
            select(ProfileChangeRequest)
            .where(
                ProfileChangeRequest.org_id == org_id,
                ProfileChangeRequest.status == "pending_review",
            )
            .order_by(ProfileChangeRequest.id)
        ).all()
    ]
    initial = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["origin"] == "initial_sync"
    )
    row = db.get(ChangeItem, UUID(initial["items"][0]["id"]))
    row.after = {**row.after, "level": "routine"}
    db.commit()
    draft = client.post(
        f"{url}/me/requests",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": "add",
                    "capability_key": "automated_testing",
                    "statement": "合成经历",
                    "level": "routine",
                }
            ]
        },
    ).json()
    definitions = exported(client, url, auth["employee"])
    definitions = [d for d in definitions if d["key"] != "incident_support"]
    for definition in definitions:
        if definition["key"] in ("automated_testing", "backend_api"):
            definition["level_rubric"]["levels"] = [
                {"key": "new_level", "label": "新等级", "description": "新等级描述"}
            ]
        if definition["key"] == "automated_testing":
            definition["ai_inference"] = False
    previous_profile = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    # Remove a capability with a published claim as well as an initial request item.
    old_claim = next(i for i in previous_profile["items"] if i["claim"])
    key = old_claim["capability"]["key"]
    definitions = [d for d in definitions if d["key"] != key]
    preview = upload(client, url, auth["admin"], definitions)
    assert "incident_support" in preview["removed"]
    assert "automated_testing" in preview["modified"]
    response = save(client, url, auth["admin"], preview)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == preview["expected_version"] + 1
    db.expire_all()
    for model, before in original.items():
        assert [
            r.model_dump(mode="json")
            for r in db.exec(
                select(model).where(model.org_id == org_id).order_by(model.id)
            ).all()
        ] == before
    assert [
        r.model_dump(mode="json")
        for r in db.exec(
            select(ProfileChangeRequest)
            .where(
                ProfileChangeRequest.org_id == org_id,
                ProfileChangeRequest.status == "pending_review",
            )
            .order_by(ProfileChangeRequest.id)
        ).all()
    ] == frozen
    profile = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    removed = next(i for i in profile["items"] if i["capability"]["key"] == key)
    assert removed["retired"] and removed["claim"] == old_claim["claim"]
    for request in (draft, initial):
        base = f"{url}/me/requests/{request['id']}"
        assert (
            client.post(
                f"{base}/submit",
                headers=auth["employee"],
                json={"revision": request["revision"]},
            ).status_code
            == 409
        )
        current = client.get(f"{base}/preview", headers=auth["employee"]).json()
        assert current["revision"] == request["revision"] + 1
        assert current["items"][0]["warning"] == "等级已调整，请重新选择或清空"
        assert current["items"][0]["after"]["after"]["level"] == "routine"
        invalid = client.post(
            f"{base}/submit",
            headers=auth["employee"],
            json={"revision": current["revision"]},
        )
        assert invalid.status_code == 422
        assert "等级 key" in invalid.json()["detail"][0]["message"]
        changed = client.put(
            f"{base}/items",
            headers=auth["employee"],
            json={
                "items": [
                    {
                        "op": "add",
                        "capability_key": "automated_testing",
                        "statement": "确认合成经历",
                        "level": None if request is initial else "new_level",
                    }
                ]
            },
        ).json()
        assert (
            client.post(
                f"{base}/submit",
                headers=auth["employee"],
                json={"revision": changed["revision"]},
            ).status_code
            == 200
        )
    notices = client.get(f"{url}/me/inbox", headers=auth["employee"]).json()["data"]
    assert any(
        n["kind"] == "dictionary_changed"
        and "1 项已移除；1 项的等级需要重新选择" in n["message"]
        for n in notices
    )
    settings = client.get(f"{url}/drafting-settings", headers=auth["admin"]).json()
    assert "automated_testing" in settings["excluded_capability_keys"]
    assert (
        next(
            d
            for d in exported(client, url, auth["employee"])
            if d["key"] == "automated_testing"
        )["ai_inference"]
        is False
    )
    assert save(client, url, auth["admin"], preview).status_code == 409


def test_profile_matches_keys_and_original_level_description(
    demo: tuple, client: TestClient
) -> None:
    url, _, auth = demo
    before = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    item = next(i for i in before["items"] if i["claim"] and i["claim"]["level"])
    definitions = exported(client, url, auth["admin"])
    definition = next(d for d in definitions if d["key"] == item["capability"]["key"])
    definition["level_rubric"]["levels"] = [
        {"key": "replacement", "label": "新等级", "description": "新的描述"}
    ]
    assert (
        save(
            client, url, auth["admin"], upload(client, url, auth["admin"], definitions)
        ).status_code
        == 200
    )
    after = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    matched = next(
        i for i in after["items"] if i["capability"]["key"] == definition["key"]
    )
    assert matched["claim"] == item["claim"]
    assert matched["capability"]["id"] != item["capability"]["id"]
    assert matched["level_adjusted"] and matched["original_level_description"]
    assert (
        client.get(
            f"{url}/employees/{before['employee']['id']}/profile-versions/1",
            headers=auth["employee"],
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "role,status", [("manager", 403), ("employee", 403), ("foreign", 404)]
)
def test_dictionary_permissions(
    shared_demo: tuple, client: TestClient, role: str, status: int
) -> None:
    url, _, auth = shared_demo
    for endpoint in ("preview", "versions"):
        assert (
            client.post(
                f"{url}/capabilities/{endpoint}", headers=auth[role], json={}
            ).status_code
            == status
        )
    assert (
        client.get(f"{url}/capabilities/export", headers=auth["foreign"]).status_code
        == 404
    )
    assert (
        client.get(f"{url}/capabilities/template", headers=auth[role]).status_code
        == status
    )


def test_concurrent_versions_and_atomic_rollback(
    demo: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, employees, auth = demo
    definitions = exported(client, url, auth["admin"])
    preview = upload(client, url, auth["admin"], definitions)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: save(client, url, auth["admin"], preview).status_code,
                range(2),
            )
        )
    assert sorted(results) == [200, 409]
    schemas = db.exec(
        select(CapabilitySchemaVersion).where(
            CapabilitySchemaVersion.org_id == employees["E02"].org_id
        )
    ).all()
    assert sum(s.status == "active" for s in schemas) == 1
    preview = upload(client, url, auth["admin"], definitions)

    def fail(*_args: object) -> None:
        raise RuntimeError("Synthetic inbox failure")

    monkeypatch.setattr(service, "notify", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        save(client, url, auth["admin"], preview)
    assert (
        client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
            "schema_version"
        ]["version"]
        == preview["expected_version"]
    )


def test_files_and_chinese_validation(shared_demo: tuple, client: TestClient) -> None:
    url, _, auth = shared_demo
    for format in ("yaml", "xlsx"):
        template = client.get(
            f"{url}/capabilities/template?format={format}", headers=auth["admin"]
        )
        assert template.status_code == 200
        parsed, errors = parse_dictionary(template.content, f"template.{format}")
        assert parsed and not errors
    definitions = [
        DictionaryDefinition.model_validate(d)
        for d in exported(client, url, auth["admin"])
    ]
    response = client.post(
        f"{url}/capabilities/preview",
        headers=auth["admin"],
        files={"file": ("custom.xlsx", dictionary_xlsx(definitions))},
    )
    assert response.status_code == 200 and not response.json()["errors"]
    parsed, errors = parse_dictionary(dictionary_xlsx(definitions), "custom.xlsx")
    assert parsed == definitions and not errors
    raw = definitions[0].model_dump()
    for data in (
        [raw, raw],
        [{**raw, "name": ""}],
        [{**raw, "level_rubric": {"levels": [raw["level_rubric"]["levels"][0]] * 2}}],
    ):
        _, errors = parse_dictionary(yaml.safe_dump(data).encode(), "bad.yaml")
        assert errors and all("第 " in e and "行" in e for e in errors)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    sheet.append(("test", "", "", "", "x", "标签", "描述", True))
    content = BytesIO()
    workbook.save(content)
    _, errors = parse_dictionary(content.getvalue(), "bad.xlsx")
    assert any("第 2 行" in e for e in errors)
    for content, filename in (
        (b"bad", "bad.zip"),
        (b"bad", "bad.xlsx"),
        (b"[", "bad.yaml"),
        (b"{}", "bad.yaml"),
        (b"[]", "bad.yaml"),
    ):
        assert parse_dictionary(content, filename)[1]
    assert (
        client.post(
            f"{url}/capabilities/preview",
            headers=auth["admin"],
            files={"file": ("big.yaml", b"x" * (2 * 1024 * 1024 + 1))},
        ).status_code
        == 413
    )


def test_dictionary_notification_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a89e001bc001")
    migrate(migration_engine, "upgrade", "a91e001bc001")
    with migration_engine.connect() as connection:
        column = next(
            c
            for c in inspect(connection).get_columns("inbox_item")
            if c["name"] == "kind"
        )
        assert column["type"].length == 18
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all() == ["a91e001bc001"]
    from app.core.config import settings
    from app.core.db import init_db
    from app.domains.inbox.models import InboxItem, InboxKind
    from app.domains.org.models import Employee
    from app.seed.loader import load_demo

    with Session(migration_engine) as session:
        init_db(session)
        session.rollback()
        load_demo(session, settings.DEMO_DATA_DIR)
        employee = session.exec(select(Employee)).first()
        for kind in (InboxKind.DICTIONARY_CHANGED, InboxKind.SUBMITTED):
            session.add(
                InboxItem(
                    org_id=employee.org_id,
                    recipient_employee_id=employee.id,
                    kind=kind,
                    message="合成通知",
                )
            )
        session.commit()
    migrate(migration_engine, "downgrade", "a89e001bc001")
    with migration_engine.connect() as connection:
        assert (
            next(
                c
                for c in inspect(connection).get_columns("inbox_item")
                if c["name"] == "kind"
            )["type"].length
            == 14
        )
        assert connection.execute(
            text("SELECT kind FROM inbox_item")
        ).scalars().all() == ["submitted"]
    migrate(migration_engine, "upgrade", "head")


def test_redraft_only_queues_pending_owner_and_matches_published_key(
    demo: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domains.capability.models import CapabilityDefinition
    from app.domains.change_request.claim import ClaimAfter
    from app.domains.drafting.model import DraftItem
    from app.domains.drafting.service import write_initial_request
    from app.domains.profile.models import AssessmentStatus

    url, employees, auth = demo
    org_id = employees["E02"].org_id
    user_id = db.exec(
        select(OrgMembership.user_id).where(OrgMembership.org_id == org_id)
    ).first()
    db.add(
        ModelSetting(
            org_id=org_id,
            updated_by_user_id=user_id,
            enabled=True,
            kind="cloud_api",
            model="openai/gpt-4o-mini",
        )
    )
    db.commit()
    definitions = exported(client, url, auth["admin"])
    preview = upload(client, url, auth["admin"], definitions)
    response = client.post(
        f"{url}/capabilities/versions",
        headers=auth["admin"],
        json={
            "definitions": definitions,
            "expected_version": preview["expected_version"],
            "redraft": True,
        },
    )
    assert response.status_code == 200, response.text
    jobs = (
        db.execute(
            text(
                "SELECT args FROM procrastinate_jobs WHERE task_name='profiles.draft_initial' AND args->>'org_id'=:org"
            ),
            {"org": str(org_id)},
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1 and jobs[0]["employee_ids"] == [str(employees["E02"].id)]
    db.execute(
        text(
            "DELETE FROM procrastinate_jobs WHERE task_name='profiles.draft_initial' AND args->>'org_id'=:org"
        ),
        {"org": str(org_id)},
    )
    claim = db.exec(
        select(ProfileClaim)
        .where(
            ProfileClaim.org_id == org_id,
            ProfileClaim.subject_employee_id == employees["E02"].id,
        )
        .order_by(ProfileClaim.created_at.desc())
    ).first()
    old = db.get(CapabilityDefinition, claim.capability_definition_id)
    current = db.exec(
        select(CapabilityDefinition).where(
            CapabilityDefinition.org_id == org_id,
            CapabilityDefinition.key == old.key,
            CapabilityDefinition.id != old.id,
        )
    ).one()
    assert write_initial_request(
        db,
        org_id,
        employees["E02"],
        [
            DraftItem(
                capability_id=current.id,
                after=ClaimAfter(
                    statement="重新起草合成判断",
                    assessment_status=AssessmentStatus.TENTATIVE,
                ),
                evidence={},
                explanation="合成依据",
                uncertainty="待本人确认",
            )
        ],
        {},
    )
    row = db.exec(
        select(ChangeItem).where(
            ChangeItem.org_id == org_id,
            ChangeItem.capability_definition_id == current.id,
        )
    ).one()
    assert row.op == "modify" and row.target_claim_id == claim.id
    db.commit()
    from app.authz.principal import Principal
    from app.domains.drafting import service as drafting

    seen: list[list[str]] = []

    def draft(_records: object, capabilities: list, _config: object) -> tuple:
        seen.append([c.key for c in capabilities])
        if len(seen) == 1:
            for definition in definitions:
                if definition["key"] == "automated_testing":
                    definition["ai_inference"] = False
            assert (
                save(
                    client,
                    url,
                    auth["admin"],
                    upload(client, url, auth["admin"], definitions),
                ).status_code
                == 200
            )
        return [], {"actor_type": "agent"}

    monkeypatch.setattr(drafting.model, "draft", draft)
    db.rollback()
    drafting.run_drafting(
        Principal.system_worker(org_id),
        [str(employees[code].id) for code in ("E01", "E02")],
    )
    assert len(seen) == 2
    assert "automated_testing" in seen[0] and "automated_testing" not in seen[1]
    from app.domains.drafting.service import InvalidDraft

    with pytest.raises(InvalidDraft, match="词典已更新"):
        write_initial_request(
            db,
            org_id,
            employees["E02"],
            [
                DraftItem(
                    capability_id=old.id,
                    after=ClaimAfter(
                        statement="旧版本模型结果",
                        assessment_status=AssessmentStatus.TENTATIVE,
                    ),
                    evidence={},
                    explanation="合成依据",
                    uncertainty="待确认",
                )
            ],
            {},
        )
    db.rollback()


def test_removing_all_items_keeps_request_and_failed_redraft_rolls_back(
    demo: tuple, client: TestClient
) -> None:
    url, _, auth = demo
    initial = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["origin"] == "initial_sync"
    )
    definitions = [
        d
        for d in exported(client, url, auth["admin"])
        if d["key"] not in ("automated_testing", "incident_support")
    ]
    preview = upload(client, url, auth["admin"], definitions)
    failed = client.post(
        f"{url}/capabilities/versions",
        headers=auth["admin"],
        json={
            "definitions": definitions,
            "expected_version": preview["expected_version"],
            "redraft": True,
        },
    )
    assert failed.status_code == 409
    assert (
        client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
            "schema_version"
        ]["version"]
        == preview["expected_version"]
    )
    assert save(client, url, auth["admin"], preview).status_code == 200
    empty = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["id"] == initial["id"]
    )
    assert empty["items"] == [] and empty["status"] == "pending_owner"
    assert empty["revision"] == initial["revision"] + 1
