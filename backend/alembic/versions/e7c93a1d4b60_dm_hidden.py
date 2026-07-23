"""per-member hidden flag for closing DMs

Revision ID: e7c93a1d4b60
Revises: d5b8e1c07a92
Create Date: 2026-07-23 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c93a1d4b60'
down_revision: Union[str, None] = 'd5b8e1c07a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'channel_members',
        sa.Column(
            'hidden',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('channel_members', 'hidden')
