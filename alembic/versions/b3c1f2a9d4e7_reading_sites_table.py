"""reading_sites table

Revision ID: b3c1f2a9d4e7
Revises: 7d57d7dfc357
Create Date: 2026-11-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c1f2a9d4e7'
down_revision: Union[str, Sequence[str], None] = '7d57d7dfc357'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Store.create_schema() back-fills the rows from `readings` on first run
    op.create_table('reading_sites',
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('sensor_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lon', sa.Float(), nullable=True),
    sa.Column('located_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_ts', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('source', 'sensor_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('reading_sites')
