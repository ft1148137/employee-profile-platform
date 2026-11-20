import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set
from app.domains.admin_assistant import store as admin_store
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.bot import store, tools
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import get_schema
from app.domains.change_request.batch_import import MaterialPerson
from app.domains.change_request.import_json import ProfileImport
from app.domains.change_request.models import ChangeItem
from app.domains.change_request.schemas import validation_message
from app.domains.profile.models import ProfileClaim
from app.domains.review.publish import current_claims
from tests.admin_assistant.test_registry import principal as admin_principal
from tests.api.routes.test_direct_profile_edits import edit_body
from tests.api.routes.test_employee_requests import payload, principal_for
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.api.routes.test_profile_import import example
from tests.api.routes.test_reviews import actor


def test_reject_long_statement(demo: tuple, client: TestClient) -> None:
    base, _, auth = demo
    response = client.post(
        f"{base}/me/requests",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": "add",
                    "capability_key": "automated_testing",
                    "statement": "中" * 201,
                }
            ]
        },
    )
    assert response.status_code == 422, response.text
    assert "能力说明最多 200 字" in response.text


@pytest.mark.parametrize(
    "entry",
    [
        "web",
        "direct",
        "bot-web",
        "bot-feishu",
        "team-web",
        "team-feishu",
        "mcp",
        "admin",
        "import",
        "materials",
    ],
)
@pytest.mark.parametrize(
    "problem,expected",
    [
        ("statement", "能力说明最多 200 字"),
        ("explanation", "说明最多 4000 字"),
        ("items", "每次最多 50 条改动"),
    ],
)
def test_all_entries_reject_overflow(
    demo: tuple,
    db: Session,
    client: TestClient,
    entry: str,
    problem: str,
    expected: str,
) -> None:
    base, people, auth = demo
    direct = entry == "direct" or entry.startswith("team") or entry == "admin"
    body = edit_body(db, people["E02"]) if direct else {"items": [payload()]}
    explanation = "explanation" if direct else "owner_statement"
    if entry in ("import", "materials"):
        body = example()
        explanation = "basis"
    if problem == "items":
        body["items"] *= 51
    else:
        body["items"][0]["statement" if problem == "statement" else explanation] = (
            "敏感合成文字"
            + "中" * ((201 if problem == "statement" else 4001) - len("敏感合成文字"))
        )
    if entry in ("web", "direct"):
        path = (
            f"{base}/employees/{people['E02'].id}/profile/direct-edits/preview"
            if direct
            else f"{base}/me/requests"
        )
        response = client.post(
            path, headers=auth["manager" if direct else "employee"], json=body
        )
        assert response.status_code == 422, response.text
        result = response.text
    elif entry.startswith(("bot", "team")):
        p = (
            actor(db, people["E02"].org_id, "manager.platform@demo.example.com")
            if direct
            else principal_for(db, people["E02"].org_id)
        )
        session = store.create_session(db, p, entry.split("-")[1])
        if direct:
            body["employee_id"] = str(people["E02"].id)
        result = json.dumps(
            tools.call(
                db,
                p,
                session,
                [],
                "prepare_profile_edit" if direct else "draft_update_request",
                body,
            ),
            ensure_ascii=False,
        )
        assert "error" in result
        assert "敏感合成文字" not in result
    elif entry == "mcp":
        headers = initialize(client, token_for(db, people["E02"])[0])
        response = call(client, headers, "draft_update_request", body)
        assert response["isError"]
        result = json.dumps(response, ensure_ascii=False)
        assert "敏感合成文字" not in result
    elif entry == "admin":
        p = admin_principal(db, demo)
        body["employee_id"] = str(people["E02"].id)
        with pytest.raises(change_set.ChangeSetError) as error:
            change_set.propose(
                db,
                p,
                admin_store.create_session(db, p).id,
                ProposeChanges.model_validate(
                    {"items": [{"function": "edit_employee_profile", "args": body}]}
                ),
            )
        result = str(error.value.detail)
    else:
        model = ProfileImport if entry == "import" else MaterialPerson
        if entry == "materials":
            body = {"employee_email": body["employee_email"], "items": body["items"]}
        with pytest.raises(ValidationError) as error:
            model.model_validate(body)
        result = validation_message(error.value)
    assert expected in result


@pytest.mark.parametrize(
    "field",
    [
        "statement",
        "owner_statement",
        "objection",
        "uncertainty",
        "explanation",
        "items",
    ],
)
def test_existing_draft_submission_checks_all_text(
    demo: tuple, db: Session, client: TestClient, field: str
) -> None:
    base, people, auth = demo
    response = client.post(
        f"{base}/me/requests", headers=auth["employee"], json={"items": [payload()]}
    )
    draft = response.json()
    row = db.exec(
        select(ChangeItem).where(
            ChangeItem.org_id == people["E02"].org_id,
            ChangeItem.request_id == draft["id"],
        )
    ).one()
    if field == "statement":
        row.after = {**row.after, "statement": "中" * 201}
    elif field == "items":
        for position in range(1, 51):
            db.add(
                ChangeItem(
                    org_id=row.org_id,
                    request_id=row.request_id,
                    capability_definition_id=row.capability_definition_id,
                    position=position,
                    op=row.op,
                    after=row.after,
                )
            )
    else:
        setattr(row, field, "中" * 4001)
    db.add(row)
    db.commit()
    response = client.post(
        f"{base}/me/requests/{draft['id']}/submit",
        headers=auth["employee"],
        json={"revision": draft["revision"]},
    )
    assert response.status_code == 422, response.text
    assert {"statement": "能力说明最多 200 字", "items": "每次最多 50 条改动"}.get(
        field, "说明最多 4000 字"
    ) in response.text


@pytest.mark.parametrize("entry", ["web", "direct"])
def test_exact_limits_and_fifty_items(
    demo: tuple, db: Session, client: TestClient, entry: str
) -> None:
    base, people, auth = demo
    org_id = people["E02"].org_id
    schema = get_schema(db, org_id)
    items = []
    for i in range(50):
        key = f"m5_limit_{i}"
        db.add(
            CapabilityDefinition(
                org_id=org_id, schema_version_id=schema.id, key=key, name=f"合成能力{i}"
            )
        )
        items.append(
            payload(
                key,
                statement="中a😀界" * 50,
                **{
                    "explanation" if entry == "direct" else "owner_statement": "中a😀界"
                    * 1000
                },
            )
        )
    db.commit()
    if entry == "direct":
        body = {**edit_body(db, people["E02"]), "items": items}
        response = client.post(
            f"{base}/employees/{people['E02'].id}/profile/direct-edits",
            headers=auth["manager"],
            json=body,
        )
        assert response.status_code == 200, response.text
    else:
        response = client.post(
            f"{base}/me/requests", headers=auth["employee"], json={"items": items}
        )
        assert response.status_code == 201, response.text
        response = client.post(
            f"{base}/me/requests/{response.json()['id']}/submit",
            headers=auth["employee"],
            json={"revision": 1},
        )
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("entry", ["web", "direct"])
def test_unchanged_published_long_statement_allows_level_change(
    demo: tuple, db: Session, client: TestClient, entry: str
) -> None:
    base, people, auth = demo
    target = current_claims(db, people["E02"].org_id, people["E02"].id)["backend_api"]
    target.statement = "旧" * 300
    db.add(target)
    db.commit()
    if entry == "direct":
        body = edit_body(db, people["E02"])
        body["items"][0]["statement"] = target.statement
        response = client.post(
            f"{base}/employees/{people['E02'].id}/profile/direct-edits",
            headers=auth["manager"],
            json=body,
        )
        assert response.status_code == 200, response.text
    else:
        response = client.post(
            f"{base}/me/requests",
            headers=auth["employee"],
            json={
                "items": [
                    payload(
                        "backend_api",
                        op="modify",
                        statement=target.statement,
                        level="routine",
                    )
                ]
            },
        )
        assert response.status_code == 201, response.text
        response = client.post(
            f"{base}/me/requests/{response.json()['id']}/submit",
            headers=auth["employee"],
            json={"revision": 1},
        )
        assert response.status_code == 200, response.text
    assert db.get(ProfileClaim, target.id).statement == "旧" * 300


def test_mcp_schema_publishes_limits(
    demo: tuple, db: Session, client: TestClient
) -> None:
    headers = initialize(client, token_for(db, demo[1]["E02"])[0])
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ).json()
    schema = next(
        t["inputSchema"]
        for t in response["result"]["tools"]
        if t["name"] == "draft_update_request"
    )
    assert schema["properties"]["items"]["maxItems"] == 50
    fields = schema["$defs"]["ItemInput"]["properties"]
    assert fields["statement"]["maxLength"] == 200
    for name in ("owner_statement", "objection", "uncertainty"):
        assert fields[name]["maxLength"] == 4000


def test_import_boundaries_and_admin_chinese_formatting() -> None:
    body = example()
    body["items"] = [
        {
            **body["items"][0],
            "statement": "中" * 200,
            "basis": "中" * 4000,
            "uncertainty": "中" * 4000,
        }
    ] * 50
    ProfileImport.model_validate(body)
    material = {"employee_email": body["employee_email"], "items": body["items"]}
    MaterialPerson.model_validate(material)
    material["items"][0]["basis"] += "中"
    with pytest.raises(change_set.ChangeSetError, match="说明最多 4000 字"):
        change_set._entry(
            "draft_profiles_from_material",
            {"materials": [{"label": "合成材料"}], "people": [material]},
        )


def test_unchanged_legacy_draft_can_save_but_must_fix_before_submit(
    demo: tuple, db: Session, client: TestClient
) -> None:
    base, people, auth = demo
    response = client.post(
        f"{base}/me/requests", headers=auth["employee"], json={"items": [payload()]}
    )
    draft = response.json()
    row = db.exec(
        select(ChangeItem).where(
            ChangeItem.org_id == people["E02"].org_id,
            ChangeItem.request_id == draft["id"],
        )
    ).one()
    row.after = {**row.after, "statement": "旧" * 300}
    row.owner_statement = "旧" * 4001
    db.add(row)
    db.commit()
    body = {
        "items": [
            payload(
                statement="旧" * 300,
                owner_statement=row.owner_statement,
                level="routine",
            )
        ]
    }
    url = f"{base}/me/requests/{draft['id']}"
    saved = client.put(f"{url}/items", headers=auth["employee"], json=body)
    assert saved.status_code == 200, saved.text
    rejected = client.post(
        f"{url}/submit",
        headers=auth["employee"],
        json={"revision": saved.json()["revision"]},
    )
    assert rejected.status_code == 422
    assert (
        "能力说明最多 200 字" in rejected.text and "说明最多 4000 字" in rejected.text
    )
    body["items"][0]["statement"] += "改"
    assert (
        client.put(f"{url}/items", headers=auth["employee"], json=body).status_code
        == 422
    )
