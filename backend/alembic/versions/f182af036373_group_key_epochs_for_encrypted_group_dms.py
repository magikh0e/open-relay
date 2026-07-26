"""group key epochs for encrypted group DMs

Adds the storage for end-to-end encrypted group conversations: a per-group key
epoch, one sealed copy of that key per member, and the epoch stamp on messages.

Autogenerate also wanted to rewrite the unique constraints on `invites.code`
and `webhooks.token` as unique indexes. That is pre-existing drift between the
model and the database's representation, a no-op for the guarantee itself, and
nothing to do with group keys, so it is deliberately left out of this revision.

Revision ID: f182af036373
Revises: f2a6c8d13e4b
Create Date: 2026-07-25 14:59:18.606735

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f182af036373'
down_revision: Union[str, None] = 'f2a6c8d13e4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'group_keys',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('channel_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('epoch', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_id', 'epoch'),
    )
    op.create_index(
        op.f('ix_group_keys_channel_id'), 'group_keys', ['channel_id'], unique=False
    )

    op.create_table(
        'group_key_shares',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('group_key_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('wrapped_key', sa.Text(), nullable=False),
        sa.Column('sender_public_key', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['group_key_id'], ['group_keys.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_key_id', 'user_id'),
    )
    op.create_index(
        op.f('ix_group_key_shares_group_key_id'),
        'group_key_shares',
        ['group_key_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_group_key_shares_user_id'), 'group_key_shares', ['user_id'], unique=False
    )

    # Nullable: 1:1 DMs derive their key pairwise and never rotate, so they
    # carry no epoch. Nullable also means no server_default dance on a table
    # that already has rows.
    op.add_column('messages', sa.Column('key_epoch', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'key_epoch')
    op.drop_index(op.f('ix_group_key_shares_user_id'), table_name='group_key_shares')
    op.drop_index(op.f('ix_group_key_shares_group_key_id'), table_name='group_key_shares')
    op.drop_table('group_key_shares')
    op.drop_index(op.f('ix_group_keys_channel_id'), table_name='group_keys')
    op.drop_table('group_keys')
