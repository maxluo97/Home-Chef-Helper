"""Recipe cache: each household's copy of its Mealie recipes and ingredients.

New tables only, plus one nullable column on households, so this is safe on
databases that already contain data.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- recipes: one row per (household, Mealie recipe) ---
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("mealie_recipe_id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("mealie_household_id", sa.String(length=64), nullable=True),
        sa.Column("mealie_updated_at", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id"], ["households.id"],
            name=op.f("fk_recipes_household_id_households"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipes")),
        sa.UniqueConstraint(
            "household_id", "mealie_recipe_id", name=op.f("uq_recipes_household_id_mealie_recipe_id")
        ),
    )
    with op.batch_alter_table("recipes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_recipes_household_id"), ["household_id"], unique=False)

    # --- recipe_ingredients: ingredient lines of each cached recipe ---
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("section_title", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("mealie_food_id", sa.String(length=64), nullable=True),
        sa.Column("food_name", sa.String(length=255), nullable=True),
        sa.Column("canonical_name", sa.String(length=255), nullable=True),
        sa.Column("is_optional", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"],
            name=op.f("fk_recipe_ingredients_recipe_id_recipes"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_ingredients")),
    )
    with op.batch_alter_table("recipe_ingredients", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_recipe_ingredients_canonical_name"), ["canonical_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_recipe_ingredients_recipe_id"), ["recipe_id"], unique=False)

    # --- households: when recipes were last synced ---
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("recipes_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("recipes_synced_at")

    with op.batch_alter_table("recipe_ingredients", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recipe_ingredients_recipe_id"))
        batch_op.drop_index(batch_op.f("ix_recipe_ingredients_canonical_name"))
    op.drop_table("recipe_ingredients")

    with op.batch_alter_table("recipes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recipes_household_id"))
    op.drop_table("recipes")
