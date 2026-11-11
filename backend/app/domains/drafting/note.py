"""Append enterprise drafting context, serializing versions with the org lock."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit, user_label
from app.domains.drafting.models import DraftingNote
from app.domains.org.models import Organization, OrgMembership
from app.models import User


class DraftingNoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2000)


class DraftingNotePublic(DraftingNoteInput):
    version: int | None = None
    created_by: str | None = None
    created_at: datetime | None = None


def latest_note(db: Session, org_id: UUID) -> DraftingNote | None:
    return db.exec(
        select(DraftingNote)
        .where(DraftingNote.org_id == org_id)
        .order_by(col(DraftingNote.version).desc())
        .limit(1)
    ).first()


def read_note(db: Session, org_id: UUID) -> DraftingNotePublic:
    row = latest_note(db, org_id)
    if row is None:
        return DraftingNotePublic(text="")
    user = db.exec(
        select(User)
        .join(OrgMembership, col(OrgMembership.user_id) == col(User.id))
        .where(OrgMembership.org_id == org_id, User.id == row.created_by_user_id)
    ).one()
    return DraftingNotePublic(
        text=row.text,
        version=row.version,
        created_by=user_label(user),
        created_at=row.created_at,
    )


def save_note(
    db: Session, principal: Principal, body: DraftingNoteInput, *, commit: bool = True
) -> DraftingNotePublic:
    db.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    before = latest_note(db, principal.org_id)
    if body.text != (before.text if before else ""):
        assert principal.user_id is not None
        version = before.version + 1 if before else 1
        db.add(
            DraftingNote(
                org_id=principal.org_id,
                version=version,
                text=body.text,
                created_by_user_id=principal.user_id,
            )
        )
        record_audit(
            db,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="drafting_note.updated",
            target_type="drafting_note",
            target_id=str(principal.org_id),
            details={
                "version": version,
                "before": {"text": before.text if before else ""},
                "after": {"text": body.text},
            },
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return read_note(db, principal.org_id)
