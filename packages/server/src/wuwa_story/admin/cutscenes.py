"""Authenticated console access to source-backed cutscene details."""

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.api.routes.cutscenes import detail, release
from wuwa_story.db.models.core import Cutscene
from wuwa_story.story.cutscenes import previews


class CutsceneData(CustomView):
    async def render(self, request: Request, templates: Jinja2Templates) -> JSONResponse:
        db = request.state.session
        scene = await db.get(Cutscene, request.path_params["node_id"])
        if scene is None or not scene.cg_name:
            raise HTTPException(404, "Cutscene not found")
        version = request.query_params.get("game_version") or None
        locale = request.query_params.get("locale", "en")
        if len(locale) > 16 or (version and len(version) > 32):
            raise HTTPException(400, "Invalid version or locale")
        snapshot = await release(db, version)
        result = await detail(scene.cg_name, snapshot.game_version, locale, db)
        result["previews"] = (await previews(db, [scene.node_id], snapshot.id, limit=None)).get(
            scene.node_id, {}
        ).get("previews", [])
        playback = (result.get("event") or {}).get("playback") or {}
        reports = []
        for asset_id in dict.fromkeys(item.get("asset_node_id") for item in playback.get("media", {}).values()
                                      if item.get("asset_node_id") is not None):
            report = await visual_reference(db, asset_id, playback.get("asset_version"))
            if report is not None:
                meta = report.metadata_json or {}
                reports.append({"asset_node_id": asset_id, "asset_version": meta.get("asset_version"),
                                "title": meta.get("title"), "events": meta.get("events") or []})
        result["system_reports"] = reports
        return JSONResponse(result)
