"""Cook log and per-person ratings.

New tables only, so this is safe on databases that already contain data.
The database itself enforces whole-star ratings from 1 to 5.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- cook_events: each time a recipe was cooked ---
    op.create_table(
        "cook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("cooked_at", sa.DateTime(), nullable=False),
        sa.Column("cooked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("servings", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("logged_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cooked_by_user_id"], ["users.id"],
            name=op.f("fk_cook_events_cooked_by_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"], ["households.id"],
            name=op.f("fk_cook_events_household_id_households"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["logged_by_user_id"], ["users.id"],
            name=op.f("fk_cook_events_logged_by_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"],
            name=op.f("fk_cook_events_recipe_id_recipes"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cook_events")),
    )
    with op.batch_alter_table("cook_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cook_events_cooked_at"), ["cooked_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_cook_events_household_id"), ["household_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cook_events_recipe_id"), ["recipe_id"], unique=False)

    # --- ratings: per-person star ratings, history kept ---
    op.create_table(
        "ratings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("cook_event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name=op.f("ck_ratings_stars_range")),
        sa.ForeignKeyConstraint(
            ["cook_event_id"], ["cook_events.id"],
            name=op.f("fk_ratings_cook_event_id_cook_events"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"], ["households.id"],
            name=op.f("fk_ratings_household_id_households"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"],
            name=op.f("fk_ratings_recipe_id_recipes"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_ratings_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ratings")),
    )
    with op.batch_alter_table("ratings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ratings_household_id"), ["household_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ratings_recipe_id"), ["recipe_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ratings_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("ratings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ratings_user_id"))
        batch_op.drop_index(batch_op.f("ix_ratings_recipe_id"))
        batch_op.drop_index(batch_op.f("ix_ratings_household_id"))
    op.drop_table("ratings")

    with op.batch_alter_table("cook_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cook_events_recipe_id"))
        batch_op.drop_index(batch_op.f("ix_cook_events_household_id"))
        batch_op.drop_index(batch_op.f("ix_cook_events_cooked_at"))
    op.drop_table("cook_events")
