"""incoming webhooks

Revision ID: c8a1f4e29b07
Revises: b9e4d21a7f30
Create Date: 2026-07-25 04:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8a1f4e29b07'
down_revision: Union[str, None] = 'b9e4d21a7f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column('author_name', sa.String(length=64), nullable=True),
    )
    op.create_table(
        'webhooks',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('channel_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index('ix_webhooks_channel_id', 'webhooks', ['channel_id'])
    op.create_index('ix_webhooks_token', 'webhooks', ['token'])


def downgrade() -> None:
    op.drop_index('ix_webhooks_token', table_name='webhooks')
    op.drop_index('ix_webhooks_channel_id', table_name='webhooks')
    op.drop_table('webhooks')
    op.drop_column('messages', 'author_name')
