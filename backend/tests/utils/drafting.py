import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sqlmodel import Session

from app.domains.model.client import _sdk
from app.domains.model.models import ModelSetting


@pytest.fixture
def mock_drafting(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> Callable[[UUID, UUID], None]:
    sdk = _sdk()
    real = sdk.completion

    def completion(**kwargs: Any) -> Any:
        body = json.loads(kwargs["messages"][-1]["content"])
        items = []
        if body["records"] and body["capabilities"]:
            items.append(
                {
                    "capability_key": body["capabilities"][0]["key"],
                    "statement": "合成能力建议",
                    "level": None,
                    "evidence": [r["ref"] for r in body["records"]],
                    "explanation": "合成工作记录",
                    "uncertainty": "需要本人确认",
                }
            )
        return real(**kwargs, mock_response=json.dumps({"items": items}))

    monkeypatch.setattr(sdk, "completion", completion)

    def enable(org_id: UUID, user_id: UUID) -> None:
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

    return enable
