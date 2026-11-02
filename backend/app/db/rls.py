import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def set_rls_context(
    session: Session,
    *,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> None:
    
    if user_id is not None:
        session.execute(
            text("select set_config('app.current_user_id', :v, true)"),
            {"v": str(user_id)},
        )
    if tenant_id is not None:
        session.execute(
            text("select set_config('app.current_tenant', :v, true)"),
            {"v": str(tenant_id)},
        )