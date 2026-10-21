"""ORM models: one class per database table.

Every model module must be imported here so that `Base.metadata` knows about
all tables; Alembic's autogenerate and the schema tests rely on it.
"""

from app.models.base import Base
from app.models.household import Household, User

__all__ = ["Base", "Household", "User"]
