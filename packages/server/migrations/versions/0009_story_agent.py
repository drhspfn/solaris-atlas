"""Durable agent execution, budget accounting, notes and explanation vectors."""

from alembic import op
from sqlalchemy import text

from wuwa_story.db.models.agents import (
    AgentCall,
    AgentDailyUsage,
    AgentJob,
    AgentNote,
    ExplanationEmbedding,
)

revision = "0009_story_agent"
down_revision = "0008_public_api_cache"
branch_labels = None
depends_on = None
TABLES = (
    AgentDailyUsage.__table__,
    AgentJob.__table__,
    AgentCall.__table__,
    AgentNote.__table__,
    ExplanationEmbedding.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table in TABLES:
        table.create(bind, checkfirst=True)
    # Fresh foundation creates current ORM tables before migration 0008. Existing
    # deployments need this new public table added explicitly to invalidation.
    if not bind.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='search.explanation_embedding'::regclass AND tgname='public_cache_changed')"
        )
    ):
        op.execute(
            "INSERT INTO ops.public_cache_revision VALUES ('search.explanation_embedding', txid_current()) ON CONFLICT DO NOTHING"
        )
        op.execute(
            "CREATE TRIGGER public_cache_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON search.explanation_embedding FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "DELETE FROM ops.public_cache_revision WHERE table_name='search.explanation_embedding'"
    )
    for table in reversed(TABLES):
        table.drop(op.get_bind(), checkfirst=True)
