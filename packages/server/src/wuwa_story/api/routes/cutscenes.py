"""Public, paginated cutscene archive and existing playback manifests."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.media import cutscene_flows, quest_media
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.repositories import cutscenes as repository
from wuwa_story.db.session import get_session
from wuwa_story.story.cutscenes import summaries
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
    snapshot = await release(session, game_version)
    total, rows = await repository.page(session, snapshot.id, q.strip(), playable, limit, offset)
    return {"game_version": snapshot.game_version, "total": total, "limit": limit, "offset": offset,
            "items": await summaries(session, rows, snapshot.id, locale)}


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
                                 "focus_line": await repository.first_line(session,
                                     candidate.get("transcript_states") or [candidate["flow_state"]], snapshot.id)})
    if event is None:
        flows = await cutscene_flows(session, [row.node_id])
        event = {"kind": "cutscene", "reference": f"cutscene:{name}", "flow_state": "",
                 "action_index": 0, "playback": flows.get(row.node_id), "resources": []}
    return {**item, "game_version": snapshot.game_version, "event": event, "passages": passages}
