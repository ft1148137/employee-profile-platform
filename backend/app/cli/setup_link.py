import sys

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.domains.setup.models import SetupPurpose
from app.domains.setup.service import AlreadyInitialized, issue_token


def setup_link(*, base_url: str | None, new_org: bool) -> int:
    purpose = SetupPurpose.NEW_ORG if new_org else SetupPurpose.INITIAL_SETUP
    with Session(engine) as session:
        try:
            token = issue_token(session, purpose)
            session.commit()
        except AlreadyInitialized:
            sys.stderr.write("企业已经初始化，不需要初始化链接\n")
            return 2
    sys.stdout.write(
        f"{(base_url or settings.FRONTEND_HOST).rstrip('/')}/setup#token={token}\n"
    )
    return 0
