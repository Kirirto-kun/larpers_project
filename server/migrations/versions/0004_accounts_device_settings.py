"""Durable member accounts and independently polled device settings."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(256), nullable=True))
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    for column in (
        sa.Column("settings_revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("contribution_mode", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("desired_state", sa.String(16), nullable=False, server_default="running"),
        sa.Column("applied_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("actual_state", sa.String(16), nullable=True),
        sa.Column("applied_mode", sa.String(16), nullable=True),
        sa.Column("settings_error", sa.String(512), nullable=True),
        sa.Column("agent_contact_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("nodes", column)
    op.execute(
        "UPDATE nodes SET desired_state = CASE WHEN mode = 'draining' THEN 'draining' "
        "WHEN mode IN ('paused', 'quarantined') THEN 'paused' ELSE 'running' END"
    )


def downgrade():
    for name in (
        "agent_contact_at",
        "settings_error",
        "applied_mode",
        "actual_state",
        "applied_revision",
        "desired_state",
        "contribution_mode",
        "settings_revision",
    ):
        op.drop_column("nodes", name)
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "email")
