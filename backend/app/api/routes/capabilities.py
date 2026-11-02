from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capability.schemas import (
    ActiveSchemaPublic,
    CapabilitiesPublic,
    CapabilityPublic,
)
from app.domains.capability.service import get_schema, list_capabilities

router = APIRouter(prefix="/orgs/{org_id}/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilitiesPublic)
def read_capabilities(
    session: SessionDep, principal: OrgPrincipalDep
) -> CapabilitiesPublic:
    schema = get_schema(session, principal.org_id)
    return CapabilitiesPublic(
        schema_version=ActiveSchemaPublic.model_validate(schema),
        data=[
            CapabilityPublic.model_validate(c)
            for c in list_capabilities(session, principal.org_id, schema.id)
        ],
    )
