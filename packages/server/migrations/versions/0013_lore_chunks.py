"""Add lore_chunks for hybrid search."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import pgvector.sqlalchemy

revision = '0013_lore_chunks'
down_revision = '0012_game_event_archive'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table('lore_chunk',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('source_id', sa.String(length=100), nullable=False),
        sa.Column('quest_id', sa.String(length=100), nullable=True),
        sa.Column('game_version', sa.String(length=20), nullable=True),
        sa.Column('characters', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('chunk_type', sa.String(length=50), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True),
        sa.Column('timestamp_start', sa.Integer(), nullable=True),
        sa.Column('timestamp_end', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='search'
    )
    op.create_index('ix_lore_chunk_fts', 'lore_chunk', ['search_vector'], unique=False, schema='search', postgresql_using='gin')

    op.create_table('lore_chunk_embedding',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('chunk_id', sa.BigInteger(), nullable=False),
        sa.Column('model_id', sa.BigInteger(), nullable=False),
        sa.Column('content_hash', postgresql.BYTEA(), nullable=False),
        sa.Column('embedding', pgvector.sqlalchemy.Vector(), nullable=False),
        sa.ForeignKeyConstraint(['chunk_id'], ['search.lore_chunk.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['model_id'], ['search.embedding_model.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chunk_id', 'model_id', name='uq_lore_chunk_embedding'),
        schema='search'
    )

def downgrade() -> None:
    op.drop_table('lore_chunk_embedding', schema='search')
    op.drop_index('ix_lore_chunk_fts', table_name='lore_chunk', schema='search', postgresql_using='gin')
    op.drop_table('lore_chunk', schema='search')
