"""Opaque demo credentials and server-owned permissions; no HTTP dependencies."""

from dataclasses import dataclass
from enum import StrEnum
import hashlib


class Role(StrEnum):
    AUTHOR = "author"
    MODERATOR = "moderator"


@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str
    role: Role


class AccessDenied(Exception):
    pass


def token_digest(raw: str) -> str:
    # Tokens have 256 random bits, unlike human passwords: SHA-256 is appropriate.
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def require_role(principal: Principal, role: Role) -> None:
    if principal.role != role:
        raise AccessDenied()


def require_owner(principal: Principal, user_id: int, *, moderator_allowed=True) -> None:
    if moderator_allowed and principal.role == Role.MODERATOR:
        return
    require_role(principal, Role.AUTHOR)
    if principal.user_id != user_id:
        raise AccessDenied()
