"""Track what Chef Helper has written back to Mealie for each recipe.

Two nullable columns on recipes, so this is safe on existing data. Existing
cooks start as "pending" and are written to Mealie at the next sync.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("recipes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mealie_last_made_pushed", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("want_to_try_cleared_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("recipes", schema=None) as batch_op:
        batch_op.drop_column("want_to_try_cleared_at")
        batch_op.drop_column("mealie_last_made_pushed")
