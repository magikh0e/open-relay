"""e2ee dm keys

Revision ID: d5b8e1c07a92
Revises: c4f7a2b9e1d3
Create Date: 2026-07-23 13:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5b8e1c07a92'
down_revision: Union[str, None] = 'c4f7a2b9e1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_keys',
        sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('wrapped_private_key', sa.Text(), nullable=False),
        sa.Column('salt', sa.String(length=64), nullable=False),
        sa.Column('iv', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.add_column(
        'messages',
        sa.Column(
            'encrypted',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('messages', 'encrypted')
    op.drop_table('user_keys')
