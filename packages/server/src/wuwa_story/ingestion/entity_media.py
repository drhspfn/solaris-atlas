"""Pinned, source-backed entity media jobs shared with the extraction worker."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.ingestion.character_artwork import character_artwork_scope
from wuwa_story.ingestion.dungeon_context import decoded
from wuwa_story.ingestion.talk_backgrounds import background_changes


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    tier: Literal["sd", "hd", "uhd"] = "hd"
    game_version: str
    release_id: int | None = Field(default=None, ge=1)
    workspace_id: int | None = Field(default=None, gt=0, lt=2**63, strict=True)
    entities: list[str] = Field(min_length=1, max_length=100)
    kinds: list[Literal["image", "voice"]] = Field(
        default_factory=lambda: ["image", "voice"], min_length=1
    )
    voice_plan_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    voice_ids: list[int] | None = None


def image_paths(value, field="") -> set[str]:
    if isinstance(value, dict):
        return set().union(*(image_paths(child, field + "." + key) for key, child in value.items()))
    if isinstance(value, list):
        return set().union(*(image_paths(child, field) for child in value))
    if isinstance(value, str) and value.startswith("/Game/") and any(
        token in field.lower() for token in ("icon", "card", "portrait", "stand")
    ):
        return {value}
    return set()


def flow_background_paths(raw: dict) -> set[str]:
    actions = decoded(raw.get('Actions', []))
    paths = set()
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict):
            continue
        params = decoded(action.get('Params'))
        if not isinstance(params, dict):
            continue
        for _, background in background_changes(params.get('TalkItems')):
            paths.update(image['engine_path']
                         for image in background['images'])
    return paths


def flow_spine_ids(raw: dict) -> set[int]:
    actions = decoded(raw.get('Actions', []))
    ids = set()
    for action in actions if isinstance(actions, list) else []:
        params = decoded(action.get('Params')) if isinstance(
            action, dict) else None
        for _, background in background_changes(params.get('TalkItems') if isinstance(params, dict) else None):
            if background['kind'] == 'spine':
                ids.add(background['resource_id'])
    return ids


async def entity_media_targets(session, request: MediaRequest) -> list[dict]:
    release_id = await session.scalar(
        select(GameRelease.id).where(
            GameRelease.game_version == request.game_version)
        .where(GameRelease.id == request.release_id if request.release_id is not None else True)
        .order_by(GameRelease.sequence.desc()).limit(1)
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
        if node is None:
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
            rows = [{field: value for field, value in raw.items()
                     if character_artwork_scope(field, value, int(identifier)) == "character"}]
            for path, field, value in (
                ("BinData/skill/skill.json", "SkillGroupId",
                 raw.get("SkillId") or int(identifier)),
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
        if kind == 'flow_state':
            images = sorted(flow_background_paths(raw))
        else:
            images = sorted(set().union(*(image_paths(row) for row in rows)))
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
            if kind == 'flow_state':
                targets.extend({'owner': node.id, 'release_id': release_id,
                                'path': f'spine_background:{identity}', 'resource_id': identity,
                                'kind': 'spine', 'language': None, 'reference_type': 'story_spine'}
                               for identity in sorted(flow_spine_ids(raw)))
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
