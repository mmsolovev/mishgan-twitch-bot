"""Update game_metadata_hltb: rename columns, add new fields, drop avg_hours

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("game_metadata_hltb", "main_story_hours", new_column_name="hltb_main_story")
    op.alter_column("game_metadata_hltb", "main_extra_hours", new_column_name="hltb_main_extra")
    op.alter_column("game_metadata_hltb", "completionist_hours", new_column_name="hltb_completionist")
    op.drop_column("game_metadata_hltb", "avg_hours")
    op.add_column("game_metadata_hltb", sa.Column("hltb_all_styles", sa.Float()))
    op.add_column("game_metadata_hltb", sa.Column("hltb_coop", sa.Float()))
    op.add_column("game_metadata_hltb", sa.Column("hltb_multiplayer", sa.Float()))
    op.add_column("game_metadata_hltb", sa.Column("hltb_review_score", sa.Integer()))
def downgrade() -> None:
    op.drop_column("game_metadata_hltb", "hltb_review_score")
    op.drop_column("game_metadata_hltb", "hltb_multiplayer")
    op.drop_column("game_metadata_hltb", "hltb_coop")
    op.drop_column("game_metadata_hltb", "hltb_all_styles")
    op.add_column("game_metadata_hltb", sa.Column("avg_hours", sa.Float()))
    op.alter_column("game_metadata_hltb", "hltb_completionist", new_column_name="completionist_hours")
    op.alter_column("game_metadata_hltb", "hltb_main_extra", new_column_name="main_extra_hours")
    op.alter_column("game_metadata_hltb", "hltb_main_story", new_column_name="main_story_hours")
