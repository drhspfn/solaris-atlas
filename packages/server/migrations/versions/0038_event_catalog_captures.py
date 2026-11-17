"""Keep a versioned client presentation independently of event schedules."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0038_event_catalog_captures"
down_revision = "0037_character_search_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("game_event_snapshot_member", sa.Column("client_capture", JSONB(), nullable=True),
                  schema="core")


def downgrade():
    op.drop_column("game_event_snapshot_member", "client_capture", schema="core")
