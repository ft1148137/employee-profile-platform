"""Publish a dictionary and its optional drafting job in one transaction."""

from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.capability.schemas import ActiveSchemaPublic, SaveDictionary
from app.domains.capability.service import save_version
from app.domains.drafting.service import queue_drafting


def save_dictionary(
    session: Session,
    principal: Principal,
    body: SaveDictionary,
    *,
    commit: bool = True,
) -> ActiveSchemaPublic:
    schema = save_version(
        session,
        principal,
        body.definitions,
        expected_version=body.expected_version,
    )
    result = ActiveSchemaPublic.model_validate(schema)
    if body.redraft:
        queue_drafting(
            session, principal.org_id, "all", pending_owner_only=True, commit=False
        )
    if commit:
        session.commit()
    return result
