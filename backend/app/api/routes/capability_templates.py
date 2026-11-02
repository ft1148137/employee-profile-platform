from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.authz.deps import OrgAdminDep
from app.core.config import settings
from app.domains.capability.schemas import (
    ActivateCapabilitySchema,
    ActiveSchemaPublic,
    CapabilityTemplatePreview,
    CapabilityTemplatePublic,
    TemplateCapabilityPublic,
)
from app.domains.capability.service import activate_template
from app.domains.capability.templates import get_template, load_catalog

router = APIRouter(tags=["capability-templates"])


@router.get("/capability-templates", response_model=list[CapabilityTemplatePublic])
def read_templates(_current_user: CurrentUser) -> list[CapabilityTemplatePublic]:
    return [t.public() for t in load_catalog(settings.CAPABILITY_TEMPLATES_DIR)]


@router.get(
    "/capability-templates/{template_id}", response_model=CapabilityTemplatePreview
)
def preview_template(
    template_id: str, _current_user: CurrentUser
) -> CapabilityTemplatePreview:
    template = get_template(template_id)
    return CapabilityTemplatePreview(
        **template.public().model_dump(),
        capabilities=[
            TemplateCapabilityPublic(
                **capability.model_dump(exclude={"level_rubric"}),
                levels=capability.level_rubric.levels,
            )
            for capability in template.capabilities
        ],
    )


@router.post("/orgs/{org_id}/capability-schema", response_model=ActiveSchemaPublic)
def activate_capability_schema(
    body: ActivateCapabilitySchema, session: SessionDep, principal: OrgAdminDep
) -> ActiveSchemaPublic:
    schema = activate_template(session, principal, body.template_id)
    session.commit()
    return ActiveSchemaPublic.model_validate(schema)
