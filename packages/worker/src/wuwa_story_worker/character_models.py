"""Disposable model extraction, headless conversion and atomic bundle publication."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.character_models import model_recipe, npc_model_recipe
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.character_models import CharacterModelJob
from wuwa_story.ingestion.model_animation_packs import validate_animation_packs
from wuwa_story.ingestion.model_archives import model_file_name
from wuwa_story.ingestion.model_limits import MAX_MODEL_COMPONENTS
from wuwa_story.storage.hashing import content_object_key
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.asset_export import _extract_files, export_assets
from wuwa_story_worker.broker import job_lock
from wuwa_story_worker.client_assets import validate_plan
from wuwa_story_worker.entity_media import tool
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.linked_component_forms import (
    prepare_linked_component,
    prepare_linked_components,
)
from wuwa_story_worker.media_provenance import (
    prepare_entity_workspace,
    sha256,
    temporary_entity_workspace,
)
from wuwa_story_worker.media_workspace import finish_writes
from wuwa_story_worker.model_animation_merge import merge_animation_glbs
from wuwa_story_worker.model_animation_packs import (
    fingerprint,
    install_pack,
    owned_sources,
    ownership_for,
    prepare_packs,
    write_manifest,
)
from wuwa_story_worker.model_facial import validate_facial_pack
from wuwa_story_worker.model_forms import resolve_recipe_forms
from wuwa_story_worker.model_publication import ensure_glb_download
from wuwa_story_worker.model_sources import (
    exported_package,
    glb_summary,
    npc_material_overrides,
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
    # Queued exports and downloaded local recipes must discover identical forms.
    return resolve_recipe_forms(await model_recipe(session, release_id, role["Id"]))


async def job_forms(session, job):
    if job.character_key.startswith("character:npc:"):
        recipe = await npc_model_recipe(session, job.release_id, job.owner_node_id,
                                        int(job.character_key.rsplit(":", 1)[1]))
        return resolve_recipe_forms(recipe)
    role = await source_row(session, job.release_id, "BinData/role/roleinfo.json",
                            int(job.character_key.split(":")[1]))
    return await source_forms(session, job.release_id, role)


async def extract_model(root, sources, fmodel, converter, include_animations=True, *, local_client=None,
                        additional_filters=(), metadata_only=False):
    model_dir = sources["model"].rsplit("/", 1)[0]
    filters = [model_dir + "/", *sources["blueprints"]]
    if sources.get("component_blueprint"):
        filters.append(sources["component_blueprint"])
    if include_animations:
        # Derive the authored character root, including sibling CommonAnim/Seq
        # directories. Exact skeleton references filter variants after conversion.
        character_dir = ((sources["model"].rsplit("/Model/", 1)[0]
                          if "/Model/" in sources["model"] else model_dir)
                         if sources["model"].startswith("Aki/Character/NPC/")
                         else "/".join(sources["model"].split("/")[:5]))
        gender_dir = "/".join(sources["model"].split("/")[:4])
        filters = [
            character_dir + "/",
            *sources["blueprints"],
            *([sources["component_blueprint"]] if sources.get("component_blueprint") else []),
            *sources.get("montages", []),
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
        if sources.get("component"):
            # Component paths do not follow the role hierarchy. Limit discovery
            # to the component's authored model and sibling animation folders.
            component_dir = sources["model"].rsplit("/Model/", 1)[0] if "/Model/" in sources["model"] else model_dir
            filters = [component_dir + "/", *sources["blueprints"],
                       *([sources["component_blueprint"]] if sources.get("component_blueprint") else []),
                       *sources.get("montages", []), "Engine/Content/Animation/DefaultAnim",
                       "ACLAnim", "ACLPlugin.uplugin"]
    if metadata_only:
        filters = [sources['model']]
    filters.extend(additional_filters)
    if local_client is None:
        # Nested exports (animations, accessories and materials) use their own
        # output directories but still need the pinned client manifest and game.
        if not (root / "plan.json").is_file():
            prepare_entity_workspace(root.parent, root)
        receipt = await export_assets(root, fmodel, "|".join(filters))
    else:
        output = root / "exports" / "local"
        output.mkdir(parents=True)
        (output / "files").mkdir()
        # Mount the client once for current CLIs. Older CLIs treat the pipe as a
        # literal filter and export nothing; only then retry individual filters.
        unique_filters = list(dict.fromkeys(filters))
        batch = "|".join(unique_filters) if len(unique_filters) > 1 else None
        for asset_filter in ([batch] if batch else []) + unique_filters:
            with tempfile.TemporaryDirectory(dir=output) as scratch:
                extracted = Path(scratch) / "export"
                extracted.mkdir()
                try:
                    await finish_writes(asyncio.to_thread(
                        _extract_files, root, fmodel, asset_filter, {"files": []}, extracted, Path(scratch),
                        root / "fmodel.log", game_dir=local_client["game"],
                        keys_file=local_client["keys"], processors=local_client["processors"], allow_empty=True,
                    ))
                except RuntimeError as error:
                    lines = (root / "fmodel.log").read_text(encoding="utf-8", errors="replace").splitlines()
                    failures = [line for line in lines if any(word in line.lower() for word in ("[fail]", "[error]"))]
                    diagnostic = re.sub(r"(?:0x)?[0-9a-fA-F]{32,}", "<redacted-id>", "\n".join(failures[-8:]))
                    raise RuntimeError(f"Local FModel export failed: {diagnostic or 'no supported files exported'}") from error
                exported = False
                for path in (extracted / "files").rglob("*"):
                    if not path.is_file():
                        continue
                    exported = True
                    target = output / "files" / path.relative_to(extracted / "files")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        if sha256(path) != sha256(target):
                            raise ValueError("Local game changed during model extraction")
                    else:
                        path.replace(target)
                if asset_filter == batch and exported:
                    break
        receipt = output / "manifest.json"
        receipt.write_text(json.dumps({"files": [
            {"path": path.relative_to(output / "files").as_posix(),
             "size": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted((output / "files").rglob("*")) if path.is_file()
        ]}), encoding="utf-8")
    raw = receipt.parent / "files"
    converted, cooked = root / "converted", root / "source-json"
    converted.mkdir()
    cooked.mkdir()
    inputs = ["-i", (raw / "Client").resolve()]
    if (raw / "Engine").is_dir():
        inputs += ["-i", (raw / "Engine").resolve()]
    common = [converter, *inputs, "-g", "GAME_WutheringWaves", "-p", "*.uasset"]
    await tool([*common, "-f", "json", "-o", cooked.resolve(), "-y"], root / "metadata.log")
    if metadata_only:
        return None, converted, cooked, json.loads(receipt.read_text(encoding='utf-8'))
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


async def run_blender(args, root, timeout=1800, *, run_id=None):
    """Retain the owned Blender log and prioritize exceptions over buffered stdout."""
    log = root / "blender.log"
    try:
        return await tool(args, log, timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        reason = (
            f"exited with code {exc.returncode}"
            if isinstance(exc, subprocess.CalledProcessError)
            else f"timed out after {exc.timeout} seconds"
        )
        retained = None
        if type(run_id) is int and run_id > 0:
            try:
                directory = asset_workspace() / "model-diagnostics" / str(run_id)
                directory.mkdir(parents=True, exist_ok=True)
                retained = directory / (sha256(log) + ".blender.log")
                shutil.copyfile(log, retained)
            except OSError:
                # A failed diagnostic backup must not hide the build exception.
                retained = None
        try:
            # stderr may precede thousands of buffered importer stdout lines.
            # Find exception blocks anywhere in the file, without reading an
            # unbounded log into memory. Keep the full log outside transient jobs.
            trace, collecting, exception = "", False, ""
            with log.open("r", encoding="utf-8", errors="replace") as stream:
                while line := stream.readline(16384):
                    start = line.find("Traceback")
                    if start >= 0:
                        trace, collecting = "", True
                        line = line[start:]
                    if line.startswith("[UEFORMAT]"):
                        collecting = False
                    if collecting and len(trace) < 5000:
                        trace += line[:5000 - len(trace)]
                    if re.match(r"[\w.]+(?:Error|Exception):", line):
                        exception = line[:1500]
            with log.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 8192))
                tail = stream.read(8192)
            prefix = (exception + trace).encode("utf-8") if trace or exception else b""
            if prefix and prefix not in tail:
                tail = prefix[:5500] + b"\nRecent output:\n" + tail[-2500:]
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
        location = f"; full log: {retained}" if retained else ""
        raise RuntimeError(f"Blender {reason}{location}; blender.log (last 8 KiB):\n{diagnostic}") from exc


async def model_progress(run_id, stage, **details):
    if run_id is not None:
        await update_admin_run(
            run_id, "character_model", "running", raw_output={"stage": stage, **details}
        )


def component_model(component, converted, cooked, skeleton, *, master_pose=False):
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
        or (not master_pose and exported_package(component_skeletons[0]) != skeleton)
    ):
        raise ValueError("Character component uses a different skeleton")
    return component_path.resolve()


async def discover_scene_components(root, sources, cooked, converted=None, manifest=None, *,
                                    skeleton=None, local_client=None, scene=None):
    """Resolve only authored component/effect dependencies, with bounded extraction."""
    from wuwa_story_worker.linked_models import discover
    from wuwa_story_worker.model_bundle import animation_sources

    if not sources.get("component_blueprint") or sources.get("component"):
        return []
    if skeleton is None:
        model_path = cooked / "Client/Content" / (sources["model"] + ".json")
        rows = json.loads(model_path.read_text(encoding="utf-8-sig"))
        references = [row.get("Properties", {}).get("Skeleton", {}).get("ObjectPath")
                      for row in rows if row.get("Type") == "SkeletalMesh"]
        if len(references) != 1 or not references[0]:
            raise ValueError("Linked model discovery requires an exact parent skeleton")
        skeleton = exported_package(references[0])
    fetched = set()
    for depth in range(12):
        clips = [row["source"] for row in owned_sources(
            animation_sources(cooked, "/Game/" + skeleton), sources.get("animation_ownership")).values()
                 if row["matches_skeleton"]]
        recipes = discover(cooked, {**sources, "parent_form_id": sources.get("id"),
                                    "parent_skeleton": skeleton,
                                    "animation_resources": sorted(set(clips) | set(sources.get("montages", [])))})
        missing = {resource for recipe in recipes for resource in recipe["required_resources"]}
        if not missing:
            break
        if missing <= fetched:
            raise ValueError("Explicit linked model dependencies are missing: " + ", ".join(sorted(missing)))
        fetched.update(missing)
        if len(fetched) > 512:
            raise ValueError("Linked model dependency discovery exceeds 512 resources")
        subroot = root / f"linked-metadata-{depth}"
        subroot.mkdir()
        _, _, extra_cooked, receipt = await extract_model(
            subroot, sources, tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
            False, metadata_only=True, additional_filters=sorted(missing), local_client=local_client)
        shutil.copytree(extra_cooked, cooked, dirs_exist_ok=True)
        if manifest is not None:
            manifest["files"].extend(receipt["files"])
    else:
        raise ValueError("Linked model dependencies exceed twelve discovery passes")
    if scene is not None:
        root_cues = {}
        for recipe in recipes:
            for cue in recipe["cues"]:
                if cue.get("defaults", {}).get("target") == "notifying_mesh":
                    root_cues[json.dumps(cue, sort_keys=True)] = cue
        scene.update(format="solaris-model-scene-v1", components=[], cues=list(root_cues.values()))
    results = []
    for recipe in recipes:
        if recipe["kind"] == "discovery":
            continue
        relation = {key: recipe[key] for key in (
            "id", "kind", "parent_component", "socket", "socket_transform", "default_visible",
            "source", "bindings", "cues", "diagnostics")}
        relation.update(name=recipe["id"], label=recipe["model"].rsplit("/", 1)[-1],
                        transform={key: (recipe.get("transform") or {}).get(key) or {}
                                   for key in ("location", "rotation", "scale")})
        if recipe["kind"] == "effect" and recipe["parent_component"] is None:
            relation["standalone_only"] = True
        # Effect-spawned objects have no SCS variable, but do have an exact notify identity.
        relation["source"] = {**relation["source"], "component": relation["source"].get("component") or recipe["id"]}
        child_clips = sorted({binding["child_clip"] for binding in recipe["bindings"]
                              if binding.get("child_clip") and binding.get("parent_clip")})
        results.append({"model": recipe["model"], "blueprints": [recipe["anim_blueprint"]]
                        if recipe.get("anim_blueprint") else [], "montages": child_clips,
                        "component": relation})
    if len(results) > MAX_MODEL_COMPONENTS:
        raise ValueError(f"Model scene exceeds {MAX_MODEL_COMPONENTS} authored components")
    return results


def cached_model_covers_sources(cached, sources, include_animations):
    if not cached:
        return False
    required = [sources['model'], *sources['blueprints'], *sources.get('extra_models', []),
                *sources.get('montages', [])]
    if sources.get('component_blueprint'):
        required.append(sources['component_blueprint'])
    converted, cooked = cached[1:3]
    return (all((cooked / 'Client/Content' / (path + '.json')).is_file() for path in required)
            and (converted / 'Client/Content' / (sources['model'] + '.uemodel')).is_file()
            and (not include_animations or all(
                (converted / 'Client/Content' / (clip + '.ueanim')).is_file()
                for clip in sources.get('montages', []))))


def component_animation_skeletons(sources, cooked, skeleton):
    """Resolve exact authored bindings before requesting their skeleton dependencies."""
    bindings = sources.get("component", {}).get("bindings", [])
    result = {}
    for clip in sorted({b["child_clip"] for b in bindings if b.get("child_clip") and b.get("parent_clip")}):
        path = cooked / "Client/Content" / (clip + ".json")
        if not path.is_file():
            raise ValueError(f"Authored component animation metadata missing: {clip}")
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        animations = [row for row in rows if row.get("Type") in {"AnimSequence", "AnimMontage"}
                      and row.get("Name") == clip.rsplit("/", 1)[-1]]
        reference = (animations[0].get("Properties", {}).get("Skeleton", {}).get("ObjectPath")
                     if len(animations) == 1 else None)
        if not reference:
            raise ValueError(f"Authored component animation {clip} has skeleton {reference!r}; "
                             f"mesh {sources['model']} uses {skeleton}; compatibility is unverified")
        result[clip] = exported_package(reference)
    return result


async def build_bundle(root, sources, include_animations=True, *, run_id=None, local_client=None,
                       animation_workers=1, report_progress=None, animation_cache=None, asset_cache=None,
                       animation_ownership=None):
    if animation_ownership:
        sources = {**sources, "animation_ownership": animation_ownership}
    async def progress(stage, **details):
        if report_progress is not None:
            report_progress(stage, **details)
        await model_progress(
            run_id, stage, form_id=sources.get("id"), form_label=sources.get("label"), **details
        )

    fmodel, converter = tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH")
    blender = tool_path("WUWA_BLENDER_PATH")
    plugin = Path(os.getenv("WUWA_UEFORMAT_PLUGIN", "/app/tools/ueformat/plugins/blender"))
    if not (plugin / "io_scene_ueformat").is_dir():
        raise ValueError("UEFormat importer unavailable; use the dedicated model worker image")
    await progress("extract")
    options = {"local_client": local_client} if local_client is not None else {}
    blueprint_exports = []
    if sources.get('blueprint_model'):
        from wuwa_story_worker.npc_setup import blueprint_sources

        package, visited = sources['blueprint_model'], set()
        for depth in range(8):
            if package in visited:
                raise ValueError('Cyclic NPC Blueprint model inheritance')
            visited.add(package)
            metadata_root = root / f'npc-blueprint-{depth}'
            metadata_root.mkdir()
            _, _, metadata_cooked, receipt = await extract_model(
                metadata_root, {'model': package, 'blueprints': []}, fmodel, converter,
                False, metadata_only=True, **options)
            path = metadata_cooked / 'Client/Content' / (package + '.json')
            if not path.is_file():
                raise ValueError('The authored NPC model Blueprint was not exported')
            resolved = blueprint_sources(json.loads(path.read_text(encoding='utf-8-sig')), package)
            blueprint_exports.append((package, metadata_cooked, receipt))
            if 'model' in resolved:
                sources = {**sources, **resolved,
                           'blueprints': sources['blueprints'] or resolved.get('blueprints', [])}
                break
            package = resolved['parent']
        else:
            raise ValueError('NPC Blueprint model inheritance exceeds eight authored parents')
    setup_receipt = None
    if sources.get('data_asset'):
        from wuwa_story_worker.npc_setup import setup_sources

        setup_root = root / 'npc-setup'
        setup_root.mkdir()
        _, _, setup_cooked, setup_receipt = await extract_model(
            setup_root, sources, fmodel, converter, False, metadata_only=True, **options)
        setup_path = setup_cooked / 'Client/Content' / (sources['data_asset'] + '.json')
        if not setup_path.is_file():
            raise ValueError('The authored NPC setup asset was not exported')
        sources = {**sources, **setup_sources(
            json.loads(setup_path.read_text(encoding='utf-8-sig')), sources['data_asset'])}
    component_filters = {path.rsplit('/', 1)[0] + '/' for path in sources.get('extra_models', [])}
    component_filters.update(path.rsplit('/', 1)[0] + '/'
        for slots in sources.get('part_material_overrides', {}).values() for path in slots.values())
    asset_key = ("/".join(sources["model"].split("/")[:5]).casefold(), include_animations)
    cached = asset_cache.get(asset_key) if asset_cache is not None else None
    if cached_model_covers_sources(cached, sources, include_animations):
        _, converted, cooked, manifest = cached
        model = converted / "Client/Content" / (sources["model"] + ".uemodel")
        await progress("reuse_extracted_assets")
    else:
        model, converted, cooked, manifest = await extract_model(
            root, sources, fmodel, converter, include_animations,
            additional_filters=sorted(component_filters), **options
        )
        if asset_cache is not None:
            asset_cache[asset_key] = model, converted, cooked, manifest
    for _, metadata_cooked, receipt in blueprint_exports:
        shutil.copytree(metadata_cooked, cooked, dirs_exist_ok=True)
        manifest['files'].extend(receipt['files'])
    if setup_receipt:
        manifest['files'].extend(setup_receipt['files'])
        from wuwa_story_worker.model_bundle import base_color_texture

        missing_textures = set()
        for slots in sources.get('part_material_overrides', {}).values():
            for package in slots.values():
                path = converted / 'Client/Content' / (package + '.json')
                if path.is_file():
                    reference, texture = base_color_texture(json.loads(path.read_text(encoding='utf-8-sig')), converted)
                    if reference and texture is None:
                        missing_textures.add(exported_package(reference))
        if missing_textures:
            texture_root = root / 'npc-part-textures'
            texture_root.mkdir()
            _, part_converted, part_cooked, receipt = await extract_model(
                texture_root, sources, fmodel, converter, False,
                additional_filters=sorted(missing_textures), **options)
            shutil.copytree(part_converted, converted, dirs_exist_ok=True)
            shutil.copytree(part_cooked, cooked, dirs_exist_ok=True)
            manifest['files'].extend(receipt['files'])
    if sources.get("component_blueprint"):
        from wuwa_story_worker.model_sources import npc_animation_references
        from wuwa_story_worker.npc_accessories import visibility_sources

        refs = set(visibility_sources(sources['model']))
        for blueprint in [*sources["blueprints"], *sources.get("montages", [])]:
            path = cooked / "Client/Content" / (blueprint + ".json")
            if path.is_file():
                refs.update(npc_animation_references(json.loads(path.read_text(encoding="utf-8-sig"))))
        if sources.get("animation_ownership"):
            sources = {**sources, "animation_ownership": {
                **sources["animation_ownership"], "explicit": sorted(
                    refs | set(sources.get("montages", [])) |
                    set(sources["animation_ownership"].get("explicit", [])))}}
        missing = [ref for ref in refs if not (cooked / "Client/Content" / (ref + ".json")).is_file()]
        if missing:
            extra_root = root / "referenced-animations"
            extra_root.mkdir()
            _, extra_converted, extra_cooked, extra_manifest = await extract_model(
                extra_root, sources, fmodel, converter, False, additional_filters=sorted(missing),
                metadata_only=not include_animations, **options
            )
            shutil.copytree(extra_converted, converted, dirs_exist_ok=True)
            shutil.copytree(extra_cooked, cooked, dirs_exist_ok=True)
            manifest["files"].extend(extra_manifest["files"])
            if any(not (cooked / "Client/Content" / (ref + ".json")).is_file() for ref in missing):
                raise ValueError("An explicitly referenced NPC animation was not exported")
    bone_visibility = {}
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
    component_animations = {}
    if include_animations and sources.get("component"):
        component_animations = component_animation_skeletons(sources, cooked, skeleton)
        other_skeletons = sorted({value for value in component_animations.values()
                                  if value.casefold() != skeleton.casefold()})
        if other_skeletons:
            dependency_root = root / "component-animation-skeletons"
            dependency_root.mkdir()
            _, extra_converted, extra_cooked, receipt = await extract_model(
                dependency_root, sources, fmodel, converter, True,
                additional_filters=other_skeletons, **options)
            shutil.copytree(extra_converted, converted, dirs_exist_ok=True)
            shutil.copytree(extra_cooked, cooked, dirs_exist_ok=True)
            manifest["files"].extend(receipt["files"])
    if sources.get('component_blueprint'):
        from wuwa_story_worker.model_bundle import animation_sources
        from wuwa_story_worker.npc_accessories import npc_bone_visibility_notifies, visibility_label

        for clip in owned_sources(animation_sources(cooked, '/Game/' + skeleton),
                                  sources.get("animation_ownership")).values():
            if not clip['matches_skeleton']:
                continue
            path = cooked / clip['source']
            rows = json.loads(path.read_text(encoding='utf-8-sig'))
            if not any(row.get('Type') == 'TsAnimNotifyHideBone_C' for row in rows):
                continue
            for event in npc_bone_visibility_notifies(rows):
                event['source'] = path.relative_to(cooked).with_suffix('').as_posix()
                event['label'] = visibility_label(sources['model'], event['bone'])
                bone_visibility.setdefault(event['bone'], []).append(event)
    socket_path = cooked / "Client/Content" / (skeleton + ".json")
    if sources.get("component_blueprint") and not socket_path.is_file():
        skeleton_root = root / "referenced-skeleton"
        skeleton_root.mkdir()
        _, skeleton_converted, skeleton_cooked, skeleton_receipt = await extract_model(
            skeleton_root, sources, fmodel, converter, False, additional_filters=[skeleton], **options
        )
        shutil.copytree(skeleton_converted, converted, dirs_exist_ok=True)
        shutil.copytree(skeleton_cooked, cooked, dirs_exist_ok=True)
        manifest["files"].extend(skeleton_receipt["files"])
        if not socket_path.is_file():
            raise ValueError("The exact authored NPC skeleton metadata was not exported")
    attachments = []
    linked_components = []
    model_scene = {}
    material_overrides = {}
    if sources.get("component_blueprint") and not sources.get("component"):
        blueprint_path = cooked / "Client/Content" / (sources["component_blueprint"] + ".json")
        if not blueprint_path.is_file():
            raise ValueError("The authored NPC component blueprint was not exported")
        blueprint_rows = json.loads(blueprint_path.read_text(encoding="utf-8-sig"))
        inherited = [json.loads((metadata_cooked / 'Client/Content' / (package + '.json')).read_text(encoding='utf-8-sig'))
                     for package, metadata_cooked, _ in reversed(blueprint_exports)]
        for rows in [*inherited, blueprint_rows]:
            material_overrides.update(npc_material_overrides(rows))
        linked_components = await discover_scene_components(
            root, sources, cooked, converted, manifest, skeleton=skeleton,
            local_client=local_client, scene=model_scene)
        for recipe in linked_components:
            _, relation = prepare_linked_component({"id": "default", **sources}, recipe)
            recipe["component"] = relation
            # Keep proven fixed socket attachments baked into the legacy body GLB;
            # the linked copy is for standalone inspection, never drawn twice.
            if relation["baked"]:
                attachments.append({"name": relation["name"], "model": recipe["model"],
                    "parent": relation["parent_component"], "socket": relation["socket"],
                    "visible": relation["default_visible"], **relation["transform"]})
        if material_overrides:
            material_root = root / "npc-materials"
            material_root.mkdir()
            _, part_converted, part_cooked, receipt = await extract_model(
                material_root, sources, fmodel, converter, False,
                additional_filters=sorted({path.rsplit("/", 1)[0] + "/" for path in material_overrides.values()}),
                **options
            )
            shutil.copytree(part_converted, converted, dirs_exist_ok=True)
            shutil.copytree(part_cooked, cooked, dirs_exist_ok=True)
            manifest["files"].extend(receipt["files"])
            if any(not (cooked / "Client/Content" / (path + ".json")).is_file()
                   for path in material_overrides.values()):
                raise ValueError("The authored NPC material override was not exported")
        socket_rows = json.loads(socket_path.read_text(encoding="utf-8-sig")) if socket_path.is_file() else []
        sockets = {row.get("Properties", {}).get("SocketName"): row.get("Properties", {})
                   for row in socket_rows if row.get("Type") == "SkeletalMeshSocket"}
        for index, attachment in enumerate(attachments):
            if attachment["parent"] != "CharacterMesh0":
                raise ValueError("NPC accessory has an unsupported attachment parent")
            socket = sockets.get(attachment["socket"])
            if socket:
                attachment["bone"] = socket.get("BoneName")
                attachment["socket_location"] = socket.get("RelativeLocation")
                attachment["socket_rotation"] = socket.get("RelativeRotation")
                attachment["socket_scale"] = socket.get("RelativeScale")
            else:
                # Unreal permits attaching directly to a named bone.
                attachment["bone"] = attachment["socket"]
            subroot = root / f"attachment-{index}"
            subroot.mkdir()
            accessory, converted_part, cooked_part, receipt = await extract_model(
                subroot, {"model": attachment["model"], "blueprints": []}, fmodel, converter,
                False, **options
            )
            shutil.copytree(converted_part, converted, dirs_exist_ok=True)
            shutil.copytree(cooked_part, cooked, dirs_exist_ok=True)
            manifest["files"].extend(receipt["files"])
            attachment["file"] = str(converted / accessory.relative_to(converted_part))
    extra_models = [
        component_model(component, converted, cooked, skeleton,
                        master_pose=sources.get('master_pose', False))
        for component in sources.get("extra_models", [])
    ]
    from wuwa_story_worker.model_material_dependencies import missing_material_resources

    material_models = [sources["model"], *sources.get("extra_models", []),
                       *(item["model"] for item in attachments)]
    override_packages = [*material_overrides.values(),
                         *(path for slots in sources.get("part_material_overrides", {}).values()
                           for path in slots.values())]
    material_fetched = set()
    for depth in range(8):
        missing = missing_material_resources(cooked, converted, material_models, override_packages)
        if not missing:
            break
        if set(missing) <= material_fetched:
            raise ValueError("Explicit model material resources were not exported: " + ", ".join(missing))
        material_fetched.update(missing)
        subroot = root / f"material-dependencies-{depth}"
        subroot.mkdir()
        _, extra_converted, extra_cooked, receipt = await extract_model(
            subroot, sources, fmodel, converter, False, additional_filters=missing, **options)
        shutil.copytree(extra_converted, converted, dirs_exist_ok=True)
        shutil.copytree(extra_cooked, cooked, dirs_exist_ok=True)
        manifest["files"].extend(receipt["files"])
    else:
        raise ValueError("Model material inheritance exceeds eight dependency passes")
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
    if attachments:
        attachment_path = root / "attachments.json"
        attachment_path.write_text(json.dumps(attachments), encoding="utf-8")
        command.extend(["--attachments", attachment_path.resolve()])
    if material_overrides:
        material_path = root / "npc-material-overrides.json"
        material_path.write_text(json.dumps(material_overrides), encoding="utf-8")
        command.extend(["--material-overrides", material_path.resolve()])
    if sources.get('master_pose'):
        command.append('--master-pose')
    if sources.get('hide_primary_geometry'):
        command.append('--hide-primary-geometry')
    if sources.get('part_material_overrides'):
        material_path = root / 'npc-part-materials.json'
        material_path.write_text(json.dumps(sources['part_material_overrides']), encoding='utf-8')
        command.extend(['--part-material-overrides', material_path.resolve()])
    if bone_visibility:
        visibility_path = root / 'npc-bone-visibility.json'
        visibility_path.write_text(json.dumps(bone_visibility), encoding='utf-8')
        command.extend(['--bone-visibility', visibility_path.resolve()])
    plan_path = output / "animation-plan.json"
    # A full Blender scene can exceed the memory budget of small production workers.
    # Enable it on a worker explicitly provisioned for the model's animation set.
    export_blender = os.getenv("WUWA_MODEL_BLENDER_EXPORT", "").lower() in {"1", "true"}
    if component_animations:
        bindings_path = root / "component-animation-bindings.json"
        bindings_path.write_text(json.dumps(component_animations), encoding="utf-8")
        command.extend(["--component-animation-bindings", bindings_path.resolve()])
    if sources.get("component"):
        command.append("--allow-empty-component-animations")
    if animation_cache is not None:
        command.append("--shared-animation-rig")
    if sources.get("animation_ownership"):
        ownership_path = root / "animation-ownership.json"
        ownership_path.write_text(json.dumps(sources["animation_ownership"]), encoding="utf-8")
        command.extend(["--animation-ownership", ownership_path.resolve()])
    await progress("rig_and_animation_plan" if include_animations else "rig")
    await run_blender(
        [*command, "--rig-only", *(["--blender-source"] if export_blender else []),
         *(["--animation-plan", plan_path] if include_animations else [])],
        root,
        900,
        run_id=run_id,
    )
    batch_count = 0
    pack_descriptors, pack_summaries = [], {}
    if include_animations:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if sources.get("component") and not plan["clips"]:
            include_animations = False
    if include_animations:
        if animation_cache is not None:
            recipe = fingerprint([sha256(converter), sha256(blender),
                                  sha256(Path(__file__).with_name("model_bundle.py")),
                                  sha256(Path(__file__).with_name("model_animation_merge.py"))])
            rig_key, reused, new_identities = prepare_packs(plan, animation_cache, recipe)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            (output / "facial.json").write_text(json.dumps(plan["facial"]), encoding="utf-8")
            for selected in reused:
                entry = selected["entry"]
                descriptor = install_pack(output, entry, selected["clips"])
                pack_descriptors.append(descriptor)
                pack_summaries[descriptor["key"]] = entry["summary"]
            if reused:
                await progress("reuse_animation_packs", packs=len(reused),
                               reused_clips=sum(len(item["clips"]) for item in reused))
        clips = plan["clips"]
        if not clips and animation_cache is None:
            raise ValueError("No playable clips in the verified animation plan")
        batch_size = 20
        batch_count = (len(clips) + batch_size - 1) // batch_size
        parts = []
        async def export_batch(index):
            await progress(
                "animation_batch",
                batch=index + 1,
                batches=batch_count,
                clips=len(clips),
                batch_size=batch_size,
            )
            started = time.monotonic()
            log_root = root
            if animation_workers > 1:
                log_root = root / f"animation-log-{index:04d}"
                log_root.mkdir()
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
                log_root,
                600,
                run_id=run_id,
            )
            part = output / "animation-parts" / f"part-{index:04d}.glb"
            stats = glb_summary(part, allow_animations=True)
            expected = min(batch_size, len(clips) - index * batch_size)
            if stats["animations"] != expected:
                raise ValueError("Animation batch lost clips")
            await progress(
                "animation_batch_completed",
                batch=index + 1,
                batches=batch_count,
                seconds=round(time.monotonic() - started, 2),
                clips=len(clips),
            )
            return part

        if animation_workers == 1:
            for index in range(batch_count):
                parts.append(await export_batch(index))
        else:
            semaphore = asyncio.Semaphore(animation_workers)

            async def bounded(index):
                async with semaphore:
                    return await export_batch(index)

            results = await finish_writes(asyncio.gather(
                *(bounded(index) for index in range(batch_count)), return_exceptions=True))
            for result in results:
                if isinstance(result, BaseException):
                    raise result
                parts.append(result)
        if parts:
            await progress("merge_animation_batches", batches=batch_count)
            await asyncio.to_thread(merge_animation_glbs, parts, output / "animations.glb",
                                    source_paths={clip["name"]: clip["source"] for clip in clips})
            summary = glb_summary(output / "animations.glb", allow_animations=True)
            if summary["animations"] != len(clips):
                raise ValueError("Merged animation collection lost clips")
            if animation_cache is None:
                (output / "animations.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            else:
                pack_id = summary["sha256"][:16]
                target = output / "animation-packs" / (pack_id + ".glb")
                target.parent.mkdir(exist_ok=True)
                (output / "animations.glb").replace(target)
                entry = {"id": pack_id, "path": target, "summary": summary,
                         "identities": new_identities, "rig_signature": plan.get("rig_signature"),
                         "rig_namespace": fingerprint([plan["skeleton"], recipe])}
                animation_cache[rig_key].append(entry)
                descriptor = install_pack(output, entry, [clip["name"] for clip in clips])
                pack_descriptors.append(descriptor)
                pack_summaries[descriptor["key"]] = summary
        if animation_cache is not None:
            if not pack_descriptors:
                raise ValueError("No verified animation packs for this appearance")
            write_manifest(output, pack_descriptors)
    if export_blender:
        await progress("assemble_blender_project")
        source_blend = output / "blender-source.blend"
        target_blend = output / "character.blend"
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        armature = report.get("blender_armature")
        if not source_blend.is_file() or not isinstance(armature, str) or not armature:
            raise ValueError("Editable Blender source or its armature is missing")
        if include_animations:
            packs = ([output / model_file_name(pack["key"]) for pack in pack_descriptors]
                     if pack_descriptors else [output / "animations.glb"])
            await run_blender([
                blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                Path(__file__).with_name("blender_ueformat_actions.py").resolve(), "--",
                "--armature", armature,
                *(["--manifest", (output / "animation-manifest.json").resolve()]
                  if pack_descriptors else []),
                source_blend.resolve(), target_blend.resolve(), *(path.resolve() for path in packs),
            ], root, 3600, run_id=run_id)
        else:
            shutil.copyfile(source_blend, target_blend)
        if not target_blend.is_file() or target_blend.stat().st_size == 0:
            raise ValueError("Blender project assembly produced no file")
        facial_path = output / "facial.json"
        if include_animations and facial_path.is_file() and json.loads(
            facial_path.read_text(encoding="utf-8")
        ).get("clips"):
            await progress("assemble_blender_facial")
            facial_blend = output / "character-facial.blend"
            await run_blender([
                blender, "-b", target_blend.resolve(), "--python-exit-code", "1", "--python",
                Path(__file__).with_name("blender_facial_attach.py").resolve(), "--",
                facial_path.resolve(), facial_blend.resolve(), "--strict",
            ], root, 3600, run_id=run_id)
            if not facial_blend.is_file() or facial_blend.stat().st_size == 0:
                raise ValueError("Blender facial assembly produced no file")
            facial_blend.replace(target_blend)
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
    if include_animations and animation_cache is None:
        summaries["animations"] = glb_summary(output / "animations.glb", allow_animations=True)
    summaries.update(pack_summaries)
    if summaries["preview"]["bytes"] > summaries["download"]["bytes"]:
        raise ValueError("Preview unexpectedly exceeds the full-resolution model")
    provenance = {
        "source_files": manifest["files"],
        "model": sources["model"],
        "extra_models": sources.get("extra_models", []),
        **({"linked_components": linked_components} if linked_components else {}),
        **({"scene": model_scene} if model_scene.get("cues") else {}),
        **({'bone_visibility_events': bone_visibility} if bone_visibility else {}),
        **({'data_asset': sources['data_asset']} if sources.get('data_asset') else {}),
        **({'blueprint_model': sources['blueprint_model']} if sources.get('blueprint_model') else {}),
        "blueprints": sources["blueprints"],
        "tools": {
            name: sha256(path)
            for name, path in [("fmodel", fmodel), ("converter", converter), ("blender", blender)]
        },
        "recipe": sha256(Path(__file__).with_name("model_bundle.py")),
        "preview_texture_size": 512,
        "include_animations": include_animations,
        "animation_batches": batch_count,
        "blender_project": export_blender,
    }
    if pack_descriptors:
        provenance["animation_packs"] = pack_descriptors
        provenance["animation_coverage"] = plan["animation_coverage"]
    if include_animations and animation_cache is None:
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


def publication_files(job, output, summaries, *, packs=None):
    """Validate every form before uploads or a database publication can begin."""
    packs = validate_animation_packs(packs or [])
    names = set()
    if packs:
        if not job.include_animations:
            raise ValueError("Unexpected animation packs for a rig-only job")
        manifest = json.loads((output / "animation-manifest.json").read_text(encoding="utf-8"))
        if manifest != {"format": "solaris-animation-packs-v1", "packs": packs}:
            raise ValueError("Animation manifest changed before publication")
        for pack in packs:
            actual = glb_summary(output / model_file_name(pack["key"]), allow_animations=True)
            if summaries.get(pack["key"]) != actual:
                raise ValueError("Animation pack summary changed before publication")
            if not set(pack["clips"]) <= {clip["name"] for clip in actual["clips"]}:
                raise ValueError("Animation pack selects unpublished clips")
            names.update(pack["clips"])
        animation_count = len(names)
    elif job.include_animations:
        actual = glb_summary(output / "animations.glb", allow_animations=True)
        if summaries.get("animations") != actual:
            raise ValueError("Animation summary changed before publication")
        animation_count = actual["animations"]
        names = {clip["name"] for clip in actual["clips"]}
    elif "animations" in summaries or (output / "animations.glb").exists():
        raise ValueError("Unexpected animations for a rig-only job")
    else:
        animation_count = 0
    files = [
        ("preview", output / "preview.glb", "unknown", "model/gltf-binary"),
        ("download", output / "character.glb", "unknown", "model/gltf-binary"),
    ]
    project = output / "character.blend"
    if project.is_file():
        with project.open("rb") as stream:
            header = stream.read(12)
        if len(header) < 12 or not (header.startswith(b"BLENDER") or header.startswith(b"\x28\xb5\x2f\xfd")):
            raise ValueError("Invalid Blender project header")
        files.append(("blender", project, "unknown", "application/x-blender"))
    if job.include_animations:
        if packs:
            files.extend((pack["key"], output / model_file_name(pack["key"]), "unknown", "model/gltf-binary")
                         for pack in packs)
            files.append(("animation_manifest", output / "animation-manifest.json", "raw_json", "application/json"))
        else:
            files.append(("animations", output / "animations.glb", "unknown", "model/gltf-binary"))
        facial_path = output / "facial.json"
        if facial_path.is_file():
            facial = validate_facial_pack(json.loads(facial_path.read_text(encoding="utf-8")))
            if any(clip.get("body_clip") is not None and clip["body_clip"] not in names for clip in facial["clips"]):
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


async def publish_forms(job, bundles, *, publication_payload=None):
    if not bundles or len({form["id"] for form, *_ in bundles}) != len(bundles):
        raise ValueError("Model publication requires distinct authored forms")
    validated = [publication_files(
        job.model_copy(update={"include_animations": "animations" in summaries or bool(provenance.get("animation_packs"))})
        if form.get("component") else job, output, summaries, packs=provenance.get("animation_packs"))
        for form, output, summaries, provenance in bundles]
    storage = S3Storage(get_settings())
    service = FileRegistrationService(storage)
    prepared_forms = []
    prepared_cache = {}
    for files, _ in validated:
        prepared = []
        for kind, path, _, mime in files:
            if not kind.startswith("animation_pack_"):
                prepared.append(await service.prepare_file(path, mime))
            else:
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if identity not in prepared_cache:
                    prepared_cache[identity] = await service.prepare_file(path, mime)
                prepared.append(prepared_cache[identity])
        for (kind, _, _, _), ready in zip(files, prepared, strict=True):
            if kind in {"download", "animations"} or kind.startswith("animation_pack_"):
                await ensure_glb_download(
                    storage, content_object_key(ready.digest), ready.digest.hex()
                )
        prepared_forms.append(prepared)
    async with SessionFactory() as session:
        run = await session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == job.run_id).with_for_update()
        )
        identity_matches = run is not None and (
            run.metadata_json.get("payload") == publication_payload if publication_payload is not None
            else CharacterModelJob.model_validate(run.metadata_json.get("payload")) == job
        )
        if not identity_matches:
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
                    reference_type=("character_model_animation_pack" if kind.startswith("animation_pack_")
                                    else "character_model_" + kind),
                    owner_node_id=job.owner_node_id,
                    release_id=job.release_id,
                    source_path="/Game/" + provenance["model"] + (
                        "#component=" + form["id"] if form.get("component") else "") + (
                        "#form=" + form["id"] + "&animation-pack=" + kind.removeprefix("animation_pack_")
                        if kind.startswith("animation_pack_") else ""),
                )
                published[kind] = file.id
                reference.metadata_json = {
                    "model_bundle_id": str(job.run_id),
                    "form_id": form["id"],
                    "form_label": form["label"],
                    "form_order": form["order"],
                    "asset_version": job.asset_version,
                    "download_id": job.download_id,
                    "tier": job.tier,
                    "game_source": provenance,
                    "animation_count": animation_count,
                    **({"component": form["component"]} if form.get("component") else {}),
                    **({"scene": provenance["scene"]} if provenance.get("scene") else {}),
                    **({"animation_pack": next(pack for pack in provenance["animation_packs"] if pack["key"] == kind)}
                       if kind.startswith("animation_pack_") else {}),
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


async def build_scene_bundles(staging, forms, *, include_animations=True, **options):
    """Retain independently validated rigs until the complete scene can publish atomically."""
    bundles, total_components = [], 0
    options = {**options, "animation_cache": {}, "asset_cache": {}}
    for form in forms:
        form_options = {**options}
        if form.get("model"):
            form_options["animation_ownership"] = ownership_for(form, forms)
        root = staging / form["id"]
        root.mkdir()
        if options.get("local_client") is None:
            prepare_entity_workspace(staging, root)
        output, summaries, provenance = await build_bundle(
            root, form, include_animations=include_animations, **form_options)
        provenance["form_source"] = form.get("source")
        components = provenance.pop("linked_components", [])
        total_components += len(components)
        if total_components > MAX_MODEL_COMPONENTS:
            raise ValueError(f"Model scene exceeds {MAX_MODEL_COMPONENTS} authored components")
        linked = []
        children = []
        prepared_components = prepare_linked_components(form, components)
        for recipe, (component_id, relation) in zip(components, prepared_components, strict=True):
            if component_id in linked:
                raise ValueError("Duplicate authored component in model scene")
            linked.append(component_id)
            child_form = {**recipe, "id": component_id, "label": relation["label"],
                          "order": len(children), "component": relation}
            child_root = staging / component_id
            child_root.mkdir()
            if options.get("local_client") is None:
                prepare_entity_workspace(staging, child_root)
            # Static props have no synthetic animation pack. Independent child rigs
            # export only their own authored animation blueprint/sequence resources.
            animated = include_animations and bool(recipe.get("blueprints") or recipe.get("montages"))
            child_output, child_summaries, child_provenance = await build_bundle(
                child_root, child_form, include_animations=animated, **options)
            child_provenance["form_source"] = relation["source"]
            children.append((child_form, child_output, child_summaries, child_provenance))
        if linked:
            scene = provenance.setdefault("scene", {"format": "solaris-model-scene-v1", "cues": []})
            scene["components"] = linked
        bundles.append((form, output, summaries, provenance))
        bundles.extend(children)
    return bundles


async def process_character_model(payload):
    if payload.get("job_type") == "media.character_model_upload":
        from wuwa_story_worker.model_uploads import process_model_upload
        return await process_model_upload(payload)
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
            forms = await job_forms(session, job)
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
                bundles = await build_scene_bundles(
                    staging, forms, include_animations=job.include_animations, run_id=job.run_id)
                await model_progress(job.run_id, "publish", forms=len(bundles))
                await publish_forms(job, bundles)

        # Never delete scratch while an owned subprocess/thread is still writing.
        await finish_writes(execute())
