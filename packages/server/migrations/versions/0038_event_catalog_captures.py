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
    op.execute("""CREATE FUNCTION ops.read_event_capture_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE identity bigint; snapshot text;
        BEGIN
            identity := CASE WHEN TG_OP='DELETE' THEN OLD.event_id ELSE NEW.event_id END;
            snapshot := CASE WHEN TG_OP='DELETE' THEN OLD.version ELSE NEW.version END;
            PERFORM ops.mark_read_projection('game_event', identity);
            PERFORM ops.mark_read_projection('game_event', identity, snapshot);
            RETURN NULL;
        END; $$""")
    op.execute("""CREATE TRIGGER read_event_capture_changed
        AFTER INSERT OR UPDATE OR DELETE ON core.game_event_snapshot_member
        FOR EACH ROW EXECUTE FUNCTION ops.read_event_capture_changed()""")


def downgrade():
    op.execute("DROP TRIGGER read_event_capture_changed ON core.game_event_snapshot_member")
    op.execute("DROP FUNCTION ops.read_event_capture_changed()")
    op.drop_column("game_event_snapshot_member", "client_capture", schema="core")
