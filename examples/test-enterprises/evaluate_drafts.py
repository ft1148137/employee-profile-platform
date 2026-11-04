"""Evaluate synthetic drafts against held-out personas; never import ground truth."""

import argparse
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlmodel import Session, select

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.authz.principal import Principal
from app.connectors.jira.client import jira_client_for
from app.connectors.jira.mock import create_mock_app
from app.core.db import engine
from app.core.secrets import encrypt_secret
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import activate_template
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.drafting.service import read_progress, run_drafting
from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from app.domains.model.schemas import ModelSettingUpdate
from app.domains.org.models import (
    Department,
    Employee,
    ExternalIdentity,
    Organization,
    OrgMembership,
)
from app.domains.source.models import SourceConnection
from app.domains.source.sync import csv_record, jira_record
from app.domains.work.ingest import ingest_record
from app.domains.work.records import parse_work_records
from app.models import User
from tests.utils.org import delete_test_organization

EXAMPLES = Path(__file__).resolve().parents[1]


@contextmanager
def enterprise(
    slug: str,
    model: ModelSettingUpdate | None = None,
) -> Generator[tuple[Session, Organization, dict[str, Employee]]]:
    path = EXAMPLES / ("demo-org" if slug == "demo-org" else f"test-enterprises/{slug}")
    org_data = yaml.safe_load((path / "org.yaml").read_text())
    roster = (
        {
            r["employee_code"]: r
            for r in yaml.safe_load((path / "roster.yaml").read_text())
        }
        if (path / "roster.yaml").exists()
        else {}
    )
    with Session(engine) as db:
        org = Organization(slug=f"eval-{slug}-{uuid4().hex[:8]}", name=org_data["name"])
        db.add(org)
        db.commit()
        user = None
        try:
            if model is not None:
                user = User(
                    username=f"{org.slug}@example.com", hashed_password="unused"
                )
                db.add(user)
                db.flush()
                db.add(OrgMembership(org_id=org.id, user_id=user.id))
                db.flush()
                key = model.api_key.get_secret_value() if model.api_key else None
                db.add(
                    ModelSetting(
                        org_id=org.id,
                        updated_by_user_id=user.id,
                        **model.model_dump(exclude={"api_key"}),
                        api_key_ciphertext=encrypt_secret(key) if key else None,
                    )
                )
            departments = {}
            for name in org_data["departments"]:
                department = Department(org_id=org.id, name=name)
                db.add(department)
                db.flush()
                departments[name] = department.id
            employees = {}
            for row in org_data["employees"]:
                employee = Employee(
                    org_id=org.id,
                    department_id=departments[row["department"]],
                    **{
                        k: row[k]
                        for k in (
                            "employee_code",
                            "display_name",
                            "title",
                            "work_email",
                        )
                    },
                    employment_status="inactive"
                    if roster.get(row["employee_code"], {}).get("status") == "departed"
                    else "active",
                )
                db.add(employee)
                db.flush()
                employees[row["employee_code"]] = employee
            activate_template(
                db,
                Principal.system_worker(org.id),
                "aiot-hardware" if slug == "qiguang-aiot" else "tech-rnd",
            )
            db.commit()
            yield db, org, employees
        finally:
            db.rollback()
            delete_test_organization(db, org.id)
            if user is not None:
                db.delete(user)
                db.commit()


def ingest_dataset(
    db: Session, org: Organization, employees: dict[str, Employee], slug: str
) -> None:
    path = EXAMPLES / "test-enterprises" / slug
    org_data = yaml.safe_load((path / "org.yaml").read_text())
    roster = yaml.safe_load((path / "roster.yaml").read_text())
    for row in org_data["employees"]:
        db.add(
            ExternalIdentity(
                org_id=org.id,
                employee_id=employees[row["employee_code"]].id,
                source="jira",
                external_id=row["account_id"],
            )
        )
    for row in roster:
        for account in row["external_accounts"]:
            db.add(
                ExternalIdentity(
                    org_id=org.id,
                    employee_id=employees[row["employee_code"]].id,
                    source=f"csv:{account['source']}",
                    external_id=account["account_id"],
                )
            )
    db.flush()
    connection = SourceConnection(
        org_id=org.id,
        kind="jira",
        config={
            "deployment": "cloud",
            "base_url": f"http://testserver/{slug}",
            "auth_type": "api_token",
            "username": "jira-admin@example.com",
            "start_date": "2020-01-01",
        },
        secret_ciphertext=encrypt_secret("mock-cloud-token"),
    )
    with (
        TestClient(create_mock_app(EXAMPLES)) as http,
        jira_client_for(connection, http) as jira,
    ):
        page = jira.search(
            datetime.fromisoformat("2020-01-01T00:00:00+00:00"), None, 100
        )
        assert page.next_cursor is None
        records = [jira_record(issue) for issue in page.issues]
    parsed = parse_work_records(
        (path / "work_records.csv").read_bytes(), "work_records.csv"
    )
    assert not parsed.has_errors
    records.extend(csv_record(row) for row in parsed.rows)
    for record in records:
        ingest_record(db, org_id=org.id, record=record, provenance={"evaluation": True})
    db.commit()


def evaluate(slug: str, model: ModelSettingUpdate) -> str:
    with enterprise(slug, model) as (db, org, employees):
        ingest_dataset(db, org, employees, slug)
        run_drafting(Principal.system_worker(org.id), None)
        progress = read_progress(db, org.id)
        if progress.counts["failed"] or progress.counts["pending"]:
            raise ModelUnavailable("模型起草未完成，请检查配置或额度后重试")
        caps = {
            c.id: c.key
            for c in db.exec(
                select(CapabilityDefinition).where(
                    CapabilityDefinition.org_id == org.id
                )
            ).all()
        }
        requests = {
            r.id: r.subject_employee_id
            for r in db.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == org.id
                )
            ).all()
        }
        predicted = {
            (requests[i.request_id], caps[i.capability_definition_id])
            for i in db.exec(
                select(ChangeItem).where(ChangeItem.org_id == org.id)
            ).all()
        }
        personas = yaml.safe_load(
            (EXAMPLES / "test-enterprises" / slug / "personas.yaml").read_text()
        )["personas"]
        truth: dict[str, set[tuple[Any, str]]] = {
            s: set() for s in ("documented", "partial", "undocumented", "misleading")
        }
        for person in personas:
            employee = employees[person["employee_code"]]
            if employee.employment_status != "active":
                continue
            for capability in person["ground_truth"]["capabilities"]:
                truth[capability["evidence"]].add((employee.id, capability["key"]))
        actual = set().union(*truth.values())

        def ratio(n: int, d: int) -> str:
            return f"{n}/{d}（{n / d:.1%}）" if d else "0/0（不适用）"

        lines = ["| 证据状态 | 起草 / 真实能力 |", "| --- | --- |"]
        lines += [
            f"| {status} | {ratio(len(values & predicted), len(values))} |"
            for status, values in truth.items()
        ]
        lines += [
            f"\n额外改动项：{ratio(len(predicted - actual), len(predicted))}；未知比例（undocumented）：{ratio(len(truth['undocumented'] - predicted), len(truth['undocumented']))}。",
            f"\n精确率：{ratio(len(predicted & actual), len(predicted))}；召回率：{ratio(len(predicted & actual), len(actual))}。",
            "\nmisleading 被起草：",
        ]
        codes = {e.id: code for code, e in employees.items()}
        lines += [
            f"- {codes[id_]} / {key}"
            for id_, key in sorted(
                truth["misleading"] & predicted, key=lambda p: (codes[p[0]], p[1])
            )
        ]
        return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", choices=["qiguang-aiot"])
    parser.add_argument("--model", required=True, help="带提供方前缀的模型名")
    parser.add_argument(
        "--kind",
        choices=["cloud_api", "local", "openai_compatible"],
        default="cloud_api",
    )
    parser.add_argument("--base-url")
    args = parser.parse_args()
    config = ModelSettingUpdate(
        enabled=True,
        kind=args.kind,
        model=args.model,
        base_url=args.base_url,
        api_key=os.environ.get("MODEL_API_KEY"),
    )
    print(evaluate(args.slug, config))
