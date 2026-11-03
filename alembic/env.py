"""Use the same validated settings as the API, without storing a DSN in INI."""

from logging.config import fileConfig

from alembic import context

from app.config import get_settings
from app.database import Base, build_engine
from app import models  # noqa: F401 -- register all model tables


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    # Tests supply only their own disposable connection; settings are not read.
    run_migrations(connection)
else:
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
