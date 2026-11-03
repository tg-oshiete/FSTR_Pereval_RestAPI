import pytest
from dataclasses import asdict
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.database import get_db
from app.validation import DEFAULT_LIMITS


@pytest.fixture
def test_settings():
    # Explicit values ignore the user's .env and FSTR_* environment variables.
    return Settings(
        _env_file=None,
        db_host="unused.invalid",
        db_port=5432,
        db_name="test_only",
        db_login="test_only",
        db_pass="test-only-placeholder",
        PORT=8000,
        **asdict(DEFAULT_LIMITS),
    )


@pytest.fixture
def test_app(test_settings):
    application = create_app(test_settings)

    def override_get_db():
        # Match production: each HTTP request owns a fresh session, including reads.
        with Session(application.state.test_db.get_bind(), autoflush=False) as session:
            yield session

    # Registered before either the test engine or the application's lifespan.
    application.dependency_overrides[get_db] = override_get_db
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest.fixture(scope="function")
def db(test_app):
    # Each test owns a new database, including commits performed by CRUD code.
    engine = create_engine(
        URL.create("sqlite"),
        # Python 3.13's non-legacy mode emits BEGIN before SAVEPOINT/SELECT.
        # Otherwise SQLite can release the first savepoint as a separate commit.
        connect_args={"check_same_thread": False, "autocommit": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with Session(bind=engine, autoflush=False) as session:
            test_app.state.test_db = session
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def client(test_app, db):
    try:
        with TestClient(test_app) as test_client:
            yield test_client
    finally:
        test_app.dependency_overrides.clear()


@pytest.fixture
def test_pereval_data():
    return {
        "title": "Тестовый перевал",
        "user": {
            "email": "string@example.com",
            "fam": "Иванов",
            "name": "Иван"
        },
        "coords": {
            "latitude": 20.0,
            "longitude": 80.0,
            "height": 3200
        },
        "level": {}
    }
