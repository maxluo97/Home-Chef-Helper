"""ORM models: one class per database table.

Every model module must be imported here so that `Base.metadata` knows about
all tables; Alembic's autogenerate and the schema tests rely on it.
"""

from app.models.auth import LoginSession
from app.models.base import Base
from app.models.household import Household, HouseholdMembership, User
from app.models.recipe import Recipe, RecipeIngredient

__all__ = ["Base", "Household", "HouseholdMembership", "LoginSession", "Recipe", "RecipeIngredient", "User"]
