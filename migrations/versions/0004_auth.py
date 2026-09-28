"""Add server-side accounts, sessions, and provider identities."""

from alembic import op

import wuwa_story.db.models  # noqa: F401 - register auth metadata
from wuwa_story.db.base import Base

revision = "0004_auth"
down_revision = "0003_entity_search_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS "auth"')
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.schema == "auth":
            table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        if table.schema == "auth":
            table.drop(bind=bind, checkfirst=True)
    op.execute('DROP SCHEMA IF EXISTS "auth"')
