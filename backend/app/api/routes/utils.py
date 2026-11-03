from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from app.api.deps import SessionDep

router = APIRouter(prefix="/utils", tags=["utils"])


@router.get("/health-check/")
def health_check(session: SessionDep) -> bool:
    try:
        session.exec(select(1))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="数据库暂时不可用")
    return True
