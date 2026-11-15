"""Authenticated console access to source-backed cutscene details."""

from sqlalchemy import select
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.api.routes.cutscenes import detail, release
from wuwa_story.db.models.core import Cutscene
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileReference
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
        playback = (result.get("event") or {}).get("playback") or {}
        asset_version = playback.get("asset_version")
        result["previews"] = (await previews(db, [scene.node_id], snapshot.id, limit=None,
                                             asset_version=asset_version)).get(
            scene.node_id, {}
        ).get("previews", [])
        reports = []
        asset_ids = list(
            dict.fromkeys(
                item.get("asset_node_id")
                for item in playback.get("media", {}).values()
                if item.get("asset_node_id") is not None
            )
        )
        for asset_id in asset_ids:
            report = await visual_reference(db, asset_id, asset_version)
            if report is not None:
                meta = report.metadata_json or {}
                reports.append({"asset_node_id": asset_id, "asset_version": meta.get("asset_version"),
                                "title": meta.get("title"), "events": meta.get("events") or []})
        result["system_reports"] = reports

        runs_count = 0
        latest_run_status = None
        atlases_count = 0
        if asset_ids:
            run_stmt = (
                select(ProcessingRun)
                .join(Processor, Processor.id == ProcessingRun.processor_id)
                .where(
                    Processor.key == "cutscene_vision",
                    ProcessingRun.target_node_id.in_(asset_ids),
                )
                .order_by(ProcessingRun.id.desc())
            )
            if asset_version:
                run_stmt = run_stmt.where(
                    ProcessingRun.metadata_json["asset_version"].astext == asset_version
                )
            runs = list(await db.scalars(run_stmt))
            runs_count = len(runs)
            if runs:
                latest_run_status = runs[0].status

            atlas_stmt = select(FileReference.id).where(
                FileReference.owner_node_id.in_(asset_ids),
                FileReference.reference_type == "video_frame_atlas",
            )
            if asset_version:
                atlas_stmt = atlas_stmt.where(
                    FileReference.metadata_json["asset_version"].astext == asset_version
                )
            atlases_count = len(list(await db.scalars(atlas_stmt)))

        previews_count = len(result["previews"])
        has_analysis = bool(reports or runs_count > 0 or atlases_count > 0)
        has_artifacts = bool(has_analysis or previews_count > 0)
        result["analysis"] = {
            "asset_node_ids": asset_ids,
            "asset_version": asset_version,
            "has_analysis": has_analysis,
            "has_artifacts": has_artifacts,
            "reports_count": len(reports),
            "runs_count": runs_count,
            "latest_run_status": latest_run_status,
            "previews_count": previews_count,
            "atlases_count": atlases_count,
        }
        return JSONResponse(result)
