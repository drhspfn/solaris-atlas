"""Do not rebuild public projections for verification timestamps alone."""

import sqlalchemy as sa
from alembic import op

revision = "0029_storage_verification_noop"
down_revision = "0028_agent_reader_role"
branch_labels = None
depends_on = None

ANCHOR = "SELECT active INTO enabled FROM ops.event_asset_state WHERE id=1;"
VERIFICATION_FILTER = """IF TG_OP='UPDATE' AND TG_TABLE_NAME='file_location' THEN
            IF NOT EXISTS (
                SELECT 1 FROM old_rows o FULL JOIN new_rows n USING(id)
                WHERE (to_jsonb(o)-'verified_at') IS DISTINCT FROM (to_jsonb(n)-'verified_at')
            ) THEN
                RETURN NULL;
            END IF;
        END IF;
        """


def _replace(previous, replacement):
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('ops.read_storage_changed()'::regprocedure)"
    ))
    expected_filters = 0 if previous == ANCHOR else 1
    if definition.count(previous) != 1 or definition.count(VERIFICATION_FILTER) != expected_filters:
        raise RuntimeError("Unexpected storage invalidation function; migration aborted")
    op.execute(definition.replace(previous, replacement))


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    # Keep the existing advisory ordering and isolation fences before this check.
    # A mixed batch still follows the original invalidation path in full.
    _replace(ANCHOR, VERIFICATION_FILTER + ANCHOR)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    _replace(VERIFICATION_FILTER + ANCHOR, ANCHOR)
