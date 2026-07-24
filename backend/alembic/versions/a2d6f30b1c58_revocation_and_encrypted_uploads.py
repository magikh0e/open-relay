"""token revocation + encrypted uploads

Revision ID: a2d6f30b1c58
Revises: f1a4c8e2b937
Create Date: 2026-07-24 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2d6f30b1c58'
down_revision: Union[str, None] = 'f1a4c8e2b937'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'token_version', sa.Integer(), nullable=False, server_default='0'
        ),
    )
    op.add_column(
        'uploads',
        sa.Column(
            'encrypted', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        'uploads',
        sa.Column('enc_meta', sa.Text(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('uploads', 'enc_meta')
    op.drop_column('uploads', 'encrypted')
    op.drop_column('users', 'token_version')
