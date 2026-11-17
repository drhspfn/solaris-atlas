"""Retain source records while binding aliases to a canonical event."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0039_event_canonical_binding"
down_revision = "0038_event_catalog_captures"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("game_event", sa.Column("canonical_event_id", sa.BigInteger(), nullable=True), schema="core")
    op.add_column("game_event", sa.Column("canonical_evidence", JSONB(), nullable=True), schema="core")
    op.create_foreign_key("fk_game_event_canonical", "game_event", "game_event", ["canonical_event_id"],
                          ["id"], source_schema="core", referent_schema="core", ondelete="RESTRICT")
    op.create_check_constraint("ck_game_event_canonical_not_self", "game_event",
                               "canonical_event_id IS NULL OR canonical_event_id <> id", schema="core")
    op.create_index("ix_game_event_canonical_event_id", "game_event", ["canonical_event_id"], schema="core")


def downgrade():
    op.drop_index("ix_game_event_canonical_event_id", table_name="game_event", schema="core")
    op.drop_constraint("ck_game_event_canonical_not_self", "game_event", schema="core", type_="check")
    op.drop_constraint("fk_game_event_canonical", "game_event", schema="core", type_="foreignkey")
    op.drop_column("game_event", "canonical_evidence", schema="core")
    op.drop_column("game_event", "canonical_event_id", schema="core")
