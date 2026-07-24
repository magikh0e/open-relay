"""push subscriptions + app secrets

Revision ID: b9e4d21a7f30
Revises: a2d6f30b1c58
Create Date: 2026-07-24 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9e4d21a7f30'
down_revision: Union[str, None] = 'a2d6f30b1c58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_secrets',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.String(length=255), nullable=False),
        sa.Column('auth', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint'),
    )
    op.create_index(
        'ix_push_subscriptions_user_id', 'push_subscriptions', ['user_id']
    )


def downgrade() -> None:
    op.drop_index('ix_push_subscriptions_user_id', table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
    op.drop_table('app_secrets')
