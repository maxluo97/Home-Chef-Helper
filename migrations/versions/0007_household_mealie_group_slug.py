"""Store the Mealie group's short name, for "Open in Mealie" links.

One nullable column, so this is safe on existing data. Households connected
before this revision get it the next time they reconnect; until then their
links are simply hidden.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mealie_group_slug", sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("mealie_group_slug")
