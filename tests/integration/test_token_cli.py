"""Exercise the actual local command against an isolated PostgreSQL database."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import issue_token
from app.models import AccessToken, DecisionHistory, User
from app.security import Role, token_digest
from tests.integration.conftest import migrate
from tests.test_regressions import error


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_cli_issue_existing_identity_revoke_and_expiry(pg_engine, pg_client, test_pereval_data, tmp_path):
    url = pg_engine.url
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FSTR_") and key != "PORT"}
    env.update(FSTR_DB_HOST=url.host, FSTR_DB_PORT=str(url.port), FSTR_DB_NAME=url.database,
               FSTR_DB_LOGIN=url.username, FSTR_DB_PASS=url.password, PYTHONPATH=str(ROOT))

    def cli(*args):
        # cwd has no .env; the only database target is this test's new database.
        return subprocess.run([sys.executable, "-m", "app.cli", *args], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=15)

    result = cli("issue", "--email", "cli@example.com", "--role", "moderator", "--fam", "Test", "--name", "Operator", "--ttl-hours", "2")
    assert result.returncode == 0 and result.stderr == ""
    first = json.loads(result.stdout)
    assert len(first["token"]) == 43 and result.stdout.count(first["token"]) == 1
    second_result = cli("issue", "--email", "cli@example.com", "--role", "author")
    assert second_result.returncode == 0 and second_result.stderr == ""
    second = json.loads(second_result.stdout)
    assert first["token"] != second["token"] and first["user_id"] == second["user_id"]
    assert first["role"] == "moderator" and second["role"] == "author"
    with Session(pg_engine) as fresh:
        row = fresh.get(AccessToken, first["token_id"])
        assert row.token_hash == token_digest(first["token"]) and first["token"] not in row.token_hash
        assert row.expires_at - row.created_at == timedelta(hours=2)
        assert row.expires_at == datetime.fromisoformat(first["expires_at"])
        assert row.revoked_at is None
    columns = {column["name"] for column in inspect(pg_engine).get_columns("access_tokens")}
    assert columns == {"id", "user_id", "role", "token_hash", "created_at", "expires_at", "revoked_at"}
    headers = {"Authorization": "Bearer " + first["token"]}
    assert pg_client.get("/moderation/perevals", headers=headers).status_code == 200
    error(pg_client.get("/moderation/perevals", headers={"Authorization": "Bearer " + second["token"]}), 403, "FORBIDDEN")
    for _ in range(2):
        revoked = cli("revoke", "--token-id", str(first["token_id"]))
        assert revoked.returncode == 0 and json.loads(revoked.stdout) == {"token_id": first["token_id"], "revoked": True}
    error(pg_client.get("/moderation/perevals", headers=headers), 401, "UNAUTHORIZED")
    assert cli("revoke", "--token-id", "2147483647").returncode == 1
    for args in [("--ttl-hours", "0"), ("--ttl-hours", "8761"), ("--name", "Conflicting")]:
        failed = cli("issue", "--email", "cli@example.com", "--role", "author", *args)
        assert failed.returncode == 1 and failed.stdout == ""
        assert first["token"] not in failed.stderr and url.password not in failed.stderr
    failed = cli("issue", "--email", "new@example.com", "--role", "author")
    assert failed.returncode == 1 and failed.stdout == ""
    with Session(pg_engine) as fresh:
        assert fresh.get(AccessToken, first["token_id"]).revoked_at is not None
        assert fresh.scalar(select(User).where(User.email == "new@example.com")) is None
        assert fresh.query(AccessToken).count() == 3  # Default author + two CLI grants.


@pytest.mark.parametrize("stage", ["insert", "commit"])
def test_failed_issuance_persists_neither_identity_nor_token(pg_engine, stage):
    def fail(*args):
        raise RuntimeError("Injected issuance failure")

    target, hook = (AccessToken, "after_insert") if stage == "insert" else (Session, "before_commit")
    with Session(pg_engine) as db:
        event.listen(target, hook, fail)
        try:
            with pytest.raises(RuntimeError, match="Injected"):
                issue_token(db, email="failed@example.com", role=Role.AUTHOR, fam="Test", name="Failed")
        finally:
            event.remove(target, hook, fail)
        assert not db.in_transaction()
        with Session(pg_engine) as fresh:
            assert fresh.query(AccessToken).count() == fresh.query(User).count() == 0


def test_0003_upgrade_preserves_legacy_data_without_fabricated_grants_or_history(pg_database):
    migrate(pg_database, "0003")
    with pg_database.begin() as connection:
        connection.execute(text("INSERT INTO users (email,fam,name) VALUES ('legacy@example.com','Test','Legacy')"))
        connection.execute(text("INSERT INTO coords (latitude,longitude,height) VALUES (20,80,3200)"))
        connection.execute(text("INSERT INTO pereval_added (title,user_id,coord_id,add_time,status) VALUES ('Legacy',1,1,now(),'accepted')"))
        before = connection.execute(text("SELECT * FROM pereval_added")).mappings().one()
    migrate(pg_database)
    migrate(pg_database)
    with pg_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM pereval_added")).mappings().one() == before
        assert connection.scalar(text("SELECT count(*) FROM access_tokens")) == 0
        assert connection.scalar(text("SELECT count(*) FROM decision_history")) == 0
    migrate(pg_database, "0003", downgrade=True)
    migrate(pg_database)
    with pg_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM pereval_added")).mappings().one() == before


@pytest.mark.parametrize("field,value", [("role", "admin"), ("role", None), ("user_id", 2147483647),
                                          ("token_hash", "short"), ("expires_at", datetime(2000, 1, 1, tzinfo=timezone.utc))])
def test_token_constraints(pg_engine, pg_identity, field, value):
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            connection.execute(text(f"UPDATE access_tokens SET {field}=:value WHERE id=:id"),
                               {"value": value, "id": pg_identity["token_id"]})


@pytest.mark.parametrize("values", [
    {"old_status": "new", "new_status": "accepted", "reason": None},
    {"old_status": "pending", "new_status": "rejected", "reason": None},
    {"old_status": "pending", "new_status": "rejected", "reason": "   "},
    {"old_status": "new", "new_status": "pending", "actor_role": "author"},
    {"old_status": "new", "new_status": "pending", "actor_user_id": 2147483647},
])
def test_history_constraints(pg_client, pg_engine, pg_identity, test_pereval_data, values):
    item_id = pg_client.post("/submitData/", json=test_pereval_data).json()["id"]
    with pytest.raises(IntegrityError):
        with Session(pg_engine) as db, db.begin():
            db.add(DecisionHistory(**{"pereval_id": item_id, "actor_user_id": pg_identity["user_id"],
                                     "actor_role": "moderator", **values}))
