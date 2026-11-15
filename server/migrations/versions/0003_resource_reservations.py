"""Keep unacknowledged execution resources reserved until the worker lease ends."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE uploads DROP COLUMN IF EXISTS proof_hash")
    op.add_column("attempts", sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE attempts SET reserved_until = lease_expires_at")
    op.alter_column("attempts", "reserved_until", nullable=False)
    op.create_index("ix_attempts_node_reserved", "attempts", ["node_id", "reserved_until"])


def downgrade():
    op.drop_index("ix_attempts_node_reserved", "attempts")
    op.drop_column("attempts", "reserved_until")
