"""Local opt-in continuation after the real Cloud browser walkthrough."""

import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.evidence.models import Evidence
from app.domains.org.models import OrgMembership
from app.domains.source.models import SourceConnection
from app.models import User

pytestmark = pytest.mark.skipif(
    os.getenv("M2_JIRA_INCREMENTAL") != "1"
    or any(not os.getenv(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")),
    reason="Local real Cloud continuation requires explicit opt-in and credentials",
)


def test_seed_touch_replaces_only_one_evidence(db: Session, client: TestClient) -> None:
    connections = [
        c
        for c in db.exec(
            select(SourceConnection).where(SourceConnection.kind == "jira")
        ).all()
        if c.config["base_url"] == os.environ["JIRA_BASE_URL"].rstrip("/")
    ]
    assert connections, "Run m2-real-jira.spec.ts against this database first"
    org = max(connections, key=lambda c: c.created_at).org_id
    user_id = db.exec(
        select(OrgMembership.user_id).where(OrgMembership.org_id == org)
    ).one()
    user = db.get(User, user_id)
    assert user
    headers = {
        "Authorization": "Bearer "
        + create_access_token(user.id, timedelta(minutes=5), user.password_changed_at)
    }
    base = f"/api/v1/orgs/{org}"
    script = Path(__file__).resolve().parents[3] / "scripts/seed_jira_cloud.py"

    def seed(*args: str) -> None:
        subprocess.run(
            [sys.executable, str(script), *args], check=True, capture_output=True
        )

    def synchronize() -> dict:
        response = client.post(base + "/sources/jira/sync", headers=headers)
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(45):
            result = next(
                r
                for r in client.get(base + "/sync-runs", headers=headers).json()["data"]
                if r["id"] == run_id
            )
            if result["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        assert result["status"] == "succeeded"
        return result

    def snapshot() -> dict:
        db.expire_all()
        return {
            str(e.id): (
                e.source_ref,
                str(e.status),
                e.content_hash,
                str(e.supersedes_evidence_id),
            )
            for e in db.exec(select(Evidence).where(Evidence.org_id == org)).all()
        }

    seed()
    synchronize()
    before = snapshot()
    assert any(e[1] == "active" for e in before.values())
    seed("--touch", "PLAT-101")
    result = synchronize()
    after = snapshot()
    changed = [id_ for id_, value in before.items() if after[id_] != value]
    added = set(after) - set(before)
    assert len(changed) == len(added) == 1
    previous, new = before[changed[0]], after[added.pop()]
    assert after[changed[0]][1] == "superseded"
    assert new[0] == previous[0] and new[1] == "active"
    assert new[2] != previous[2] and new[3] == changed[0]
    assert result["counts"]["evidence_superseded"] == 1
    assert result["counts"]["evidence_added"] == 0
