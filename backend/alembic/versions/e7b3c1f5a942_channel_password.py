"""channel password (IRC +k channel key)

Revision ID: e7b3c1f5a942
Revises: d4e1a9c7b520
Create Date: 2026-07-25 07:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b3c1f5a942'
down_revision: Union[str, None] = 'd4e1a9c7b520'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'channels',
        sa.Column('password_hash', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('channels', 'password_hash')
