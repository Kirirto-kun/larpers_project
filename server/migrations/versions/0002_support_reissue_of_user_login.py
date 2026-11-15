"""support reissue of user login"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invitations", sa.Column("existing_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_invitations_existing_user", "invitations", "users", ["existing_user_id"], ["id"]
    )


def downgrade():
    op.drop_constraint("fk_invitations_existing_user", "invitations", type_="foreignkey")
    op.drop_column("invitations", "existing_user_id")
