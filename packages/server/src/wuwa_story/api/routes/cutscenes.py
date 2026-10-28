"""Public, paginated cutscene archive and existing playback manifests."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.captions import caption_texts, caption_tracks
from wuwa_story.api.routes.story.media import _links, cutscene_flows, quest_media
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.repositories import cutscenes as repository
from wuwa_story.db.session import get_session
from wuwa_story.story.cutscenes import summaries
from wuwa_story.story.domain import version_key
from wuwa_story.story.service import _release_id

router = APIRouter(prefix="/cutscenes", tags=["story browsing"])


async def release(session, game_version):
    release_id = await _release_id(session, game_version)
    if release_id is None:
        raise HTTPException(404, "game version not imported")
    return await session.get(GameRelease, release_id)


@router.get("")
async def catalog(game_version: str | None = None, locale: str = "en",
                  q: str = Query("", max_length=128), playable: bool = True,
                  limit: int = Query(24, ge=1, le=48), offset: int = Query(0, ge=0, le=10000),
                  session: AsyncSession = Depends(get_session)):
    snapshot = await release(session, game_version) if game_version else None
    total, rows = await repository.page(session, snapshot.id if snapshot else None, q.strip(), playable, limit, offset)
    items = {}
    # Resolve each scene in an actual containing snapshot, never fabricate an all-version graph.
    for identity in {snapshot.id if snapshot else row.latest_release_id for row in rows}:
        group = rows if snapshot else [
            row for row in rows if row.latest_release_id == identity]
        items.update({item["node_id"]: item for item in await summaries(session, group, identity, locale)})
    return {"game_version": snapshot.game_version if snapshot else None,
            "imported_game_versions": sorted(await repository.imported_versions(session), key=version_key, reverse=True),
            "total": total, "limit": limit, "offset": offset,
            "items": [{**items[row.node_id], "first_observed_game_version": row.first_observed_game_version,
                       "game_version": snapshot.game_version if snapshot else row.latest_game_version} for row in rows]}


@router.get("/{name}")
async def detail(name: str, game_version: str | None = None, locale: str = "en",
                 session: AsyncSession = Depends(get_session)):
    snapshot = await release(session, game_version)
    row = await repository.scene(session, name, snapshot.id)
    if row is None:
        raise HTTPException(404, "cutscene not found in this snapshot")
    item = (await summaries(session, [row], snapshot.id, locale))[0]
    event = None
    passages = []
    for quest in item["quests"]:
        manifest = await quest_media(quest["game_quest_id"], snapshot.game_version, locale, session)
        for candidate in manifest["events"]:
            if candidate["kind"] == "cutscene" and candidate["reference"] == f"cutscene:{name}":
                event = event or candidate
                passages.append({**quest, "flow_state": candidate["flow_state"],
                                 "action_index": candidate["action_index"],
                                 "reference": candidate["reference"],
                                 "focus_line": None if quest.get("context_inferred") else await repository.first_line(
                                     session, candidate.get("transcript_states") or [candidate["flow_state"]], snapshot.id)})
    if event is None:
        flows = await cutscene_flows(session, [row.node_id])
        playback = flows.get(row.node_id)
        captions = {}
        if playback and playback.get("asset_version") == "3.7.0":
            links = await _links(session, [row.node_id], ("uses_caption",), snapshot.id)
            ids = [link["node_id"] for link in links.get(row.node_id, [])]
            if ids:
                records = list(await session.scalars(
                    select(SourceRecord.data)
                    .join(NodeRevision, NodeRevision.source_record_id == SourceRecord.id)
                    .where(NodeRevision.node_id.in_(ids), NodeRevision.release_id == snapshot.id)
                ))
                captions = caption_tracks(records, await caption_texts(session, records, locale, snapshot.id))
        event = {"kind": "cutscene", "reference": f"cutscene:{name}", "flow_state": "",
                 "action_index": 0, "playback": playback, "captions": captions, "resources": []}
    return {**item, "game_version": snapshot.game_version, "event": event, "passages": passages}
