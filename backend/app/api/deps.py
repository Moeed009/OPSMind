import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AuthError, AuthUser, decode_token
from app.db.enums import Role
from app.db.models import Membership, User
from app.db.rls import set_rls_context
from app.db.session import get_session_factory

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthUser:
    if credentials is None:
        raise _unauthorized()
    try:
        return decode_token(credentials.credentials)
    except AuthError:
        raise _unauthorized() from None


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def get_user_db(user: CurrentUser) -> Iterator[Session]:
   
    with get_session_factory()() as session, session.begin():
        set_rls_context(session, user_id=user.id)
        yield session


UserDb = Annotated[Session, Depends(get_user_db, scope="function")]


@dataclass(frozen=True)
class TenantContext:
    user: AuthUser
    tenant_id: uuid.UUID
    role: Role
    db: Session


def get_tenant_context(
    user: CurrentUser,
    db: UserDb,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TenantContext:
    if not x_tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Tenant-ID header is required")
    try:
        tenant_id = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Tenant-ID must be a valid UUID"
        ) from None

   
    row = db.execute(
        select(Membership.role, User.is_active)
        .join(User, User.id == Membership.user_id)
        .where(Membership.user_id == user.id, Membership.tenant_id == tenant_id)
    ).one_or_none()
    if row is None or not row.is_active:
        
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this tenant")

    set_rls_context(db, tenant_id=tenant_id)
    return TenantContext(user=user, tenant_id=tenant_id, role=row.role, db=db)


TenantCtx = Annotated[TenantContext, Depends(get_tenant_context)]