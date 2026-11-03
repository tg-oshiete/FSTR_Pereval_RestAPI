"""Authentication ends its separate read transaction before any route write."""

import re
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import AccessToken, User
from app.security import Principal, Role, require_role, token_digest


bearer = HTTPBearer(auto_error=False, description="Непрозрачный токен, выданный локальной CLI")


def get_auth_db(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_auth_db)],
) -> Principal:
    if credentials is None or not re.fullmatch(r"[A-Za-z0-9_-]{43}", credentials.credentials):
        raise APIError("UNAUTHORIZED")
    # No ORM object or transaction escapes this dependency. The server clock
    # determines expiry; revoked/unknown/expired tokens have the same response.
    row = db.execute(select(User.id, User.email, AccessToken.role).join(
        AccessToken, AccessToken.user_id == User.id,
    ).where(
        AccessToken.token_hash == token_digest(credentials.credentials),
        AccessToken.revoked_at.is_(None), AccessToken.expires_at > func.now(),
    )).one_or_none()
    db.rollback()
    if row is None:
        raise APIError("UNAUTHORIZED")
    return Principal(user_id=row.id, email=row.email, role=Role(row.role))


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def author(principal: CurrentPrincipal) -> Principal:
    require_role(principal, Role.AUTHOR)
    return principal


def moderator(principal: CurrentPrincipal) -> Principal:
    require_role(principal, Role.MODERATOR)
    return principal


Author = Annotated[Principal, Depends(author)]
Moderator = Annotated[Principal, Depends(moderator)]
