"""Real issued tokens for tests; authentication dependencies are never bypassed."""

from sqlalchemy.orm import Session

from app.cli import issue_token
from app.security import Principal, Role


def provision(engine, profile, role=Role.AUTHOR):
    with Session(engine, autoflush=False) as session:
        credential = issue_token(session, role=role, **profile)
    credential["principal"] = Principal(credential["user_id"], profile["email"], role)
    credential["headers"] = {"Authorization": "Bearer " + credential["token"]}
    return credential
