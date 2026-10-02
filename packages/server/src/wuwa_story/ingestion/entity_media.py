"""Pinned, source-backed entity media jobs shared with the extraction worker."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    tier: Literal["sd", "hd", "uhd"] = "hd"
    game_version: str
    entities: list[str] = Field(min_length=1, max_length=100)
    kinds: list[Literal["image", "voice"]] = Field(
        default_factory=lambda: ["image", "voice"], min_length=1
    )
    voice_ids: list[int] | None = None


async def entity_media_targets(session, request: MediaRequest) -> list[dict]:
    release_id = await session.scalar(
        select(GameRelease.id).where(GameRelease.game_version == request.game_version)
    )
    if release_id is None:
        raise ValueError("Story snapshot is not imported")
    targets = []

    async def records(path, field, value):
        return list(
            await session.scalars(
                select(SourceRecord.data)
                .join(SourceFile)
                .where(
                    SourceRecord.release_id == release_id,
                    SourceFile.logical_source_path == path,
                    SourceRecord.data[field].as_integer() == value,
                )
                .order_by(SourceRecord.row_index)
            )
        )

    for key in sorted(set(request.entities)):
        node = await session.scalar(select(Node).where(Node.canonical_key == key))
        kind, _, identifier = key.partition(":")
        if node is None or kind not in ("character", "item") or not identifier.isdigit():
            raise ValueError(f"Unsupported media entity: {key}")
        raw = await session.scalar(
            select(SourceRecord.data)
            .join(NodeRevision, NodeRevision.source_record_id == SourceRecord.id)
            .where(
                NodeRevision.node_id == node.id,
                NodeRevision.release_id == release_id,
            )
            .order_by(NodeRevision.revision.desc())
            .limit(1)
        )
        if raw is None:
            raise ValueError(f"Entity has no source in snapshot: {key}")
        rows = [raw]
        if kind == "character":
            for path, field, value in (
                ("BinData/skill/skill.json", "SkillGroupId", raw.get("SkillId") or int(identifier)),
                (
                    "BinData/skillTree/skilltree.json",
                    "NodeGroup",
                    raw.get("SkillTreeGroupId") or int(identifier),
                ),
                (
                    "BinData/resonate_chain/resonantchain.json",
                    "GroupId",
                    raw.get("ResonantChainGroupId") or int(identifier),
                ),
            ):
                rows.extend(await records(path, field, value))
        images = sorted(
            {
                value
                for row in rows
                for field, value in row.items()
                if isinstance(value, str)
                and value.startswith("/Game/")
                and any(token in field.lower() for token in ("icon", "card", "portrait", "stand"))
            }
        )
        if "image" in request.kinds:
            targets.extend(
                {
                    "owner": node.id,
                    "release_id": release_id,
                    "path": path,
                    "kind": "image",
                    "language": None,
                }
                for path in images
            )
        if kind == "character" and "voice" in request.kinds:
            for row in await records("BinData/favor/favorword.json", "RoleId", int(identifier)):
                path = row.get("Voice")
                if (
                    isinstance(path, str)
                    and path.startswith("/Game/")
                    and (request.voice_ids is None or row.get("Id") in request.voice_ids)
                ):
                    targets.extend(
                        {
                            "owner": node.id,
                            "release_id": release_id,
                            "path": path,
                            "kind": "voice",
                            "language": language,
                        }
                        for language in ("en", "ja", "ko", "zh")
                    )
    if not targets:
        raise ValueError("No authored media references for this request")
    return targets
