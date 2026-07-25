"""last_active_at + share_last_active on users

Revision ID: f2a6c8d13e4b
Revises: e7b3c1f5a942
Create Date: 2026-07-25 08:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a6c8d13e4b'
down_revision: Union[str, None] = 'e7b3c1f5a942'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'share_last_active',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )
    op.add_column(
        'users',
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'last_active_at')
    op.drop_column('users', 'share_last_active')
