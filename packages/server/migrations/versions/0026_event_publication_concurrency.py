"""Permit parallel media publishers while excluding event source registration."""

import sqlalchemy as sa
from alembic import op

revision = "0026_event_publication_locks"
down_revision = "0025_event_asset_dependencies"
branch_labels = None
depends_on = None

EXCLUSIVE = "IF NOT pg_try_advisory_xact_lock(705,25) THEN"
SHARED = "IF NOT pg_try_advisory_xact_lock_shared(705,25) THEN"
REGISTRATION = """IF NOT (CASE WHEN TG_TABLE_SCHEMA='storage'
                THEN pg_try_advisory_xact_lock_shared(705,25)
                ELSE pg_try_advisory_xact_lock(705,25) END) THEN"""


def _replace(signature, previous, replacement):
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": signature},
    )
    if definition.count(previous) != 1:
        raise RuntimeError(f"Unexpected {signature} gate; migration aborted")
    op.execute(definition.replace(previous, replacement))


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    _replace("ops.register_event_asset_source()", EXCLUSIVE, REGISTRATION)
    _replace("ops.read_storage_changed()", EXCLUSIVE, SHARED)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    _replace("ops.read_storage_changed()", SHARED, EXCLUSIVE)
    _replace("ops.register_event_asset_source()", REGISTRATION, EXCLUSIVE)
