"""add is_visible, is_active, bot_name to bot_commands

Revision ID: 0005
Revises: 6fc24faa972f
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "6fc24faa972f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_commands",
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "bot_commands",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "bot_commands",
        sa.Column("bot_name", sa.String(50), nullable=False, server_default="self"),
    )


def downgrade() -> None:
    op.drop_column("bot_commands", "bot_name")
    op.drop_column("bot_commands", "is_active")
    op.drop_column("bot_commands", "is_visible")