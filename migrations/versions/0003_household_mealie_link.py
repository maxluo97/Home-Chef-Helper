"""Household link to Mealie: encrypted token and who it points at.

All columns are nullable (a household starts unconnected), so this is safe
on databases that already contain households.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mealie_token_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("mealie_username", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("mealie_household_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("mealie_household_name", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("mealie_group_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("mealie_connected_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("mealie_connected_at")
        batch_op.drop_column("mealie_group_id")
        batch_op.drop_column("mealie_household_name")
        batch_op.drop_column("mealie_household_id")
        batch_op.drop_column("mealie_username")
        batch_op.drop_column("mealie_token_encrypted")
