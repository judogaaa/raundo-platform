"""Allow several investor profiles per user (quota is enforced in services)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261102_0016"
down_revision = "20261102_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "investor_profiles_user_id_key", "investor_profiles", type_="unique"
    )
    op.create_index(
        "ix_investor_profiles_user_id", "investor_profiles", ["user_id"], unique=False
    )


def downgrade() -> None:
    # Restoring the unique constraint is only possible when no duplicates exist.
    op.drop_index("ix_investor_profiles_user_id", table_name="investor_profiles")
    op.create_unique_constraint(
        "investor_profiles_user_id_key", "investor_profiles", ["user_id"]
    )
