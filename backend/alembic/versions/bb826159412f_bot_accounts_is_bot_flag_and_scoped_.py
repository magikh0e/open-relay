"""bot accounts: is_bot flag and scoped tokens

Adds the bot identity flag and the token table behind it. The token itself is
never stored, only its SHA-256 digest, so what lives here cannot be replayed as
a credential.

As with the group-key revision, autogenerate also wanted to rewrite the unique
constraints on `invites.code` and `webhooks.token` as unique indexes. That is
pre-existing model-versus-database drift, a no-op for the guarantee itself, and
unrelated to bot accounts, so it is deliberately left out.

Revision ID: bb826159412f
Revises: f182af036373
Create Date: 2026-07-26 04:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb826159412f'
down_revision: Union[str, None] = 'f182af036373'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'bot_tokens',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scopes', sa.String(length=128), nullable=False),
        sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique: the digest is what a request is resolved by, so two rows sharing
    # one would make the lookup ambiguous.
    op.create_index(
        op.f('ix_bot_tokens_token_hash'), 'bot_tokens', ['token_hash'], unique=True
    )
    op.create_index(
        op.f('ix_bot_tokens_user_id'), 'bot_tokens', ['user_id'], unique=False
    )

    # server_default is required, not cosmetic: this table has rows, and a
    # NOT NULL column without one fails on any populated database.
    op.add_column(
        'users',
        sa.Column('is_bot', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('users', 'is_bot')
    op.drop_index(op.f('ix_bot_tokens_user_id'), table_name='bot_tokens')
    op.drop_index(op.f('ix_bot_tokens_token_hash'), table_name='bot_tokens')
    op.drop_table('bot_tokens')
