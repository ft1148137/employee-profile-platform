from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, col, select

from app.core.config import settings
from app.core.security import get_password_hash
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
)
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.org.models import (
    Department,
    Employee,
    ExternalIdentity,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.profile.models import (
    ClaimEvidence,
    ProfileClaim,
    ProfileProgress,
    ProfileVersion,
)
from app.domains.profile.service import build_profile_snapshot
from app.domains.work.models import Task, WorkEvent
from app.models import User
from app.seed.schema import SeedError, read_bundle


def load_demo(
    session: Session, path: Path, *, allow_non_dev: bool = False
) -> dict[str, Any]:
    """Validate first; commit the complete import once, including its audit record."""
    if settings.FASTAPI_ENV != "development" and not allow_non_dev:
        raise SeedError(
            "Demo import requires FASTAPI_ENV=development or --allow-non-dev"
        )
    if not settings.DEMO_USER_PASSWORD:
        raise SeedError("DEMO_USER_PASSWORD must be configured (8–128 characters)")
    data = read_bundle(path)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"added": 0, "existing": 0})

    def ensure[T: SQLModel](model: type[T], **values: Any) -> T:
        key = values.pop("lookup")
        row = session.exec(select(model).filter_by(**key)).one_or_none()
        counts[str(model.__tablename__)]["existing" if row else "added"] += 1
        if row is None:
            row = model(**key, **values)
            session.add(row)
            session.flush()
        return row

    with session.begin():
        org = ensure(Organization, lookup={"slug": data.org.slug}, name=data.org.name)
        tenant = {"org_id": org.id}
        departments = {
            name: ensure(
                Department,
                lookup={**tenant, "name": name, "parent_department_id": None},
            )
            for name in data.org.departments
        }
        employees = {}
        for employee_row in data.org.employees:
            employee = ensure(
                Employee,
                lookup={**tenant, "employee_code": employee_row.employee_code},
                department_id=departments[employee_row.department].id,
                **employee_row.model_dump(
                    include={"display_name", "title", "work_email"}
                ),
            )
            employees[employee_row.employee_code] = employee
            ensure(
                ExternalIdentity,
                lookup={
                    **tenant,
                    "source": "jira",
                    "external_id": employee_row.account_id,
                },
                employee_id=employee.id,
                display_name=employee_row.display_name,
                linked_by="seed",
                linked_at=datetime.now(UTC),
            )
        accounts = {a.email: a for a in data.org.accounts}
        users = {}
        account_emails = list(accounts)
        if settings.FIRST_SUPERUSER and settings.FIRST_SUPERUSER not in accounts:
            account_emails.append(str(settings.FIRST_SUPERUSER))
        for email in account_emails:
            user = session.exec(select(User).where(User.email == email)).one_or_none()
            if user is None and email == settings.FIRST_SUPERUSER:
                raise SeedError("Run initial_data.py before loading demo data")
            account = accounts.get(email)
            account_employee = (
                employees[account.employee_code]
                if account and account.employee_code
                else None
            )
            if user is None and account_employee is not None:
                user = session.exec(
                    select(User)
                    .join(OrgMembership)
                    .where(
                        OrgMembership.org_id == org.id,
                        OrgMembership.employee_id == account_employee.id,
                    )
                ).one_or_none()
            counts["user"]["existing" if user else "added"] += 1
            username = email.strip().lower()
            if user is None:
                user = User(
                    username=username,
                    email=email,
                    full_name=account_employee.display_name
                    if account_employee
                    else "企业管理员",
                    hashed_password=get_password_hash(settings.DEMO_USER_PASSWORD),
                )
                session.add(user)
                session.flush()
            if not user.full_name:
                user.full_name = (
                    account_employee.display_name if account_employee else "企业管理员"
                )
                session.add(user)
            users[email] = user
            membership = ensure(
                OrgMembership,
                lookup={**tenant, "user_id": user.id},
                employee_id=account_employee.id if account_employee else None,
            )
            role = account.role if account else Role.ORG_ADMIN
            if email == settings.FIRST_SUPERUSER:
                role = Role.ORG_ADMIN
            if role:
                ensure(
                    RoleGrant,
                    lookup={
                        **tenant,
                        "membership_id": membership.id,
                        "role": role,
                        "department_id": account_employee.department_id
                        if account_employee and role == Role.DEPT_MANAGER
                        else None,
                    },
                )
        schema = ensure(
            CapabilitySchemaVersion,
            lookup={**tenant, "version": 1},
            status=SchemaStatus.ACTIVE,
            template_ref="tech-rnd.zh-CN@1",
            activated_at=datetime.now(UTC),
        )
        if schema.activated_at is None:
            schema.activated_at = datetime.now(UTC)
            session.add(schema)
        capabilities = {
            row.key: ensure(
                CapabilityDefinition,
                lookup={**tenant, "schema_version_id": schema.id, "key": row.key},
                position=position,
                **row.model_dump(exclude={"key"}),
            )
            for position, row in enumerate(data.capabilities)
        }
        evidence_by_ref = {}

        def evidence(
            *,
            source: str,
            source_ref: str,
            employee: Employee,
            filename: str,
            **values: Any,
        ) -> None:
            record, added = append_evidence(
                session,
                org_id=org.id,
                subject_employee_id=employee.id,
                source=source,
                source_ref=source_ref,
                provenance={
                    "ingested_by": "seed",
                    "seed_file": filename,
                    "seed_format_version": 1,
                    **values.pop("provenance", {}),
                },
                **values,
            )
            counts["evidence"]["added" if added else "existing"] += 1
            evidence_by_ref[f"{source}:{source_ref}"] = record

        skipped = []
        for issue in data.jira.issues:
            fields = issue.fields
            identity = session.exec(
                select(ExternalIdentity).where(
                    ExternalIdentity.org_id == org.id,
                    ExternalIdentity.source == "jira",
                    ExternalIdentity.external_id == fields.assignee.accountId,
                )
            ).one_or_none()
            if identity is None or identity.employee_id is None:
                skipped.append(issue.key)
                continue
            employee = session.exec(
                select(Employee).where(
                    Employee.org_id == org.id, Employee.id == identity.employee_id
                )
            ).one()
            task = ensure(
                Task,
                lookup={**tenant, "source": "jira", "external_key": issue.key},
                title=fields.summary,
                task_type=fields.issuetype.name,
                status=fields.status.name,
                url=f"https://jira.demo.example.com/browse/{issue.key}",
                source_created_at=fields.created,
                attributes=fields.model_dump(
                    mode="json", include={"labels", "components"}
                ),
            )
            event = ensure(
                WorkEvent,
                lookup={**tenant, "source": "jira", "source_ref": issue.key},
                actor_employee_id=employee.id,
                task_id=task.id,
                action="resolved" if fields.resolutiondate else "assignee",
                occurred_at=fields.resolutiondate or fields.created,
            )
            evidence(
                source="jira",
                source_ref=issue.key,
                employee=employee,
                filename="jira/issues.json",
                kind=EvidenceKind.WORK_RECORD,
                title=fields.summary,
                content=fields.model_dump(mode="json"),
                visibility=Visibility.STANDARD,
                occurred_at=event.occurred_at,
                event_id=event.id,
            )
        for report in data.self_reports:
            evidence(
                source="self_report",
                source_ref=report.id,
                employee=employees[report.employee_code],
                filename="self_reports.yaml",
                kind=EvidenceKind.SELF_REPORT,
                title="员工自述",
                content=report.model_dump(mode="json"),
                visibility=Visibility.RESTRICTED,
                occurred_at=report.submitted_at,
            )
        for review in data.manager_reviews:
            evidence(
                source="manager_review",
                source_ref=review.id,
                employee=employees[review.employee_code],
                filename="manager_reviews.yaml",
                kind=EvidenceKind.MANAGER_REVIEW,
                title=review.question,
                content=review.model_dump(mode="json"),
                visibility=Visibility.RESTRICTED,
                occurred_at=review.reviewed_at,
                provenance={
                    "reviewer_employee_id": str(
                        employees[review.reviewer_employee_code].id
                    )
                },
            )
        claims: dict[str, ProfileClaim] = {}
        for claim_row in data.claims:
            claim = session.exec(
                select(ProfileClaim).where(
                    ProfileClaim.org_id == org.id,
                    col(ProfileClaim.generated_by)["seed_ref"].astext == claim_row.id,
                )
            ).one_or_none()
            counts["profile_claim"]["existing" if claim else "added"] += 1
            if claim is None:
                claim = ProfileClaim(
                    org_id=org.id,
                    subject_employee_id=employees[claim_row.employee_code].id,
                    capability_definition_id=capabilities[claim_row.capability_key].id,
                    generated_by={"actor_type": "seed", "seed_ref": claim_row.id},
                    supersedes_claim_id=claims[claim_row.supersedes].id
                    if claim_row.supersedes
                    else None,
                    **claim_row.model_dump(
                        exclude={
                            "id",
                            "employee_code",
                            "capability_key",
                            "supersedes",
                            "evidence",
                        }
                    ),
                )
                session.add(claim)
                session.flush()
            claims[claim_row.id] = claim
            for ref in claim_row.evidence:
                ensure(
                    ClaimEvidence,
                    lookup={
                        **tenant,
                        "claim_id": claim.id,
                        "evidence_id": evidence_by_ref[ref.ref].id,
                    },
                    relation=ref.relation,
                )
        versions: dict[tuple[str, int], ProfileVersion] = {}
        for version_row in data.profile_versions:
            employee_id = employees[version_row.employee_code].id
            version = ensure(
                ProfileVersion,
                lookup={
                    **tenant,
                    "subject_employee_id": employee_id,
                    "revision": version_row.revision,
                },
                schema_version_id=schema.id,
                published_at=version_row.published_at,
                published_by={
                    "actor_type": "user",
                    "user_id": str(users[version_row.published_by].id),
                },
                supersedes_version_id=versions[
                    (version_row.employee_code, version_row.revision - 1)
                ].id
                if version_row.revision > 1
                else None,
                snapshot=build_profile_snapshot(
                    session,
                    org_id=org.id,
                    employee_id=employee_id,
                    claim_ids=[claims[c].id for c in version_row.claims],
                ),
            )
            versions[(version_row.employee_code, version_row.revision)] = version
        for request_row in data.change_requests:
            request = session.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == org.id,
                    ProfileChangeRequest.origin == request_row.origin,
                    col(ProfileChangeRequest.generated_by)["seed_ref"].astext
                    == request_row.id,
                )
            ).one_or_none()
            counts["profile_change_request"]["existing" if request else "added"] += 1
            if request is None:
                request = ProfileChangeRequest(
                    org_id=org.id,
                    subject_employee_id=employees[request_row.employee_code].id,
                    origin=request_row.origin,
                    status=request_row.status,
                    author={"actor_type": "seed"},
                    generated_by={"actor_type": "seed", "seed_ref": request_row.id},
                )
                session.add(request)
                session.flush()
            for position, item_row in enumerate(request_row.items):
                item = ensure(
                    ChangeItem,
                    lookup={**tenant, "request_id": request.id, "position": position},
                    capability_definition_id=capabilities[item_row.capability_key].id,
                    target_claim_id=claims[item_row.target_claim].id
                    if item_row.target_claim
                    else None,
                    **item_row.model_dump(
                        mode="json",
                        exclude={"capability_key", "target_claim", "evidence"},
                    ),
                )
                for ref in item_row.evidence:
                    ensure(
                        ChangeItemEvidence,
                        lookup={
                            **tenant,
                            "item_id": item.id,
                            "evidence_id": evidence_by_ref[ref.ref].id,
                        },
                        relation=ref.relation,
                    )
        for progress_row in data.progress:
            ensure(
                ProfileProgress,
                lookup={
                    **tenant,
                    "employee_id": employees[progress_row.employee_code].id,
                },
                **progress_row.model_dump(exclude={"employee_code"}),
            )
        # One receipt per immutable seed bundle keeps a repeated import row-idempotent.
        ensure(
            AuditEvent,
            lookup={**tenant, "actor_type": ActorKind.SEED, "action": "seed.loaded"},
            target_type="organization",
            target_id=str(org.id),
            details=dict(counts),
        )
    return {"counts": dict(counts), "skipped_jira_issues": skipped}
