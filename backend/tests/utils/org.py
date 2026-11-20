from uuid import UUID

from psycopg import ClientCursor, Connection, Error, sql
from sqlmodel import Session, col, delete, select

from app.domains.access_token.models import (
    McpSetting,
    OAuthGrant,
    OAuthRefreshToken,
    PersonalAccessToken,
)
from app.domains.admin_assistant.models import (
    AdminAssistantMessage,
    AdminAssistantSession,
    AdminChangeSet,
)
from app.domains.audit.models import AuditEvent
from app.domains.bot.models import BotMessage, BotSession, BotSetting
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilityProposal,
    CapabilityProposalEvidence,
    CapabilitySchemaVersion,
    DepartmentCapability,
)
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
    ReviewDecision,
)
from app.domains.drafting.models import DraftingNote, DraftingSetting, UpdateSetting
from app.domains.evidence.models import Evidence
from app.domains.im.models import (
    ImAction,
    ImBindCode,
    ImBinding,
    ImEvent,
    ImNotification,
    ImSetting,
)
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
from app.domains.review.models import ApprovalPolicy, ReviewAppeal, ReviewDelegation
from app.domains.setup.models import SetupStepState
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.work.models import Task, WorkEvent
from app.models import User

ORG_MODELS = (
    ImNotification,
    ReviewDelegation,
    ReviewAppeal,
    ApprovalPolicy,
    ImAction,
    ImEvent,
    ImBindCode,
    ImBinding,
    ImSetting,
    PersonalAccessToken,
    OAuthRefreshToken,
    OAuthGrant,
    McpSetting,
    AdminChangeSet,
    AdminAssistantMessage,
    AdminAssistantSession,
    BotMessage,
    BotSession,
    BotSetting,
    SetupStepState,
    ModelSetting,
    SyncRun,
    DraftingSetting,
    DraftingNote,
    UpdateSetting,
    SourceConnection,
    AuditEvent,
    InboxItem,
    PendingQuestion,
    ChangeItemEvidence,
    ChangeItem,
    ReviewDecision,
    ProfileChangeRequest,
    ClaimEvidence,
    ProfileVersion,
    ProfileClaim,
    ProfileProgress,
    DepartmentCapability,
    CapabilityProposalEvidence,
    CapabilityProposal,
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


def delete_test_organization(session: Session, org_id: UUID, *other_ids: UUID) -> None:
    org_ids = list(dict.fromkeys((org_id, *other_ids)))
    tables = (*ORG_MODELS, Organization)
    deletes = sql.SQL("\n").join(
        sql.SQL("DELETE FROM {} WHERE {} = ANY(target_ids);").format(
            sql.Identifier(str(model.__tablename__)),
            sql.Identifier("id" if model is Organization else "org_id"),
        )
        for model in tables
    )
    # Client binding safely passes the UUID array inside the dollar-quoted block.
    block = sql.SQL("""
        DO $cleanup$
        DECLARE target_ids uuid[] := %s;
        BEGIN
            IF (SELECT count(*) FROM (
                SELECT id FROM organization
                WHERE id = ANY(target_ids) AND slug <> 'demo'
                FOR UPDATE
            ) AS owned) <> cardinality(target_ids) THEN
                RAISE EXCEPTION USING ERRCODE = 'Z0001',
                    MESSAGE = 'Only a test-owned organization may be deleted';
            END IF;
            PERFORM set_config('app.allow_evidence_delete', 'on', true);
            {}
        END
        $cleanup$
    """).format(deletes)
    session.flush()
    connection = session.connection().connection.driver_connection
    assert isinstance(connection, Connection)
    try:
        with ClientCursor(connection) as cursor:
            cursor.execute(block, (org_ids,))
    except Error as exc:
        if exc.sqlstate != "Z0001":
            raise
        session.rollback()
        raise ValueError(str(exc.diag.message_primary)) from exc
    session.commit()
