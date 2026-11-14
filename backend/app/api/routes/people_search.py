from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capability.search import (
    PeopleSearchInput,
    PeopleSearchResult,
    find_people,
)

router = APIRouter(prefix="/orgs/{org_id}/people-search", tags=["people-search"])


@router.get("", response_model=PeopleSearchResult)
def search_people(
    session: SessionDep,
    principal: OrgPrincipalDep,
    query: Annotated[PeopleSearchInput, Query()],
) -> PeopleSearchResult:
    return find_people(session, principal, query)
