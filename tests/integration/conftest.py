"""Only new, disposable PostgreSQL databases; no .env or external test DSN."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.main import create_app
from app.validation import DEFAULT_LIMITS


ROOT = Path(__file__).resolve().parents[2]


def migrate(engine, revision="head", *, downgrade=False):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture(scope="session")
def postgres_server():
    name = "fstr-integration-" + secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("FSTR_", "POSTGRES_"))}
    env.update(POSTGRES_USER="integration", POSTGRES_DB="postgres", POSTGRES_PASSWORD=password)

    def docker(*args, check=True):
        result = subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=90)
        if check and result.returncode:
            pytest.fail("Disposable PostgreSQL Docker operation failed; check Docker availability", pytrace=False)
        return result

    container = None
    try:
        # Docker refuses an existing name; never inspect/adopt another container.
        container = docker("run", "--detach", "--rm", "--name", name,
                           "--tmpfs", "/var/lib/postgresql", "--publish", "127.0.0.1::5432",
                           "--env", "POSTGRES_USER", "--env", "POSTGRES_DB", "--env", "POSTGRES_PASSWORD",
                           "postgres:18-alpine").stdout.strip()
        deadline = time.monotonic() + 60
        while docker("exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "integration",
                     "-d", "postgres", check=False).returncode:
            if time.monotonic() >= deadline:
                pytest.fail("Disposable PostgreSQL TCP readiness timed out", pytrace=False)
            time.sleep(0.25)
        info = json.loads(docker("inspect", container).stdout)[0]
        port = int(info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
        yield URL.create("postgresql+psycopg2", username="integration", password=password,
                         host="127.0.0.1", port=port, database="postgres")
    finally:
        if container:
            docker("rm", "--force", container, check=False)


@pytest.fixture
def pg_database(postgres_server):
    database = "test_" + secrets.token_hex(8)
    admin = create_engine(postgres_server, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        engine = create_engine(postgres_server.set(database=database), hide_parameters=True,
                               isolation_level="READ COMMITTED",
                               connect_args={"options": "-c statement_timeout=15000 -c lock_timeout=10000"})
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def pg_engine(pg_database):
    migrate(pg_database)
    return pg_database


@pytest.fixture
def pg_client(pg_engine):
    url = pg_engine.url
    settings = Settings(_env_file=None, db_host=url.host, db_port=url.port, db_name=url.database,
                        db_login=url.username, db_pass=url.password, PORT=8000, **asdict(DEFAULT_LIMITS))
    app = create_app(settings)

    def database():
        with Session(pg_engine, autoflush=False) as session:
            yield session

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
