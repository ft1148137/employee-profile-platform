import csv
import io
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.audit.service import record_audit
from app.domains.capability.search import (
    PeopleSearchInput,
    PeopleSearchResult,
    find_people,
)
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capability.stats import CapabilityStatsForbidden
from app.domains.org.timezone import read_timezone

router = APIRouter(prefix="/orgs/{org_id}", tags=["people-search"])


@router.get("/people-search", response_model=PeopleSearchResult)
def search_people(
    session: SessionDep,
    principal: OrgPrincipalDep,
    query: Annotated[PeopleSearchInput, Query()],
) -> PeopleSearchResult:
    return find_people(session, principal, query)


class ProfileExportRow(BaseModel):
    employee_code: str | None
    capability_key: str
    capability_name: str
    level: str | None
    level_name: str | None
    status: str
    evidence_count: int
    latest_evidence_at: datetime | None
    published_by: str
    published_at: datetime


@router.get(
    "/profile-export",
    response_model=list[ProfileExportRow],
    responses={
        200: {
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}
        }
    },
)
def export_profiles(
    session: SessionDep,
    principal: OrgPrincipalDep,
    format: Literal["csv", "json"] = "csv",
) -> Response:
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise CapabilityStatsForbidden()
    schema = get_schema(session, principal.org_id)
    rows = []
    for capability in list_capabilities(session, principal.org_id, schema.id):
        skip = 0
        while True:
            page = find_people(
                session,
                principal,
                PeopleSearchInput(capability_key=capability.key, skip=skip, limit=100),
            )
            for row in page.data:
                rows.append(
                    ProfileExportRow(
                        employee_code=row.employee_code,
                        capability_key=row.capability_key,
                        capability_name=row.capability_name,
                        level=row.level,
                        level_name=row.level_name,
                        status=row.status,
                        evidence_count=row.evidence_count,
                        latest_evidence_at=row.latest_evidence_at,
                        published_by=row.published_by.display_name
                        or row.published_by.actor_type,
                        published_at=row.published_at,
                    ).model_dump(mode="json")
                )
            skip += len(page.data)
            if skip >= page.count or not page.data:
                break
    headers = {
        "Content-Disposition": f'attachment; filename="profile-export.{format}"',
        "Cache-Control": "no-store",
    }
    response: Response
    if format == "json":
        response = JSONResponse(rows, headers=headers)
    else:
        timezone = ZoneInfo(read_timezone(session, principal.org_id).timezone)
        status_labels = {
            "confirmed": "已确认",
            "tentative": "暂定",
            "disputed": "存在争议",
        }

        def format_time(value: str | None) -> str:
            return (
                datetime.fromisoformat(value)
                .astimezone(timezone)
                .strftime("%Y-%m-%d %H:%M")
                if value
                else ""
            )

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "工号",
                "能力",
                "等级",
                "判断状态",
                "证据数",
                "最近证据时间",
                "批准人",
                "批准时间",
            ]
        )
        for exported in rows:
            values = [
                exported["employee_code"],
                exported["capability_name"],
                exported["level_name"] or "未定级",
                status_labels[exported["status"]],
                exported["evidence_count"],
                format_time(exported["latest_evidence_at"]),
                exported["published_by"],
                format_time(exported["published_at"]),
            ]
            # Keep user-controlled cells from being interpreted as Excel formulas.
            writer.writerow(
                "'" + value
                if isinstance(value, str) and value.startswith(tuple("=+-@\t\r\n"))
                else value
                for value in values
            )
        response = Response(
            output.getvalue().encode("utf-8-sig"),
            media_type="text/csv",
            headers=headers,
        )
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="profile.exported",
        target_type="organization",
        target_id=str(principal.org_id),
        details={"format": format, "row_count": len(rows), "filters": {}},
    )
    session.commit()
    return response
