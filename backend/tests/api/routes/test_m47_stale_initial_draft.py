import pytest
from fastapi.testclient import TestClient


def initial_draft(client: TestClient, demo: tuple) -> tuple[str, dict]:
    url, _, auth = demo
    initial = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["origin"] == "initial_sync"
    )
    return f"{url}/me/requests/{initial['id']}", initial


def publish(
    client: TestClient, demo: tuple, *, statement: str = "经理当前判断"
) -> dict:
    url, employees, auth = demo
    profile_url = f"{url}/employees/{employees['E02'].id}/profile"
    profile = client.get(profile_url, headers=auth["manager"]).json()
    claim = next(
        i["claim"]
        for i in profile["items"]
        if i["capability"]["key"] == "automated_testing"
    )
    response = client.post(
        f"{profile_url}/direct-edits",
        headers=auth["manager"],
        json={
            "base_revision": profile["latest_published"]["revision"],
            "items": [
                {
                    "op": "modify" if claim else "add",
                    "capability_key": "automated_testing",
                    "statement": statement,
                    "level": "routine",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return client.get(f"{url}/me/profile", headers=auth["employee"]).json()


def assert_formal_unchanged(client: TestClient, demo: tuple, formal: dict) -> None:
    current = client.get(f"{demo[0]}/me/profile", headers=demo[2]["employee"]).json()
    assert current["items"] == formal["items"]
    assert current["latest_published"] == formal["latest_published"]


@pytest.mark.parametrize("edit", [False, True])
def test_stale_initial_preview_and_submission(
    demo: tuple, client: TestClient, edit: bool
) -> None:
    _, _, auth = demo
    base, _ = initial_draft(client, demo)
    formal = publish(client, demo)
    preview = client.get(f"{base}/preview", headers=auth["employee"]).json()
    changed = next(
        i
        for i in preview["items"]
        if i["after"]["capability"]["key"] == "automated_testing"
    )
    assert changed["before"]["statement"] == "经理当前判断"
    assert changed["current_changed_at"]
    assert changed["after"]["op"] == "modify"
    assert changed["after"]["target_claim_id"] == changed["before"]["id"]
    if edit:
        result = client.put(
            f"{base}/items",
            headers=auth["employee"],
            json={
                "items": [
                    {
                        "op": "add",  # Old editors may still send the original operation.
                        "capability_key": "automated_testing",
                        "statement": "本人根据当前值补充",
                        "level": "routine",
                    }
                ]
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["op"] == "modify"
        preview = client.get(f"{base}/preview", headers=auth["employee"]).json()
    result = client.post(
        f"{base}/submit",
        headers=auth["employee"],
        json={"revision": preview["revision"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "pending_review"
    assert_formal_unchanged(client, demo, formal)


def test_discard_one_stale_item(demo: tuple, client: TestClient) -> None:
    _, _, auth = demo
    base, initial = initial_draft(client, demo)
    formal = publish(client, demo)
    retained = next(
        i for i in initial["items"] if i["capability"]["key"] == "incident_support"
    )
    result = client.put(
        f"{base}/items",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": retained["op"],
                    "capability_key": retained["capability"]["key"],
                    "statement": retained["after"]["statement"],
                }
            ]
        },
    )
    assert result.status_code == 200, result.text
    result = client.post(
        f"{base}/submit",
        headers=auth["employee"],
        json={"revision": result.json()["revision"]},
    )
    assert result.status_code == 200
    assert len(result.json()["items"]) == 1
    assert_formal_unchanged(client, demo, formal)


@pytest.mark.parametrize("preview_before", [False, True])
def test_direct_submission_aligns_to_latest_without_publishing(
    demo: tuple, client: TestClient, preview_before: bool
) -> None:
    _, _, auth = demo
    base, initial = initial_draft(client, demo)
    publish(client, demo)
    preview = (
        client.get(f"{base}/preview", headers=auth["employee"]).json()
        if preview_before
        else initial
    )
    formal = publish(client, demo, statement="经理再次修改")
    stale = client.post(
        f"{base}/submit",
        headers=auth["employee"],
        json={"revision": preview["revision"]},
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["status"] == "pending_review"
    current = next(
        i["claim"]
        for i in formal["items"]
        if i["capability"]["key"] == "automated_testing"
    )
    assert stale.json()["items"][0]["target_claim_id"] == current["id"]
    assert_formal_unchanged(client, demo, formal)


@pytest.mark.parametrize("role", ["manager", "admin", "foreign"])
def test_others_cannot_realign_or_submit_initial(
    demo: tuple, client: TestClient, role: str
) -> None:
    _, _, auth = demo
    base, initial = initial_draft(client, demo)
    publish(client, demo)
    assert client.get(f"{base}/preview", headers=auth[role]).status_code == 404
    assert (
        client.put(f"{base}/items", headers=auth[role], json={"items": []}).status_code
        == 404
    )
    assert (
        client.post(
            f"{base}/submit", headers=auth[role], json={"revision": initial["revision"]}
        ).status_code
        == 404
    )
