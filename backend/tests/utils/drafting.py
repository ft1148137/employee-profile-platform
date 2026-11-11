from collections.abc import Callable
from uuid import UUID

import pytest
from sqlmodel import Session

from app.domains.drafting.models import DraftingSetting
from app.domains.model.models import ModelSetting
from tests.utils.pi import PiHarness


@pytest.fixture
def mock_drafting(db: Session, pi_service: PiHarness) -> Callable[[UUID, UUID], None]:
    pi_service.auto_draft = True

    def enable(org_id: UUID, user_id: UUID) -> None:
        db.merge(
            DraftingSetting(
                org_id=org_id, updated_by_user_id=user_id, auto_draft_enabled=True
            )
        )
        db.add(
            ModelSetting(
                org_id=org_id,
                updated_by_user_id=user_id,
                enabled=True,
                kind="openai_compatible",
                provider="openai-compatible",
                base_url=pi_service.config.baseUrl,
                model="synthetic",
            )
        )
        db.commit()

    return enable
