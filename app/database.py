"""Database objects are constructed explicitly, never during import."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base

from app.config import Settings


Base = declarative_base()


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        # Email collision recovery re-SELECTs the winning row after SAVEPOINT
        # rollback. Each statement must see the latest committed snapshot.
        settings.database_url, pool_pre_ping=True, hide_parameters=True,
        isolation_level="READ COMMITTED",
    )


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
