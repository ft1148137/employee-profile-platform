from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, col, delete, select

from app.domains.access_token.models import (
    McpSetting,
    OAuthGrant,
    OAuthRefreshToken,
    PersonalAccessToken,
)
from app.domains.audit.models import AuditEvent
from app.domains.bot.models import BotMessage, BotSession, BotSetting
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
)
from app.domains.drafting.models import DraftingSetting
from app.domains.evidence.models import Evidence
from app.domains.inbox.models import InboxItem, PendingQuestion
from app.domains.model.models import ModelSetting
from app.domains.org.models import (
    Department,
    Employee,
    ExternalIdentity,
    Organization,
    OrgMembership,
    ReportingLine,
    RoleGrant,
    RosterImport,
)
from app.domains.profile.models import (
    ClaimEvidence,
    ProfileClaim,
    ProfileProgress,
    ProfileVersion,
)
from app.domains.setup.models import SetupStepState
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.work.models import Task, WorkEvent
from app.models import User

ORG_MODELS = (
    PersonalAccessToken,
    OAuthRefreshToken,
    OAuthGrant,
    McpSetting,
    BotMessage,
    BotSession,
    BotSetting,
    SetupStepState,
    ModelSetting,
    SyncRun,
    DraftingSetting,
    SourceConnection,
    AuditEvent,
    InboxItem,
    PendingQuestion,
    ChangeItemEvidence,
    ChangeItem,
    ProfileChangeRequest,
    ClaimEvidence,
    ProfileVersion,
    ProfileClaim,
    ProfileProgress,
    Evidence,
    WorkEvent,
    Task,
    CapabilityDefinition,
    CapabilitySchemaVersion,
    RoleGrant,
    ReportingLine,
    RosterImport,
    OrgMembership,
    ExternalIdentity,
    Employee,
    Department,
)


def cleanup_new_users(session: Session, existing_user_ids: set[UUID]) -> None:
    session.execute(
        delete(User).where(
            col(User.id).not_in(existing_user_ids),
            ~select(OrgMembership.id).where(OrgMembership.user_id == User.id).exists(),
            ~select(RoleGrant.id)
            .where(RoleGrant.granted_by_user_id == User.id)
            .exists(),
        )
    )


def delete_test_organization(session: Session, org_id: UUID) -> None:
    org = session.get(Organization, org_id)
    if org is None or org.slug == "demo":
        raise ValueError("Only a test-owned organization may be deleted")
    for model in ORG_MODELS:
        if model is Evidence:
            session.execute(text("SET LOCAL app.allow_evidence_delete = 'on'"))
        session.execute(delete(model).where(model.org_id == org_id))
    session.delete(org)
    session.commit()
