"""Durable, idempotent lore review requests after source imports."""

from alembic import op

from wuwa_story.db.models.agents import AgentRevisit

revision = "0010_agent_revisit"
down_revision = "0009_story_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    AgentRevisit.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    AgentRevisit.__table__.drop(op.get_bind(), checkfirst=True)
