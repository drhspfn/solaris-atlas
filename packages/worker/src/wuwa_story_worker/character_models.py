"""Disposable model extraction, headless conversion and atomic bundle publication."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
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
from wuwa_story_worker.model_animation_merge import merge_animation_glbs
from wuwa_story_worker.model_facial import validate_facial_pack
from wuwa_story_worker.model_forms import resolve_model_forms
from wuwa_story_worker.model_publication import ensure_glb_download, publish_viewer
from wuwa_story_worker.model_sources import (
    exported_package,
    glb_summary,
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


async def source_forms(session, release_id, role):
    morphs = list(
        await session.scalars(
            select(SourceRecord.data)
            .join(SourceFile)
            .where(
                SourceRecord.release_id == release_id,
                SourceFile.logical_source_path == "BinData/role/rolemorph.json",
                SourceRecord.data["RoleId"].astext == str(role["Id"]),
            )
        )
    )
    mesh_ids = {role.get("MeshId"), role.get("UiMeshId"), *(r.get("MeshId") for r in morphs)}
    preloads = list(
        await session.scalars(
            select(SourceRecord.data)
            .join(SourceFile)
            .where(
                SourceRecord.release_id == release_id,
                SourceFile.logical_source_path
                == "BinData/model_config_preload/modelconfigpreload.json",
                SourceRecord.data["Id"].astext.in_([str(value) for value in mesh_ids if value]),
            )
        )
    )
    return resolve_model_forms(role, preloads, morphs)


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
            # Cooked clips can use Engine compression settings as well as ACL.
            "Engine/Content/Animation/DefaultAnim",
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


async def model_progress(run_id, stage, **details):
    if run_id is not None:
        await update_admin_run(
            run_id, "character_model", "running", raw_output={"stage": stage, **details}
        )


def component_model(component, converted, cooked, skeleton):
    metadata_path = cooked / "Client/Content" / (component + ".json")
    component_path = converted / "Client/Content" / (component + ".uemodel")
    if not component_path.is_file() or not metadata_path.is_file():
        raise ValueError("An authored character component was not exported")
    rows = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    component_skeletons = [
        row.get("Properties", {}).get("Skeleton", {}).get("ObjectPath")
        for row in rows
        if row.get("Type") == "SkeletalMesh" and row.get("Name") == component.rsplit("/", 1)[-1]
    ]
    if (
        len(component_skeletons) != 1
        or not component_skeletons[0]
        or exported_package(component_skeletons[0]) != skeleton
    ):
        raise ValueError("Character component uses a different skeleton")
    return component_path.resolve()


async def build_bundle(root, sources, include_animations=True, *, run_id=None):
    async def progress(stage, **details):
        await model_progress(
            run_id, stage, form_id=sources.get("id"), form_label=sources.get("label"), **details
        )

    fmodel, converter = tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH")
    blender = tool_path("WUWA_BLENDER_PATH")
    plugin = Path(os.getenv("WUWA_UEFORMAT_PLUGIN", "/app/tools/ueformat/plugins/blender"))
    if not (plugin / "io_scene_ueformat").is_dir():
        raise ValueError("UEFormat importer unavailable; use the dedicated model worker image")
    await progress("extract")
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
    extra_models = [
        component_model(component, converted, cooked, skeleton)
        for component in sources.get("extra_models", [])
    ]
    command = [
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
        *[arg for component in extra_models for arg in ("--extra-model", component)],
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
        "--preview-texture-size",
        "512",
    ]
    plan_path = output / "animation-plan.json"
    await progress("rig_and_animation_plan" if include_animations else "rig")
    await run_blender(
        [*command, "--rig-only", *(["--animation-plan", plan_path] if include_animations else [])],
        root,
        900,
    )
    batch_count = 0
    if include_animations:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        clips = plan["clips"]
        if not clips:
            raise ValueError("No playable clips in the verified animation plan")
        batch_size = 20
        batch_count = (len(clips) + batch_size - 1) // batch_size
        parts = []
        for index in range(batch_count):
            await progress(
                "animation_batch",
                batch=index + 1,
                batches=batch_count,
                clips=len(clips),
                batch_size=batch_size,
            )
            started = time.monotonic()
            await run_blender(
                [
                    *command,
                    "--animation-plan",
                    plan_path,
                    "--animation-batch",
                    str(index),
                    "--animation-batch-size",
                    str(batch_size),
                ],
                root,
                600,
            )
            part = output / "animation-parts" / f"part-{index:04d}.glb"
            stats = glb_summary(part, allow_animations=True)
            expected = min(batch_size, len(clips) - index * batch_size)
            if stats["animations"] != expected:
                raise ValueError("Animation batch lost clips")
            parts.append(part)
            await progress(
                "animation_batch_completed",
                batch=index + 1,
                batches=batch_count,
                seconds=round(time.monotonic() - started, 2),
                clips=len(clips),
            )
        await progress("merge_animation_batches", batches=batch_count)
        await asyncio.to_thread(merge_animation_glbs, parts, output / "animations.glb")
        summary = glb_summary(output / "animations.glb", allow_animations=True)
        if summary["animations"] != len(clips):
            raise ValueError("Merged animation collection lost clips")
        (output / "animations.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
        "extra_models": sources.get("extra_models", []),
        "blueprints": sources["blueprints"],
        "tools": {
            name: sha256(path)
            for name, path in [("fmodel", fmodel), ("converter", converter), ("blender", blender)]
        },
        "recipe": sha256(Path(__file__).with_name("model_bundle.py")),
        "preview_texture_size": 512,
        "include_animations": include_animations,
        "animation_batches": batch_count,
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
        provenance["facial_recipe"] = sha256(Path(__file__).with_name("model_facial.py"))
        provenance["animation_merge_recipe"] = sha256(
            Path(__file__).with_name("model_animation_merge.py")
        )
    return output, summaries, provenance


def publication_files(job, output, summaries):
    """Validate every form before uploads or a database publication can begin."""
    if job.include_animations:
        actual = glb_summary(output / "animations.glb", allow_animations=True)
        if summaries.get("animations") != actual:
            raise ValueError("Animation summary changed before publication")
        animation_count = actual["animations"]
    elif "animations" in summaries or (output / "animations.glb").exists():
        raise ValueError("Unexpected animations for a rig-only job")
    else:
        animation_count = 0
    files = [
        ("preview", output / "preview.glb", "unknown", "model/gltf-binary"),
        ("download", output / "character.glb", "unknown", "model/gltf-binary"),
    ]
    if job.include_animations:
        files.append(("animations", output / "animations.glb", "unknown", "model/gltf-binary"))
        facial_path = output / "facial.json"
        if facial_path.is_file():
            facial = validate_facial_pack(json.loads(facial_path.read_text(encoding="utf-8")))
            names = {clip["name"] for clip in actual["clips"]}
            if any(clip["body_clip"] is not None and clip["body_clip"] not in names for clip in facial["clips"]):
                raise ValueError("Facial curves refer to an unpublished body clip")
            if facial["clips"]:
                files.append(("facial", facial_path, "raw_json", "application/json"))
    if (output / "physics.json").is_file():
        files.append(("physics", output / "physics.json", "raw_json", "application/json"))
    return files, animation_count


async def publish_bundle(job, output, summaries, provenance):
    # Legacy single-form entry point also keeps the atomic publication contract.
    await publish_forms(
        job, [({"id": "default", "label": "Default", "order": 0}, output, summaries, provenance)]
    )


async def publish_forms(job, bundles):
    if not bundles or len({form["id"] for form, *_ in bundles}) != len(bundles):
        raise ValueError("Model publication requires distinct authored forms")
    validated = [publication_files(job, output, summaries) for _, output, summaries, _ in bundles]
    storage = S3Storage(get_settings())
    service = FileRegistrationService(storage)
    prepared_forms = []
    for files, _ in validated:
        prepared = [await service.prepare_file(path, mime) for _, path, _, mime in files]
        for (kind, _, _, _), ready in zip(files, prepared, strict=True):
            if kind in {"download", "animations"}:
                await ensure_glb_download(
                    storage, content_object_key(ready.digest), ready.digest.hex()
                )
        prepared_forms.append(prepared)
    viewer_key = await publish_viewer(storage)
    async with SessionFactory() as session:
        run = await session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == job.run_id).with_for_update()
        )
        if run is None or CharacterModelJob.model_validate(run.metadata_json.get("payload")) != job:
            raise ValueError("Model job identity changed before publication")
        if run.status in {"completed", "cancelled"}:
            return
        reports = []
        for (form, output, summaries, provenance), (files, animation_count), prepared in zip(
            bundles, validated, prepared_forms, strict=True
        ):
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
                    "form_id": form["id"],
                    "form_label": form["label"],
                    "form_order": form["order"],
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
            reports.append(
                {
                    "id": form["id"],
                    "label": form["label"],
                    "files": summaries,
                    "physics": (output / "physics.json").is_file(),
                    "animations": animation_count,
                }
            )
        run.status, run.error, run.finished_at = "completed", None, datetime.now(UTC)
        run.raw_output = {
            "files": reports[0]["files"],
            "physics": reports[0]["physics"],
            "animations": sum(report["animations"] for report in reports),
            "forms": reports,
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
            forms = await source_forms(session, job.release_id, role)
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
            # Keep independently extracted rigs until all forms validate. One commit
            # publishes the complete set; any failure leaves the previous bundle intact.
            with temporary_entity_workspace(client) as staging:
                bundles = []
                for form in forms:
                    with temporary_entity_workspace(client) as root:
                        output, summaries, provenance = await build_bundle(
                            root, form, include_animations=job.include_animations, run_id=job.run_id
                        )
                        retained = staging / form["id"]
                        shutil.move(output, retained)
                    provenance["form_source"] = form["source"]
                    bundles.append((form, retained, summaries, provenance))
                await model_progress(job.run_id, "publish", forms=len(bundles))
                await publish_forms(job, bundles)

        # Never delete scratch while an owned subprocess/thread is still writing.
        await finish_writes(execute())
