"""Regression checks for startup, safe settings and disposable test databases."""

import os
from pathlib import Path
import subprocess
import sys

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import Base
from app.models import User


ROOT = Path(__file__).resolve().parents[1]


def test_import_and_collection_modules_do_not_initialize_database(tmp_path):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("FSTR_") and key != "PORT"
    }
    env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    # Reading this file at import time would fail validation.
    (tmp_path / ".env").write_text("FSTR_DB_PORT=invalid\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-c", """
from sqlalchemy import event
from sqlalchemy.engine import Engine
def unexpected_connection(*args):
    raise AssertionError('Import opened a database connection')
event.listen(Engine, 'engine_connect', unexpected_connection)
import app.main
import tests.conftest
import app.database
assert not any(isinstance(value, Engine) for value in vars(app.database).values())
"""],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout == ""
    assert result.stderr == ""


def test_password_special_characters_and_safe_representation(test_settings):
    secret = "test-only:@/#+?%$value"
    values = test_settings.model_dump()
    values["db_pass"] = secret
    settings = Settings(_env_file=None, **values)
    assert settings.database_url.password == secret
    assert make_url(settings.database_url.render_as_string(hide_password=False)).password == secret
    assert secret not in repr(settings)
    assert secret not in str(settings.database_url)


@pytest.mark.parametrize("port", [0, 65536, "not-a-port"])
def test_invalid_settings_hide_input(test_settings, port):
    values = test_settings.model_dump()
    values["db_port"] = port
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **values)
    assert "input_value" not in str(error.value)


def test_health_and_docs_do_not_use_database(test_app):
    # No db fixture: using get_db would access a missing test_db attribute.
    with TestClient(test_app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
        assert not hasattr(test_app.state, "session_factory")


@pytest.mark.parametrize("iteration", [1, 2])
def test_each_test_owns_its_committed_data(db, iteration):
    assert db.query(User).count() == 0
    user = User(email="isolated@example.com", fam="Test", name="Test")
    db.add(user)
    db.commit()
    assert user.id == 1


def test_initial_migration_matches_models_and_can_repeat(db):
    db.execute(text("INSERT INTO users (email, fam, name) VALUES ('repeat@example.com', 'Test', 'Test')"))
    db.commit()
    config = Config(str(ROOT / "alembic.ini"))
    with db.get_bind().begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(config).get_current_head()
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
