"""add_stream_titles

Revision ID: 6fc24faa972f
Revises: 0003
Create Date: 2026-08-25 04:25:48.337977

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6fc24faa972f'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'stream_titles',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('stream_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('is_initial', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['stream_id'], ['streams.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_stream_titles_stream_id', 'stream_titles', ['stream_id'], unique=False)

    # Migrate existing streams.title → stream_titles as initial titles
    op.execute("""
        INSERT INTO stream_titles (stream_id, title, started_at, is_initial, created_at)
        SELECT id, title, started_at, TRUE, created_at
        FROM streams
        WHERE title IS NOT NULL AND title != ''
    """)


def downgrade() -> None:
    op.drop_index('ix_stream_titles_stream_id', table_name='stream_titles')
    op.drop_table('stream_titles')
