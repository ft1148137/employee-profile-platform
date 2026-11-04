from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.evidence.models import Evidence
from app.domains.inbox import service
from app.domains.inbox.models import InboxItem, PendingQuestion, QuestionStatus
from app.domains.org.models import Employee, OrgMembership
from tests.api.routes.test_employee_requests import principal_for


def test_answer_is_evidence_only(demo: tuple, client: TestClient, db: Session) -> None:
    url, employees, auth = demo
    headers = auth["employee"]
    base = f"{url}/me"
    before_profile = client.get(f"{base}/profile", headers=headers).json()
    before_inbox = client.get(f"{base}/inbox?limit=1", headers=headers).json()
    questions = client.get(f"{base}/questions?limit=1", headers=headers).json()
    assert questions["count"] == 2 and len(questions["data"]) == 1
    question = questions["data"][0]
    path = f"{base}/questions/{question['id']}"
    assert client.get(path, headers=headers).json() == question
    assert (
        client.post(
            f"{path}/answer", headers=headers, json={"answer": "   "}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{path}/answer",
            headers=headers,
            json={"answer": "x", "employee_id": str(employees["E03"].id)},
        ).status_code
        == 422
    )
    response = client.post(
        f"{path}/answer", headers=headers, json={"answer": " 本人确认的合成经历 "}
    )
    assert response.status_code == 200, response.text
    answered = response.json()
    assert answered["status"] == "answered" and answered["answered_at"]
    evidence = db.get(Evidence, UUID(answered["answer_evidence_id"]))
    assert evidence.subject_employee_id == employees["E02"].id
    assert evidence.org_id == employees["E02"].org_id
    assert evidence.kind == "self_report" and evidence.visibility == "restricted"
    assert (
        evidence.source == "self_report:question"
        and evidence.source_ref == question["id"]
    )
    assert evidence.content == {
        "question": question["question"],
        "text": "本人确认的合成经历",
    }
    assert client.get(f"{base}/questions", headers=headers).json()["count"] == 1
    inbox = client.get(f"{base}/inbox", headers=headers).json()
    notification = next(i for i in inbox["data"] if i["question_id"] == question["id"])
    assert notification["done_at"] and notification["read_at"]
    assert inbox["unread_count"] == before_inbox["unread_count"] - 1
    assert inbox["pending_count"] == before_inbox["pending_count"] - 1
    assert (
        client.post(
            f"{path}/answer", headers=headers, json={"answer": "不能覆盖原回答"}
        ).status_code
        == 409
    )
    db.expire_all()
    assert evidence.content["text"] == "本人确认的合成经历"
    assert client.get(f"{base}/profile", headers=headers).json() == before_profile
    assert (
        client.get(f"{url}/evidence/{evidence.id}", headers=auth["admin"]).json()[
            "content"
        ]
        is None
    )
    # Reading a todo does not complete it or send the employee away from their inbox.
    remaining = next(i for i in inbox["data"] if i["question_id"] and not i["done_at"])
    read = client.post(f"{base}/inbox/{remaining['id']}/read", headers=headers).json()
    assert read["read_at"] and read["done_at"] is None
    assert (
        client.get(f"{base}/inbox", headers=headers).json()["pending_count"]
        == inbox["pending_count"]
    )


def test_only_target_can_read_or_answer(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    question = client.get(f"{url}/me/questions", headers=auth["employee"]).json()[
        "data"
    ][0]
    for role in ("manager", "admin", "foreign"):
        path = f"{url}/me/questions/{question['id']}"
        assert client.get(path, headers=auth[role]).status_code == 404
        assert (
            client.post(
                f"{path}/answer", headers=auth[role], json={"answer": "冒用"}
            ).status_code
            == 404
        )
    # Give an existing foreign tenant an employee; the tenant predicate still rejects this question.
    user_id = UUID(
        jwt.decode(
            auth["foreign"]["Authorization"].removeprefix("Bearer "),
            options={"verify_signature": False},
        )["sub"]
    )
    membership = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == user_id)
    ).one()
    foreign_employee = Employee(
        org_id=membership.org_id, display_name="Synthetic foreign employee"
    )
    db.add(foreign_employee)
    db.flush()
    membership.employee_id = foreign_employee.id
    db.commit()
    foreign_path = f"/api/v1/orgs/{membership.org_id}/me/questions/{question['id']}"
    assert client.get(foreign_path, headers=auth["foreign"]).status_code == 404
    assert (
        client.post(
            f"{foreign_path}/answer", headers=auth["foreign"], json={"answer": "跨企业"}
        ).status_code
        == 404
    )
    other = PendingQuestion(
        org_id=employees["E03"].org_id,
        target_employee_id=employees["E03"].id,
        question="别人的问题",
        reason="合成",
    )
    db.add(other)
    db.commit()
    assert (
        client.get(
            f"{url}/me/questions/{other.id}", headers=auth["employee"]
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{url}/me/questions/{other.id}/answer",
            headers=auth["employee"],
            json={"answer": "冒用"},
        ).status_code
        == 404
    )
    assert other.id not in {
        UUID(q["id"])
        for q in client.get(f"{url}/me/questions", headers=auth["employee"]).json()[
            "data"
        ]
    }


def test_answer_once_under_concurrency(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    question = client.get(f"{url}/me/questions", headers=auth["employee"]).json()[
        "data"
    ][0]

    def send(_: int) -> int:
        return client.post(
            f"{url}/me/questions/{question['id']}/answer",
            headers=auth["employee"],
            json={"answer": "并发合成回答"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == [200, 409]
    assert (
        len(
            db.exec(
                select(Evidence).where(
                    Evidence.org_id == employees["E02"].org_id,
                    Evidence.source_ref == question["id"],
                )
            ).all()
        )
        == 1
    )


def test_answer_rollback_and_closed_question(
    demo: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, employees, auth = demo
    question = client.get(f"{url}/me/questions", headers=auth["employee"]).json()[
        "data"
    ][0]

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(service, "record_audit", fail)
        with pytest.raises(RuntimeError, match="Synthetic"):
            client.post(
                f"{url}/me/questions/{question['id']}/answer",
                headers=auth["employee"],
                json={"answer": "应回滚"},
            )
    assert not db.exec(
        select(Evidence).where(
            Evidence.org_id == employees["E02"].org_id,
            Evidence.source_ref == question["id"],
        )
    ).all()
    row = db.get(PendingQuestion, UUID(question["id"]))
    assert row.status == QuestionStatus.OPEN and row.answer_evidence_id is None
    inbox = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == row.org_id, InboxItem.question_id == row.id
        )
    ).one()
    assert inbox.read_at is None and inbox.done_at is None
    row.status = QuestionStatus.DISMISSED
    db.commit()
    assert (
        client.post(
            f"{url}/me/questions/{row.id}/answer",
            headers=auth["employee"],
            json={"answer": "已关闭"},
        ).status_code
        == 409
    )


def test_question_and_inbox_queries_are_fixed(shared_demo: tuple, db: Session) -> None:
    _, employees, _ = shared_demo
    principal = principal_for(db, employees["E02"].org_id)
    queries: list[str] = []

    def count(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        for limit in (1, 100):
            queries.clear()
            service.get_pending_questions(principal, db, limit=limit)
            assert len(queries) == 2
            queries.clear()
            result = service.my_inbox(principal, db, limit=limit)
            assert len(queries) == 2
            assert result.count >= 3 and result.pending_count >= 3
    finally:
        event.remove(engine, "before_cursor_execute", count)
