"""Initial schema matching the existing models, without demo data.

Revision ID: 0001
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("phone", sa.String()),
        sa.Column("fam", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("otc", sa.String()),
    )
    op.create_table(
        "coords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
    )
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date_added", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("img", sa.LargeBinary(), nullable=False),
        sa.Column("title", sa.String()),
    )
    op.create_table(
        "pereval_areas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_parent", sa.Integer(), nullable=False),
        sa.Column("title", sa.String()),
    )
    op.create_table(
        "spr_activities_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String()),
    )
    op.create_table(
        "pereval_added",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date_added", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("beauty_title", sa.String()),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("other_titles", sa.String()),
        sa.Column("connect", sa.String()),
        sa.Column("add_time", sa.DateTime(timezone=True)),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("coord_id", sa.Integer(), sa.ForeignKey("coords.id")),
        sa.Column("level_spring", sa.String(2)),
        sa.Column("level_summer", sa.String(2)),
        sa.Column("level_winter", sa.String(2)),
        sa.Column("level_autumn", sa.String(2)),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.CheckConstraint(
            "status IN ('new', 'pending', 'accepted', 'rejected')",
            name="check_status_values",
        ),
    )
    op.create_table(
        "pereval_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_pereval", sa.Integer(), sa.ForeignKey("pereval_added.id", ondelete="CASCADE")),
        sa.Column("id_image", sa.Integer(), sa.ForeignKey("images.id", ondelete="CASCADE")),
    )
    for table in (
        "users", "coords", "images", "pereval_areas", "spr_activities_types",
        "pereval_added", "pereval_images",
    ):
        op.create_index(f"ix_{table}_id", table, ["id"])


def downgrade() -> None:
    for table in (
        "pereval_images", "pereval_added", "spr_activities_types", "pereval_areas",
        "images", "coords", "users",
    ):
        op.drop_index(f"ix_{table}_id", table_name=table)
        op.drop_table(table)
