"""per-user privacy settings

Revision ID: f1a4c8e2b937
Revises: e7c93a1d4b60
Create Date: 2026-07-23 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a4c8e2b937'
down_revision: Union[str, None] = 'e7c93a1d4b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FLAGS = ("share_typing", "share_presence", "allow_dms", "discoverable")


def upgrade() -> None:
    for flag in _FLAGS:
        op.add_column(
            'users',
            sa.Column(
                flag, sa.Boolean(), nullable=False, server_default=sa.true()
            ),
        )


def downgrade() -> None:
    for flag in reversed(_FLAGS):
        op.drop_column('users', flag)
