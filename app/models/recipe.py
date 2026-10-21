"""Local copy of each household's Mealie recipes (plan §3 `recipes_cache`).

Mealie owns recipe content (plan §2.3); these tables mirror the parts Chef
Helper needs so screens and scoring work without calling Mealie, and keep
working while Mealie is down. Recipe sync (app/services/recipe_sync.py)
keeps them current.

Recipes removed from Mealie are marked, not deleted, because cook logs and
ratings (block 6) will point at them.
"""

from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UTCDateTime

# Mealie IDs are UUIDs; slugs and names are free text from recipe titles.
MEALIE_ID_MAX_LENGTH = 64
RECIPE_TEXT_MAX_LENGTH = 255


class Recipe(Base):
    """One Mealie recipe as seen by one Chef Helper household."""

    __tablename__ = "recipes"
    # A household holds at most one copy of each Mealie recipe.
    __table_args__ = (UniqueConstraint("household_id", "mealie_recipe_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)

    # --- Copied from Mealie ---
    mealie_recipe_id: Mapped[str] = mapped_column(String(MEALIE_ID_MAX_LENGTH))
    slug: Mapped[str] = mapped_column(String(RECIPE_TEXT_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(RECIPE_TEXT_MAX_LENGTH))
    # Tag slugs, e.g. ["want-to-try", "dinner"].
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Mealie household that added the recipe (informational; see plan §9.7).
    mealie_household_id: Mapped[str | None] = mapped_column(String(MEALIE_ID_MAX_LENGTH))
    # Mealie's `updatedAt` text when ingredients were last copied. If Mealie
    # reports a different value, the recipe changed and is re-downloaded.
    mealie_updated_at: Mapped[str | None] = mapped_column(String(64))

    # --- Sync bookkeeping ---
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # Set when the recipe disappears from Mealie; cleared if it comes back.
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # --- Write-back to Mealie (app/services/mealie_writeback.py) ---
    # The "last made" time Chef Helper last wrote to Mealie successfully. If
    # the household's latest cook differs, a write-back is pending.
    mealie_last_made_pushed: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # When Chef Helper removed the want-to-try tag after the first cook
    # (plan §9.7). Set once, so a tag re-added by hand later is left alone.
    want_to_try_cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Ingredient lines in recipe order. Replacing the list deletes old lines.
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeIngredient.position",
    )


class RecipeIngredient(Base):
    """One ingredient line of a recipe (plan §3 `recipe_ingredients`)."""

    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 0-based order within the recipe

    # --- Text, for showing the line ---
    display_text: Mapped[str] = mapped_column(Text)  # e.g. "2 cups mozzarella cheese shredded"
    original_text: Mapped[str | None] = mapped_column(Text)  # as imported
    section_title: Mapped[str | None] = mapped_column(String(RECIPE_TEXT_MAX_LENGTH))  # heading starting here

    # --- Parsed parts (from Mealie's ingredient parser) ---
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    mealie_food_id: Mapped[str | None] = mapped_column(String(MEALIE_ID_MAX_LENGTH))
    food_name: Mapped[str | None] = mapped_column(String(RECIPE_TEXT_MAX_LENGTH))

    # --- Chef Helper's interpretation ---
    # Normalised food name used to match inventory (app/domain/ingredients.py).
    # None when Mealie couldn't identify a food ("salt to taste").
    canonical_name: Mapped[str | None] = mapped_column(String(RECIPE_TEXT_MAX_LENGTH), index=True)
    is_optional: Mapped[bool] = mapped_column(default=False)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
