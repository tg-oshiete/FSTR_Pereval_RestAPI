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
        # A waiting FOR UPDATE must see the competing writer's committed row
        # before checking expected_status or whether content remains editable.
        settings.database_url, pool_pre_ping=True, hide_parameters=True,
        isolation_level="READ COMMITTED",
    )


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
