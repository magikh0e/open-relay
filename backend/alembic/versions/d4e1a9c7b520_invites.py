"""invite codes

Revision ID: d4e1a9c7b520
Revises: c8a1f4e29b07
Create Date: 2026-07-25 05:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e1a9c7b520'
down_revision: Union[str, None] = 'c8a1f4e29b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'invites',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['used_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_invites_code', 'invites', ['code'])


def downgrade() -> None:
    op.drop_index('ix_invites_code', table_name='invites')
    op.drop_table('invites')
