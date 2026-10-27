"""Disposable model extraction, headless conversion and atomic bundle publication."""

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.character_models import CharacterModelJob
from wuwa_story.storage.hashing import content_object_key
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.broker import job_lock
from wuwa_story_worker.client_assets import validate_plan
from wuwa_story_worker.entity_media import tool
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.media_provenance import sha256, temporary_entity_workspace
from wuwa_story_worker.media_workspace import finish_writes
from wuwa_story_worker.model_publication import ensure_glb_download, publish_viewer
from wuwa_story_worker.model_sources import (
    exported_package,
    glb_summary,
    model_sources,
    spring_profile,
)
from wuwa_story_worker.tooling import asset_workspace, tool_path


async def source_row(session, release_id, path, identity):
    row = await session.scalar(
        select(SourceRecord.data)
        .join(SourceFile)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == path,
            SourceRecord.data["Id"].astext == str(identity),
        )
        .order_by(SourceRecord.row_index)
        .limit(1)
    )
    if row is None:
        raise ValueError(f"Character model source is missing in the requested release: {path}")
    return row


async def extract_model(root, sources, fmodel, converter, include_animations=True):
    model_dir = sources["model"].rsplit("/", 1)[0]
    filters = [model_dir + "/", *sources["blueprints"]]
    if include_animations:
        # Derive the authored character root, including sibling CommonAnim/Seq
        # directories. Exact skeleton references filter variants after conversion.
        character_dir = "/".join(sources["model"].split("/")[:5])
        gender_dir = "/".join(sources["model"].split("/")[:4])
        filters = [
            character_dir + "/",
            *sources["blueprints"],
            # Shared movement sets are named BaseAnim, BaseAnim2, etc.
            gender_dir + "/BaseAnim",
            "ACLAnim",
            "ACLPlugin.uplugin",
            # Candidate discovery only: NPC/cinematic paths can be outside
            # the authored root. Ownership is checked by skeleton metadata.
            character_dir.rsplit("/", 1)[1],
        ]
    receipt = await export_assets(root, fmodel, "|".join(filters))
    raw = receipt.parent / "files"
    converted, cooked = root / "converted", root / "source-json"
    converted.mkdir()
    cooked.mkdir()
    inputs = ["-i", (raw / "Client").resolve()]
    if (raw / "Engine").is_dir():
        inputs += ["-i", (raw / "Engine").resolve()]
    common = [converter, *inputs, "-g", "GAME_WutheringWaves", "-p", "*.uasset"]
    await tool([*common, "-f", "json", "-o", cooked.resolve(), "-y"], root / "metadata.log")
    await tool(
        [
            *common,
            "--mesh-format",
            "UEFormat",
            "--compression",
            "None",
            "--mesh-quality",
            "Highest",
            "-o",
            converted.resolve(),
            "-y",
        ],
        root / "convert.log",
    )
    model = converted / "Client/Content" / (sources["model"] + ".uemodel")
    if not model.is_file():
        raise ValueError("The exact authored mesh was not exported from this client")
    return model, converted, cooked, json.loads(receipt.read_text(encoding="utf-8"))


async def run_blender(args, root, timeout=1800):
    """Preserve only Blender's bounded diagnostic before scratch is removed."""
    log = root / "blender.log"
    try:
        return await tool(args, log, timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        reason = (
            f"exited with code {exc.returncode}"
            if isinstance(exc, subprocess.CalledProcessError)
            else f"timed out after {exc.timeout} seconds"
        )
        try:
            with log.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 8192))
                tail = stream.read(8192)
            # Strip CSI, OSC and other terminal escapes, then unsafe controls.
            tail = re.sub(
                rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]",
                b"",
                tail,
            )
            tail = re.sub(rb"[\x00-\x08\x0b-\x1f\x7f]", b"", tail)
            diagnostic = tail.decode("utf-8", errors="replace")
            diagnostic = diagnostic.encode("utf-8")[-8192:].decode("utf-8", errors="ignore")
        except OSError:
            diagnostic = "Blender log unavailable"
        raise RuntimeError(f"Blender {reason}; blender.log (last 8 KiB):\n{diagnostic}") from exc


async def build_bundle(root, sources, include_animations=True):
    fmodel, converter = tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH")
    blender = tool_path("WUWA_BLENDER_PATH")
    plugin = Path(os.getenv("WUWA_UEFORMAT_PLUGIN", "/app/tools/ueformat/plugins/blender"))
    if not (plugin / "io_scene_ueformat").is_dir():
        raise ValueError("UEFormat importer unavailable; use the dedicated model worker image")
    model, converted, cooked, manifest = await extract_model(
        root, sources, fmodel, converter, include_animations
    )
    output = root / "bundle"
    output.mkdir()
    model_metadata = json.loads(
        (cooked / "Client/Content" / (sources["model"] + ".json")).read_text(encoding="utf-8-sig")
    )
    skeletons = [
        row.get("Properties", {}).get("Skeleton", {}).get("ObjectPath")
        for row in model_metadata
        if row.get("Type") == "SkeletalMesh"
        and row.get("Name") == sources["model"].rsplit("/", 1)[-1]
    ]
    if len(skeletons) != 1 or not skeletons[0]:
        raise ValueError("The exported character mesh has no exact skeleton reference")
    skeleton = exported_package(skeletons[0])
    await run_blender(
        [
            blender,
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            Path(__file__).with_name("model_bundle.py").resolve(),
            "--",
            "--model",
            model.resolve(),
            "--converted",
            converted.resolve(),
            "--source-json",
            cooked.resolve(),
            "--skeleton",
            "/Game/" + skeleton,
            "--ueformat-plugin",
            plugin.resolve(),
            "--output",
            output.resolve(),
            "--with-animations" if include_animations else "--rig-only",
            "--preview-texture-size",
            "512",
        ],
        root,
        1800,
    )
    profile = None
    for blueprint in sources["blueprints"]:
        path = cooked / "Client/Content" / (blueprint + ".json")
        if path.is_file():
            candidate = spring_profile(
                json.loads(path.read_text(encoding="utf-8-sig")),
                blueprint,
                sha256(path),
                expected_skeleton=skeleton,
            )
            if candidate["groups"]:
                if profile is not None:
                    raise ValueError(
                        "Multiple authored physics profiles require explicit composition"
                    )
                profile = candidate
    if profile:
        (output / "physics.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    summaries = {
        kind: glb_summary(output / name)
        for kind, name in [("preview", "preview.glb"), ("download", "character.glb")]
    }
    if include_animations:
        summaries["animations"] = glb_summary(output / "animations.glb", allow_animations=True)
    if summaries["preview"]["bytes"] > summaries["download"]["bytes"]:
        raise ValueError("Preview unexpectedly exceeds the full-resolution model")
    provenance = {
        "source_files": manifest["files"],
        "model": sources["model"],
        "blueprints": sources["blueprints"],
        "tools": {
            name: sha256(path)
            for name, path in [("fmodel", fmodel), ("converter", converter), ("blender", blender)]
        },
        "recipe": sha256(Path(__file__).with_name("model_bundle.py")),
        "preview_texture_size": 512,
        "include_animations": include_animations,
    }
    if include_animations:
        provenance["animations"] = json.loads(
            (output / "animations.json").read_text(encoding="utf-8")
        )
        provenance["animation_coverage"] = json.loads(
            (output / "report.json").read_text(encoding="utf-8")
        )["animation_coverage"]
        if provenance["animations"] != summaries["animations"]:
            raise ValueError("Animation report disagrees with exported clips")
        provenance["animation_recipe"] = sha256(Path(__file__).with_name("model_animations.py"))
    return output, summaries, provenance


async def publish_bundle(job, output, summaries, provenance):
    animation_count = 0
    if job.include_animations:
        actual = glb_summary(output / "animations.glb", allow_animations=True)
        if summaries.get("animations") != actual:
            raise ValueError("Animation summary changed before publication")
        animation_count = actual["animations"]
    elif "animations" in summaries or (output / "animations.glb").exists():
        raise ValueError("Unexpected animations for a rig-only job")
    storage = S3Storage(get_settings())
    service = FileRegistrationService(storage)
    files = [
        ("preview", output / "preview.glb", "unknown", "model/gltf-binary"),
        ("download", output / "character.glb", "unknown", "model/gltf-binary"),
    ]
    if job.include_animations:
        files.append(("animations", output / "animations.glb", "unknown", "model/gltf-binary"))
    if (output / "physics.json").is_file():
        files.append(("physics", output / "physics.json", "raw_json", "application/json"))
    prepared = [await service.prepare_file(path, mime) for _, path, _, mime in files]
    viewer_key = await publish_viewer(storage)
    await ensure_glb_download(
        storage, content_object_key(prepared[1].digest), prepared[1].digest.hex()
    )
    if job.include_animations:
        await ensure_glb_download(
            storage, content_object_key(prepared[2].digest), prepared[2].digest.hex()
        )
    async with SessionFactory() as session:
        run = await session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == job.run_id).with_for_update()
        )
        if run is None or CharacterModelJob.model_validate(run.metadata_json.get("payload")) != job:
            raise ValueError("Model job identity changed before publication")
        if run.status in {"completed", "cancelled"}:
            return
        published = {}
        for (kind, path, file_type, mime), ready in zip(files, prepared, strict=True):
            file = await service.register_file(
                session, path, file_type, mime_type=mime, prepared=ready
            )
            reference = await service.register_reference(
                session,
                file_id=file.id,
                reference_type="character_model_" + kind,
                owner_node_id=job.owner_node_id,
                release_id=job.release_id,
                source_path="/Game/" + provenance["model"],
            )
            published[kind] = file.id
            reference.metadata_json = {
                "model_bundle_id": str(job.run_id),
                "viewer_object_key": viewer_key,
                "asset_version": job.asset_version,
                "download_id": job.download_id,
                "tier": job.tier,
                "game_source": provenance,
                "animation_count": animation_count,
            }
        await service.register_variant(
            session,
            published["download"],
            published["preview"],
            "character_model_preview",
            job.run_id,
        )
        run.status, run.error, run.finished_at = "completed", None, datetime.now(UTC)
        run.raw_output = {
            "files": summaries,
            "physics": (output / "physics.json").is_file(),
            "animations": animation_count,
            "temporary_exports_removed": True,
        }
        await session.commit()


async def process_character_model(payload):
    job = CharacterModelJob.model_validate(payload)
    async with (
        engine.connect() as connection,
        job_lock(connection, 707, job.owner_node_id % 2147483648),
    ):
        async with SessionFactory() as session:
            run = await session.get(ProcessingRun, job.run_id)
            if (
                run is None
                or CharacterModelJob.model_validate(run.metadata_json.get("payload")) != job
            ):
                raise ValueError("Model job payload does not match its durable run")
            if run.status in {"completed", "cancelled"}:
                return
            role = await source_row(
                session,
                job.release_id,
                "BinData/role/roleinfo.json",
                int(job.character_key.split(":")[1]),
            )
            preload = await source_row(
                session,
                job.release_id,
                "BinData/model_config_preload/modelconfigpreload.json",
                role.get("MeshId"),
            )
            sources = model_sources(role, preload)
        client = (
            asset_workspace() / "assets" / f"{job.asset_version}-{job.tier}-{job.download_id[:16]}"
        )
        plan = json.loads((client / "plan.json").read_text(encoding="utf-8"))
        validate_plan(plan)
        status = json.loads((client / "status.json").read_text(encoding="utf-8"))
        if (
            plan.get("id") != job.download_id
            or plan.get("version") != job.asset_version
            or plan.get("tier") != job.tier
            or status.get("state") != "downloaded"
            or status.get("id") != job.download_id
        ):
            raise ValueError("A matching ready client is required for model extraction")
        await update_admin_run(
            job.run_id, "character_model", "running", raw_output={"stage": "export"}
        )

        async def execute():
            with temporary_entity_workspace(client) as root:
                output, summaries, provenance = await build_bundle(
                    root, sources, include_animations=job.include_animations
                )
                await publish_bundle(job, output, summaries, provenance)

        # Never delete scratch while an owned subprocess/thread is still writing.
        await finish_writes(execute())
