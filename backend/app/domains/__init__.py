"""Register all domain tables with SQLModel metadata."""

from app.domains.account import models as account
from app.domains.audit import models as audit
from app.domains.capability import models as capability
from app.domains.evidence import models as evidence
from app.domains.org import models as org
from app.domains.profile import models as profile
from app.domains.work import models as work

__all__ = ["account", "audit", "capability", "evidence", "org", "profile", "work"]
