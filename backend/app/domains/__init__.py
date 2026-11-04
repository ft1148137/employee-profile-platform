"""Register all domain tables with SQLModel metadata."""

from app.domains.access_token import models as access_token
from app.domains.account import models as account
from app.domains.audit import models as audit
from app.domains.capability import models as capability
from app.domains.change_request import models as change_request
from app.domains.drafting import models as drafting
from app.domains.evidence import models as evidence
from app.domains.inbox import models as inbox
from app.domains.model import models as model
from app.domains.org import models as org
from app.domains.profile import models as profile
from app.domains.setup import models as setup
from app.domains.source import models as source
from app.domains.work import models as work

__all__ = [
    "access_token",
    "account",
    "audit",
    "capability",
    "change_request",
    "drafting",
    "evidence",
    "inbox",
    "model",
    "org",
    "profile",
    "setup",
    "source",
    "work",
]
