"""Add versioned map tiles and positioned game entities."""

from alembic import op

from wuwa_story.db.models.maps import MapMarker, MapTile, TileMap

revision = "0006_tile_maps"
down_revision = "0005_quest_tree_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Foundation creates current metadata on an empty database.
    for model in (TileMap, MapTile, MapMarker):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for model in (MapMarker, MapTile, TileMap):
        model.__table__.drop(op.get_bind(), checkfirst=True)
