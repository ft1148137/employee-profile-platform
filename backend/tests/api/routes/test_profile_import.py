import json

from fastapi.testclient import TestClient

from app.domains.change_request.import_json import FILES_DIR, ProfileImport


def test_schema_and_authenticated_downloads(
    shared_demo: tuple, client: TestClient
) -> None:
    assert (
        json.loads((FILES_DIR / "v1.schema.json").read_bytes())
        == ProfileImport.model_json_schema()
    )
    assert ProfileImport.model_validate_json((FILES_DIR / "example.json").read_bytes())
    _, _, auth = shared_demo
    for name, filename in (
        ("schema", "v1.schema.json"),
        ("example", "example.json"),
        ("skill", "SKILL.md"),
    ):
        path = f"/api/v1/profile-import/files/{name}"
        assert client.get(path).status_code == 401
        response = client.get(path, headers=auth["employee"])
        assert response.status_code == 200
        assert response.content == (FILES_DIR / filename).read_bytes()
    assert (
        client.get(
            "/api/v1/profile-import/files/unknown", headers=auth["employee"]
        ).status_code
        == 422
    )


def test_upload_route_removed(shared_demo: tuple, client: TestClient) -> None:
    url, _, auth = shared_demo
    response = client.post(
        f"{url}/me/profile-import",
        headers=auth["employee"],
        files={"file": ("profile.json", b"{}", "application/json")},
    )
    assert response.status_code in (404, 405)
