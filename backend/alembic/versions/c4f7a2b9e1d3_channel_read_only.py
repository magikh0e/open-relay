"""channel read_only flag

Revision ID: c4f7a2b9e1d3
Revises: ade2e03dbf06
Create Date: 2026-07-23 12:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f7a2b9e1d3'
down_revision: Union[str, None] = 'ade2e03dbf06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'channels',
        sa.Column(
            'read_only',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('channels', 'read_only')
