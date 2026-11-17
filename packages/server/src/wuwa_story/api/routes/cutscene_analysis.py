"""Administrator controls for the existing, checkpointed visual worker."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from wuwa_story.agents.contracts import StrictModel
from wuwa_story.agents.cutscene_analysis_plan import build_analysis_plan, enqueue_logical_visual_job
from wuwa_story.agents.cutscene_vision import (
    enqueue_visual_job,
    resume_visual_job,
    visual_reference,
    wipe_cutscene_analysis,
)
from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.agents.visual_enqueue_lock import lock_visual_sources
from wuwa_story.api.routes.story.cutscene_audio import audio_bundles
from wuwa_story.api.routes.story.media import cutscene_videos
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.core import Cutscene
from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileObject, FileReference
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.cutscenes import Clip, PlaybackFlow
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.story.cutscene_publications import publication_order, publication_types

router = APIRouter(prefix="/cutscenes", dependencies=[Depends(require_admin)])


class VisualRequest(StrictModel):
    asset_node_ids: list[int] = Field(min_length=1, max_length=20)
    game_version: str = Field(min_length=1, max_length=64)


class VisualResumeRequest(StrictModel):
    output_tokens: int | None = Field(default=None, ge=128, le=16000)


@router.get("/assets/{asset_id}/playback")
async def playback(asset_id: int, game_version: str = Query(min_length=1, max_length=64),
                   session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, asset_id)
    videos = await cutscene_videos(session, [asset_id], game_version)
    if node is None or asset_id not in videos:
        raise HTTPException(
            404, "Playable video is unavailable for this version")
    video = videos[asset_id]
    settings = get_settings()
    bundles = await audio_bundles(session, [asset_id], game_version, settings, S3Storage(settings))
    if asset_id in bundles:
        video = {**video, "url": bundles[asset_id]["videos"]["full"],
                 "audio_tracks": bundles[asset_id]["tracks"], "timeline_offset": 0}
    return {"version": 1, "entry": "video", "asset_version": game_version,
            "nodes": [{"id": "video", "kind": "clip", "asset": node.canonical_key,
                       "start": 0, "end": None, "next": None}],
            "media": {node.canonical_key: video}, "evidence": "Imported video variant"}


def public_job(run: ProcessingRun) -> dict[str, Any]:
    checkpoint = run.raw_output or {}
    batches = checkpoint.get("batches", [])
    return {
        "id": run.id,
        "asset_node_id": run.target_node_id,
        "game_version": run.metadata_json.get("asset_version"),
        "status": run.status,
        "title_only": bool(run.metadata_json.get("title_only_report_id")),
        "title_generated": bool(checkpoint.get("title")) if run.metadata_json.get("title_only_report_id") else None,
        "step": checkpoint.get("step", 0),
        "frames_done": sum(len(batch.get("events", [])) for batch in batches),
        "frames_remaining": sum(len(batch) for batch in checkpoint.get("pending", [])),
        "error": run.error,
        "tokens_input": run.tokens_input,
        "tokens_output": run.tokens_output,
        "cost_usd": run.cost,
        "output_tokens": run.metadata_json.get("settings", {}).get("vision_output_tokens"),
    }


_ANALYSIS_REF_TYPES = (
    "cutscene_visual_description",
    "cutscene_visual_title",
    "cutscene_preview",
    "video_frame_atlas",
)


async def _asset_analysis_summaries(
    session: AsyncSession,
    rows: list[tuple[FileReference, str]],
) -> dict[tuple[int, str], dict[str, Any]]:
    if not rows:
        return {}
    summaries: dict[tuple[int, str], dict[str, Any]] = {}
    video_file_to_version: dict[tuple[int, int], str] = {}
    asset_ids: set[int] = set()
    for ref, _ in rows:
        ver = str((ref.metadata_json or {}).get("asset_version") or "")
        asset_ids.add(ref.owner_node_id)
        video_file_to_version[(ref.owner_node_id, ref.file_id)] = ver
        summaries[(ref.owner_node_id, ver)] = {
            "reports_count": 0,
            "previews_count": 0,
            "atlases_count": 0,
            "runs_count": 0,
            "latest_run_status": None,
            "has_analysis": False,
            "has_artifacts": False,
        }

    ref_rows = await session.scalars(
        select(FileReference).where(
            FileReference.owner_node_id.in_(asset_ids),
            FileReference.reference_type.in_(_ANALYSIS_REF_TYPES),
        )
    )
    for item in ref_rows:
        meta = item.metadata_json or {}
        ver = meta.get("asset_version")
        if not ver and meta.get("source_file_id") is not None:
            try:
                ver = video_file_to_version.get((item.owner_node_id, int(meta["source_file_id"])))
            except (TypeError, ValueError):
                ver = None
        summary = summaries.get((item.owner_node_id, str(ver or "")))
        if summary is None:
            continue
        if item.reference_type in ("cutscene_visual_description", "cutscene_visual_title"):
            summary["reports_count"] += 1
        elif item.reference_type == "cutscene_preview":
            summary["previews_count"] += 1
        elif item.reference_type == "video_frame_atlas":
            summary["atlases_count"] += 1

    run_rows = await session.scalars(
        select(ProcessingRun)
        .join(Processor, Processor.id == ProcessingRun.processor_id)
        .where(
            Processor.key == "cutscene_vision",
            ProcessingRun.target_node_id.in_(asset_ids),
        )
        .order_by(ProcessingRun.id.desc())
    )
    for run in run_rows:
        ver = str((run.metadata_json or {}).get("asset_version") or "")
        summary = summaries.get((run.target_node_id, ver))
        if summary is None:
            continue
        summary["runs_count"] += 1
        if summary["latest_run_status"] is None:
            summary["latest_run_status"] = run.status

    for summary in summaries.values():
        summary["has_analysis"] = bool(
            summary["reports_count"] > 0
            or summary["runs_count"] > 0
            or summary["atlases_count"] > 0
        )
        summary["has_artifacts"] = bool(
            summary["has_analysis"] or summary["previews_count"] > 0
        )
    return summaries


async def _asset_cutscenes(
    session: AsyncSession,
    asset_ids: set[int],
) -> dict[int, list[dict[str, Any]]]:
    if not asset_ids:
        return {}
    mapping: dict[int, list[dict[str, Any]]] = {aid: [] for aid in asset_ids}
    seen: dict[int, set[int]] = {aid: set() for aid in asset_ids}

    variants, assets_edge = aliased(Edge), aliased(Edge)
    variant_type, asset_type = aliased(RelationType), aliased(RelationType)
    graph_rows = await session.execute(
        select(
            assets_edge.to_node_id,
            Cutscene.node_id,
            Cutscene.cg_name,
            Cutscene.cg_file,
        )
        .join(variants, variants.from_node_id == Cutscene.node_id)
        .join(variant_type, variant_type.id == variants.relation_type_id)
        .join(assets_edge, assets_edge.from_node_id == variants.to_node_id)
        .join(asset_type, asset_type.id == assets_edge.relation_type_id)
        .where(
            assets_edge.to_node_id.in_(asset_ids),
            variants.layer == "source",
            assets_edge.layer == "source",
            variant_type.key == "has_variant",
            asset_type.key == "references_asset",
        )
        .order_by(Cutscene.cg_name.asc(), Cutscene.node_id.asc())
    )
    for asset_id, scene_id, cg_name, cg_file in graph_rows:
        if scene_id in seen.get(asset_id, set()):
            continue
        seen[asset_id].add(scene_id)
        mapping[asset_id].append(
            {"node_id": scene_id, "cg_name": cg_name, "cg_file": cg_file}
        )

    direct_rows = await session.execute(
        select(
            Cutscene.media_asset_node_id,
            Cutscene.node_id,
            Cutscene.cg_name,
            Cutscene.cg_file,
        )
        .where(Cutscene.media_asset_node_id.in_(asset_ids))
        .order_by(Cutscene.cg_name.asc(), Cutscene.node_id.asc())
    )
    for asset_id, scene_id, cg_name, cg_file in direct_rows:
        if asset_id is None or scene_id in seen.get(asset_id, set()):
            continue
        seen[asset_id].add(scene_id)
        mapping[asset_id].append(
            {"node_id": scene_id, "cg_name": cg_name, "cg_file": cg_file}
        )
    return mapping


async def _logical_plan(session: AsyncSession, scene_id: int, version: str):
    flow_ref = await session.scalar(
        select(FileReference).where(
            FileReference.owner_node_id == scene_id,
            FileReference.reference_type.in_(publication_types("cutscene_flow")),
            FileReference.metadata_json["asset_version"].astext == version,
            FileReference.metadata_json["publication_complete"].as_boolean().is_(True),
        ).order_by(publication_order(FileReference.reference_type), FileReference.id.desc()).limit(1)
    )
    if flow_ref is None:
        raise ValueError("A complete published playback flow is required for this cutscene")
    flow = PlaybackFlow.model_validate(flow_ref.metadata_json["flow"])
    keys = {node.asset for node in flow.nodes if isinstance(node, Clip)}
    publication = flow_ref.metadata_json.get("publication_references") or []
    reference_files = {item["id"]: item["file_id"] for item in publication if isinstance(item, dict)
                       and isinstance(item.get("id"), int) and isinstance(item.get("file_id"), int)}
    reference_ids = list(reference_files)
    if not reference_ids:
        raise ValueError("Playback publication does not identify its video references")
    rows = await session.execute(
        select(Node.canonical_key, Node.id, FileReference, FileObject.sha256)
        .join(FileReference, FileReference.owner_node_id == Node.id)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .where(Node.canonical_key.in_(keys), FileReference.id.in_(reference_ids),
               FileReference.reference_type.in_(publication_types("cutscene_video")),
               FileReference.metadata_json["asset_version"].astext == version)
    )
    sources, references = {}, {}
    for key, node_id, ref, digest in rows:
        if key in sources or not digest or reference_files.get(ref.id) != ref.file_id:
            raise ValueError("Ambiguous or unverified video in playback publication")
        sources[key] = {"asset_node_id": node_id, "file_id": ref.file_id,
                        "source_sha256": digest.hex(),
                        "duration_seconds": ref.metadata_json.get("duration_seconds")}
        references[node_id] = ref
    if set(sources) != keys:
        raise ValueError("Playback publication is missing one or more source videos")
    plan = build_analysis_plan(scene_id, version, flow.model_dump(), sources,
                               flow_reference_id=flow_ref.id)
    return plan, references[plan["segments"][0]["asset_node_id"]], references


async def _existing_whole_scene_job(session, plan, references):
    for asset_id in plan["selection"]["reusable_whole_scene_asset_ids"]:
        ref = references[asset_id]
        report = await visual_reference(session, asset_id, ref.metadata_json["asset_version"])
        source = await session.get(FileObject, ref.file_id)
        if source is None or source.sha256 is None:
            continue
        if (report is None or report.metadata_json.get("source_file_id") != ref.file_id
                or report.metadata_json.get("source_sha256") != source.sha256.hex()):
            previous = await session.scalar(select(ProcessingRun.id).join(Processor).where(
                Processor.key == "cutscene_vision", ProcessingRun.target_node_id == asset_id,
                ProcessingRun.metadata_json["asset_version"].astext == ref.metadata_json["asset_version"],
                ProcessingRun.metadata_json["source_sha256"].astext == source.sha256.hex(),
            ).limit(1))
            if previous:
                raise ValueError("A legacy analysis of this whole-scene alternative already exists; reconcile it first")
            continue
        digest = report.metadata_json.get("source_sha256")
        run = await session.scalar(select(ProcessingRun).join(Processor).where(
            Processor.key == "cutscene_vision", ProcessingRun.target_node_id == asset_id,
            ProcessingRun.status == "completed",
            ProcessingRun.metadata_json["asset_version"].astext == ref.metadata_json["asset_version"],
            ProcessingRun.metadata_json["source_sha256"].astext == digest,
        ).order_by(ProcessingRun.id.desc()).limit(1))
        if run:
            return run, report
        raise ValueError("A complete report exists without a matching completed run; reconcile before reanalysis")
    return None


async def _guard_partial_legacy_jobs(session, version, references):
    # A partial asset-level run is not a reusable logical report, but starting
    # the whole scene concurrently could bill the same video twice.
    for asset_id, ref in references.items():
        source = await session.get(FileObject, ref.file_id)
        if source is None or source.sha256 is None:
            raise ValueError("A playback source is no longer content-addressed")
        previous = await session.scalar(select(ProcessingRun.id).join(Processor).where(
            Processor.key == "cutscene_vision", ProcessingRun.target_node_id == asset_id,
            ProcessingRun.metadata_json["asset_version"].astext == version,
            ProcessingRun.metadata_json["source_sha256"].astext == source.sha256.hex(),
        ).limit(1))
        if previous:
            raise ValueError("A legacy analysis of a scene part already exists; reconcile it first")


@router.get("/assets")
async def assets(
    q: str = Query("", max_length=128),
    before: int | None = None,
    analyzed_only: bool = Query(False),
    limit: int = Query(30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    version = FileReference.metadata_json["asset_version"].astext
    latest = (
        select(FileReference.id)
        .where(
            FileReference.reference_type.in_(
                publication_types("cutscene_video")),
            FileReference.owner_node_id.is_not(None),
        )
        .distinct(FileReference.owner_node_id, version)
        .order_by(
            FileReference.owner_node_id,
            version,
            publication_order(FileReference.reference_type),
            FileReference.id.desc(),
        )
    )
    statement = (
        select(FileReference, Node.canonical_key)
        .join(Node, Node.id == FileReference.owner_node_id)
        .where(FileReference.id.in_(latest))
        .order_by(FileReference.id.desc())
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(FileReference.id < before)
    if q:
        variants, assets_edge = aliased(Edge), aliased(Edge)
        variant_type, asset_type = aliased(RelationType), aliased(RelationType)
        scene_text_match = or_(
            Cutscene.cg_name.icontains(q, autoescape=True),
            Cutscene.cg_file.icontains(q, autoescape=True),
        )
        has_graph_scene = (
            select(Cutscene.node_id)
            .join(variants, variants.from_node_id == Cutscene.node_id)
            .join(variant_type, variant_type.id == variants.relation_type_id)
            .join(assets_edge, assets_edge.from_node_id == variants.to_node_id)
            .join(asset_type, asset_type.id == assets_edge.relation_type_id)
            .where(
                assets_edge.to_node_id == FileReference.owner_node_id,
                variants.layer == "source",
                assets_edge.layer == "source",
                variant_type.key == "has_variant",
                asset_type.key == "references_asset",
                scene_text_match,
            )
            .exists()
        )
        has_direct_scene = (
            select(Cutscene.node_id)
            .where(
                Cutscene.media_asset_node_id == FileReference.owner_node_id,
                scene_text_match,
            )
            .exists()
        )
        q_clauses = [
            Node.canonical_key.icontains(q, autoescape=True),
            has_graph_scene,
            has_direct_scene,
        ]
        if q.isdigit():
            q_num = int(q)
            q_clauses.append(FileReference.owner_node_id == q_num)
            q_clauses.append(
                select(Cutscene.node_id)
                .join(variants, variants.from_node_id == Cutscene.node_id)
                .join(variant_type, variant_type.id == variants.relation_type_id)
                .join(assets_edge, assets_edge.from_node_id == variants.to_node_id)
                .join(asset_type, asset_type.id == assets_edge.relation_type_id)
                .where(
                    assets_edge.to_node_id == FileReference.owner_node_id,
                    Cutscene.node_id == q_num,
                    variants.layer == "source",
                    assets_edge.layer == "source",
                    variant_type.key == "has_variant",
                    asset_type.key == "references_asset",
                )
                .exists()
            )
        statement = statement.where(or_(*q_clauses))
    if analyzed_only:
        analysis_ref = aliased(FileReference)
        has_ref = (
            select(analysis_ref.id)
            .where(
                analysis_ref.owner_node_id == FileReference.owner_node_id,
                analysis_ref.reference_type.in_(_ANALYSIS_REF_TYPES),
                or_(
                    analysis_ref.metadata_json["asset_version"].astext == version,
                    analysis_ref.metadata_json["source_file_id"].as_integer() == FileReference.file_id,
                ),
            )
            .exists()
        )
        has_run = (
            select(ProcessingRun.id)
            .join(Processor, Processor.id == ProcessingRun.processor_id)
            .where(
                Processor.key == "cutscene_vision",
                ProcessingRun.target_node_id == FileReference.owner_node_id,
                ProcessingRun.metadata_json["asset_version"].astext == version,
            )
            .exists()
        )
        statement = statement.where(or_(has_ref, has_run))
    rows = (await session.execute(statement)).all()
    summaries = await _asset_analysis_summaries(session, rows)
    cutscenes_by_asset = await _asset_cutscenes(
        session, {ref.owner_node_id for ref, _ in rows}
    )
    return {
        "enabled": get_agent_settings().vision_enabled,
        "assets": [
            {
                "id": ref.owner_node_id,
                "reference": key,
                "game_version": ref.metadata_json["asset_version"],
                "cutscenes": cutscenes_by_asset.get(ref.owner_node_id, []),
                **summaries.get(
                    (ref.owner_node_id, str(ref.metadata_json["asset_version"] or "")),
                    {
                        "reports_count": 0,
                        "previews_count": 0,
                        "atlases_count": 0,
                        "runs_count": 0,
                        "latest_run_status": None,
                        "has_analysis": False,
                        "has_artifacts": False,
                    },
                ),
            }
            for ref, key in rows
        ],
        "next_before": rows[-1][0].id if len(rows) == limit else None,
    }


@router.post("/jobs", status_code=202, dependencies=[Depends(require_csrf)])
async def create_visual_jobs(request: VisualRequest, session: AsyncSession = Depends(get_session)):
    settings = get_agent_settings()
    if not settings.vision_enabled:
        raise HTTPException(
            409, "Enable AGENT_VISION_ENABLED on the API and visual worker")
    ids = set(request.asset_node_ids)
    if any(asset_id <= 0 for asset_id in ids):
        raise HTTPException(422, "Asset IDs must be positive")
    references = list(
        await session.scalars(
            select(FileReference)
            .where(
                FileReference.reference_type.in_(
                    publication_types("cutscene_video")),
                FileReference.owner_node_id.in_(ids),
                FileReference.metadata_json["asset_version"].astext == request.game_version,
            )
            .distinct(FileReference.owner_node_id)
            .order_by(FileReference.owner_node_id, publication_order(FileReference.reference_type), FileReference.id.desc())
        )
    )
    if {ref.owner_node_id for ref in references} != ids:
        raise HTTPException(
            404, "An imported video is missing for the selected version")
    try:
        associations = await _asset_cutscenes(session, ids)
        scene_ids = set()
        legacy = []
        for ref in references:
            scenes = associations.get(ref.owner_node_id, [])
            if len(scenes) > 1:
                raise ValueError("Asset belongs to multiple cutscenes; select a specific cutscene")
            if scenes:
                scene_ids.add(scenes[0]["node_id"])
            else:
                legacy.append(ref)
        prepared = [await _logical_plan(session, scene_id, request.game_version)
                    for scene_id in sorted(scene_ids)]
        covered = set().union(*(refs.keys() for _, _, refs in prepared)) if prepared else set()
        if any(ref.owner_node_id not in covered for ref in references if associations.get(ref.owner_node_id)):
            raise ValueError("Selected asset does not belong to its published playback flow")
        await lock_visual_sources(session, covered | scene_ids, request.game_version)
        jobs = []
        for plan, ref, source_refs in prepared:
            reused = await _existing_whole_scene_job(session, plan, source_refs)
            if reused:
                run, report = reused
                jobs.append({**public_job(run), "reused_report_id": report.id,
                             "cutscene_node_id": plan["cutscene_node_id"],
                             "reused_from_asset_node_id": run.target_node_id})
            else:
                await _guard_partial_legacy_jobs(session, request.game_version, source_refs)
                run = await enqueue_logical_visual_job(session, plan, ref, settings)
                jobs.append(public_job(run))
        jobs.extend([public_job(await enqueue_visual_job(session, ref, settings)) for ref in legacy])
        await session.commit()
    except ValueError as error:
        await session.rollback()
        raise HTTPException(422, str(error)) from error
    return {"jobs": jobs}


@router.get("/jobs")
async def visual_jobs(
    before: int | None = None,
    run_id: int | None = None,
    limit: int = Query(30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    statement = (
        select(ProcessingRun, Node.canonical_key)
        .join(Processor, Processor.id == ProcessingRun.processor_id)
        .outerjoin(Node, Node.id == ProcessingRun.target_node_id)
        .where(Processor.key == "cutscene_vision")
        .order_by(ProcessingRun.id.desc())
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(ProcessingRun.id < before)
    if run_id is not None:
        statement = statement.where(ProcessingRun.id == run_id)
    rows = (await session.execute(statement)).all()
    return {
        "jobs": [{**public_job(run), "reference": key} for run, key in rows],
        "next_before": rows[-1][0].id if len(rows) == limit else None,
    }


@router.post("/jobs/{run_id}/resume", dependencies=[Depends(require_csrf)])
async def resume_visual(
    run_id: int, request: VisualResumeRequest, session: AsyncSession = Depends(get_session)
):
    if not get_agent_settings().vision_enabled:
        raise HTTPException(
            409, "Enable AGENT_VISION_ENABLED on the API and visual worker")
    try:
        run = await resume_visual_job(session, run_id, request.output_tokens)
        await session.commit()
    except ValueError as error:
        await session.rollback()
        raise HTTPException(422, str(error)) from error
    return public_job(run)


@router.delete("/assets/{asset_id}/analysis", dependencies=[Depends(require_csrf)])
async def delete_cutscene_analysis(
    asset_id: int,
    game_version: str | None = Query(
        default=None, min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
):
    if asset_id <= 0:
        raise HTTPException(422, "Asset ID must be positive")
    node = await session.get(Node, asset_id)
    if node is None:
        raise HTTPException(404, "Cutscene asset node not found")
    storage = S3Storage(get_settings())
    try:
        summary = await wipe_cutscene_analysis(session, storage, asset_id, game_version)
        await session.commit()
    except ValueError as error:
        await session.rollback()
        raise HTTPException(409, str(error)) from error
    return summary
