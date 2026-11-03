"""Allow the API's canonical n/k without narrowing or rewriting existing values.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
SEASONS = ("spring", "summer", "winter", "autumn")


def resize(old, new):
    # PostgreSQL alters these columns in place. SQLite is only a disposable test DB.
    with op.batch_alter_table("pereval_added") as batch:
        for season in SEASONS:
            batch.alter_column(f"level_{season}", existing_type=sa.String(old),
                               type_=sa.String(new), existing_nullable=True)


def upgrade() -> None:
    resize(2, 3)


def downgrade() -> None:
    table = sa.table("pereval_added", *(sa.column(f"level_{season}", sa.String()) for season in SEASONS))
    too_long = sa.or_(*(sa.func.length(column) > 2 for column in table.c))
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(table).where(too_long)):
        raise RuntimeError("Cannot narrow seasons: values longer than two characters exist")
    resize(3, 2)
