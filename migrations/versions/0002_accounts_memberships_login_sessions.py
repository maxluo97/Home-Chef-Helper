"""Accounts, household memberships, and login sessions.

Adds login fields to users, a join code to households, the many-to-many
household_memberships table (a user can be in several households), and
login_sessions (one row per logged-in device).

Note: the new NOT NULL columns have no default, so this migration expects
empty users/households tables. That holds: no deployment had accounts
before this revision.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- New table: which users belong to which households ---
    op.create_table(
        "household_memberships",
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"], ["households.id"],
            name=op.f("fk_household_memberships_household_id_households"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_household_memberships_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "user_id", name=op.f("pk_household_memberships")),
    )
    with op.batch_alter_table("household_memberships", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_household_memberships_user_id"), ["user_id"], unique=False)

    # --- New table: logged-in devices ---
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("active_household_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_household_id"], ["households.id"],
            name=op.f("fk_login_sessions_active_household_id_households"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_login_sessions_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_login_sessions")),
    )
    with op.batch_alter_table("login_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_sessions_user_id"), ["user_id"], unique=False)

    # --- households: add the join code ---
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(sa.Column("join_code", sa.String(length=9), nullable=False))
        batch_op.create_unique_constraint(batch_op.f("uq_households_join_code"), ["join_code"])

    # --- users: login fields; "name" becomes "display_name" ---
    with op.batch_alter_table("users", schema=None) as batch_op:
        # A rename, not drop + add (autogenerate's guess), so names survive.
        batch_op.alter_column("name", new_column_name="display_name")
        batch_op.add_column(sa.Column("username", sa.String(length=32), nullable=False))
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=False))
        batch_op.create_unique_constraint(batch_op.f("uq_users_username"), ["username"])


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse order of upgrade().
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_users_username"), type_="unique")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("username")
        batch_op.alter_column("display_name", new_column_name="name")

    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_households_join_code"), type_="unique")
        batch_op.drop_column("join_code")

    with op.batch_alter_table("login_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_sessions_user_id"))
    op.drop_table("login_sessions")

    with op.batch_alter_table("household_memberships", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_household_memberships_user_id"))
    op.drop_table("household_memberships")
