import json
from typing import Any

import httpx


def final_response(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    event = json.loads(response.text.splitlines()[-1])
    assert event["type"] == "final"
    return event["response"]
