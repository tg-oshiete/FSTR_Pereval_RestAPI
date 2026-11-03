"""Local operator only: python -m app.cli issue|revoke. Never log raw tokens."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import secrets
import sys

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import build_engine
from app.models import AccessToken, User
from app.schemas import NormalizedEmail, UserCreate
from app.security import Role, token_digest


def issue_token(db: Session, *, email: str, role: Role, ttl_hours: int = 24,
                **profile) -> dict:
    if not 1 <= ttl_hours <= 8760:
        raise ValueError("ttl_hours must be between 1 and 8760")
    role = Role(role)
    email = str(TypeAdapter(NormalizedEmail).validate_python(email))
    with db.begin():
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            data = UserCreate.model_validate({"email": email, **profile})
            user = User(**data.model_dump())
            db.add(user)
            db.flush()
        else:
            # CLI may provision a new identity, but never silently overwrite one.
            data = UserCreate.model_validate({
                field: profile.get(field, getattr(user, field))
                for field in UserCreate.model_fields
            })
            if any(getattr(user, field) != value for field, value in data.model_dump().items()):
                raise ValueError("Profile conflicts with the existing user")
        raw = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=ttl_hours)
        token = AccessToken(user_id=user.id, role=role.value, token_hash=token_digest(raw),
                            created_at=now, expires_at=expires)
        db.add(token)
        db.flush()
        result = {"token_id": token.id, "user_id": user.id, "role": role.value,
                  "expires_at": expires.isoformat(), "token": raw}
    # Return the secret only after a successful commit.
    return result


def revoke_token(db: Session, token_id: int) -> bool:
    with db.begin():
        result = db.execute(update(AccessToken).where(
            AccessToken.id == token_id,
        ).values(revoked_at=func.coalesce(
            AccessToken.revoked_at, func.now(),
        )))
        return result.rowcount == 1


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", help="Выдать токен; для нового пользователя нужны --fam/--name")
    issue.add_argument("--email", required=True)
    issue.add_argument("--role", choices=list(Role), required=True)
    issue.add_argument("--ttl-hours", type=int, default=24)
    for field in ("fam", "name", "phone", "otc"):
        issue.add_argument(f"--{field}", default=argparse.SUPPRESS)
    revoke = commands.add_parser("revoke", help="Отозвать токен по несекретному token_id")
    revoke.add_argument("--token-id", type=int, required=True)
    return result


def main(argv=None):
    args = vars(parser().parse_args(argv))
    command = args.pop("command")
    engine = None
    try:
        engine = build_engine(get_settings())
        with Session(engine, autoflush=False) as db:
            if command == "issue":
                output = issue_token(db, **args)
            else:
                if args["token_id"] < 1:
                    raise ValueError("token_id must be positive")
                if not revoke_token(db, args["token_id"]):
                    print("Token ID not found", file=sys.stderr)
                    return 1
                output = {"token_id": args["token_id"], "revoked": True}
        print(json.dumps(output))
        return 0
    except (ValidationError, ValueError):
        print("Invalid arguments or conflicting profile; new users require --fam and --name", file=sys.stderr)
        return 1
    except Exception:
        # No DSN, SQL parameters, profile data or secret in diagnostics.
        print("Token operation failed; check database configuration and migrations", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
