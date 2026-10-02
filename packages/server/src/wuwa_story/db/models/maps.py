"""Versioned game coordinate maps; file identity stays in storage.file_object."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class TileMap(MetadataMixin, Base):
    __tablename__ = "tile_map"
    __table_args__ = (
        UniqueConstraint("asset_job_id", "game_map_id", "layer_key"),
        CheckConstraint("tile_size > 0 AND world_tile_size > 0", name="positive_tile_size"),
        {"schema": "core"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    asset_job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    game_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    game_map_id: Mapped[int] = mapped_column(Integer, nullable=False)
    layer_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tile_size: Mapped[int] = mapped_column(Integer, nullable=False)
    world_tile_size: Mapped[float] = mapped_column(Float, nullable=False)
    min_x: Mapped[int] = mapped_column(Integer, nullable=False)
    min_y: Mapped[int] = mapped_column(Integer, nullable=False)
    max_x: Mapped[int] = mapped_column(Integer, nullable=False)
    max_y: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_file_id: Mapped[int | None] = mapped_column(ForeignKey("storage.file_object.id", ondelete="RESTRICT"))


class MapTile(MetadataMixin, Base):
    __tablename__ = "map_tile"
    __table_args__ = (UniqueConstraint("map_id", "x", "y"), {"schema": "core"})
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("core.tile_map.id", ondelete="CASCADE"), nullable=False, index=True)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("storage.file_object.id", ondelete="RESTRICT"), nullable=False)
    source_path: Mapped[str] = mapped_column(String(2048), nullable=False)


class MapMarker(MetadataMixin, Base):
    __tablename__ = "map_marker"
    __table_args__ = (
        UniqueConstraint("asset_job_id", "game_map_id", "entity_id"),
        {"schema": "core"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    asset_job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    game_map_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blueprint_type: Mapped[str] = mapped_column(String(255), nullable=False)
    world_x: Mapped[float] = mapped_column(Float, nullable=False)
    world_y: Mapped[float] = mapped_column(Float, nullable=False)
    world_z: Mapped[float] = mapped_column(Float, nullable=False)
