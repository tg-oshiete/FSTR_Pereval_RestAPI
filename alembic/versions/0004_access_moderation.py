"""Private demo tokens and atomic decision history.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Existing identities/submissions remain unchanged. No implicit moderator,
    # token or fabricated history is created for legacy decisions.
    op.create_index("ix_pereval_added_status_id", "pereval_added", ["status", "id"])
    op.create_table(
        "access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(9), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('author', 'moderator')", name="ck_access_token_role"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_access_token_hash_length"),
        sa.CheckConstraint("expires_at > created_at", name="ck_access_token_expiry"),
    )
    op.create_table(
        "decision_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pereval_id", sa.Integer(), sa.ForeignKey("pereval_added.id"), nullable=False),
        sa.Column("old_status", sa.String(8), nullable=False),
        sa.Column("new_status", sa.String(8), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_role", sa.String(9), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reason", sa.String(1000)),
        sa.CheckConstraint("actor_role = 'moderator'", name="ck_decision_actor_role"),
        sa.CheckConstraint("(old_status = 'new' AND new_status = 'pending') OR "
                           "(old_status = 'pending' AND new_status IN ('accepted', 'rejected'))",
                           name="ck_decision_transition"),
        sa.CheckConstraint("length(trim(reason)) >= 1 AND length(reason) <= 1000", name="ck_reason_length"),
        sa.CheckConstraint("new_status != 'rejected' OR reason IS NOT NULL", name="ck_rejection_reason"),
    )
    op.create_index("ix_decision_history_pereval_id_id", "decision_history", ["pereval_id", "id"])


def downgrade():
    op.drop_index("ix_decision_history_pereval_id_id", table_name="decision_history")
    op.drop_table("decision_history")
    op.drop_table("access_tokens")
    op.drop_index("ix_pereval_added_status_id", table_name="pereval_added")
