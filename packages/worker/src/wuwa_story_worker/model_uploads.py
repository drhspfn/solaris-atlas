"""Publish a verified local build using the same atomic model publication path."""

import asyncio
import json
import logging
import os
import shutil
import struct
from pathlib import Path

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.character_models import CharacterModelJob
from wuwa_story.ingestion.client_assets import inspect_installed_clients
from wuwa_story.ingestion.model_archives import (
    CHUNK_BYTES,
    FILES,
    digest,
    model_file_name,
    read_archive,
    validate_manifest,
)
from wuwa_story.ingestion.model_limits import MAX_MODEL_COMPONENTS
from wuwa_story.ingestion.model_uploads import ModelUploadJob, chunk_count, chunk_key
from wuwa_story.storage.s3 import S3Storage

from wuwa_story_worker.broker import job_lock
from wuwa_story_worker.client_assets import validate_plan
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.linked_component_forms import prepare_linked_components
from wuwa_story_worker.media_workspace import finish_writes
from wuwa_story_worker.model_animations import animation_summary
from wuwa_story_worker.model_facial import validate_facial_pack
from wuwa_story_worker.model_sources import authored_package, glb_summary
from wuwa_story_worker.tooling import asset_workspace, tool_path
from wuwa_story_worker.transient_workspace import transient_workspace

logger = logging.getLogger(__name__)


def _component_summary(path):
    """Validate a static component GLB, or use the usual skinned-model validator."""
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("Invalid GLB output")
        magic, version, size, length, kind = struct.unpack("<4sIIII", header)
        if (magic != b"glTF" or version != 2 or size != path.stat().st_size
                or kind != 0x4E4F534A or length > size - 20):
            raise ValueError("Invalid GLB output")
        data = json.loads(stream.read(length))
    if data.get("skins"):
        return glb_summary(path)
    if (not data.get("meshes") or data.get("animations")
            or any("uri" in item for item in data.get("images", []))
            or any("uri" in item for item in data.get("buffers", []))):
        raise ValueError("Invalid static component model")
    return {"bytes": size, "bones": 0, "textures": len(data.get("images", [])),
            "sha256": digest(path)}


def decode_bundles(archive, root, job, expected_forms, *, expected_components=None, _manifest=None):
    manifest = read_archive(archive, root) if _manifest is None else validate_manifest(_manifest)
    if (manifest.get("character_key"), manifest.get("game_version")) != (job.character_key, job.game_version):
        raise ValueError(
            "Archive belongs to another character or snapshot; download its recipe again")
    roots = [entry for entry in manifest["forms"] if not entry["form"]["id"].startswith("component-")]
    forms = [entry["form"] for entry in roots]
    if forms != expected_forms:
        raise ValueError(
            "Archive forms no longer match authored model sources; rebuild with a fresh recipe")
    included = ["animations" in entry["files"] or bool(entry["provenance"].get("animation_packs"))
                for entry in roots]
    if any(included) != all(included):
        raise ValueError(
            "Archive must include animations for every form, or for none")
    publication = CharacterModelJob(run_id=job.run_id, character_key=job.character_key,
                                    owner_node_id=job.owner_node_id, release_id=job.release_id, game_version=job.game_version,
                                    asset_version=manifest.get("asset_version"), tier=manifest.get("tier"),
                                    download_id=digest(archive), include_animations=all(included))
    children = [entry for entry in manifest["forms"] if entry["form"].get("component")]
    if children:
        # The archive is untrusted; its claimed Blueprint and component name prove nothing.
        if not isinstance(expected_components, dict) or set(expected_components) != {
            entry["form"]["id"] for entry in children
        }:
            raise ValueError("Archive components require trusted authored component identities")
        for entry in children:
            expected = expected_components[entry["form"]["id"]]
            if (not isinstance(expected, dict)
                    or expected.get("component") != entry["form"]["component"]
                    or expected.get("model") != entry["provenance"].get("model")):
                raise ValueError("Archive component does not match its trusted authored identity")
    entries = {entry["form"]["id"]: entry for entry in manifest["forms"]}
    for entry in manifest["forms"]:
        component = entry["form"].get("component")
        if component is None:
            continue
        parent = entries[component["parent_form_id"]]
        while parent["form"].get("component") is not None:
            parent = entries[parent["form"]["component"]["parent_form_id"]]
        parent_form = parent["form"]
        blueprint = (parent_form.get("component_blueprint")
                     or (parent_form.get("model") if not parent_form.get("component")
                         and not parent_form.get("data_asset") and not parent_form.get("blueprint_model")
                         else parent["provenance"].get("model")))
        source = component["source"]["blueprint"]
        if not isinstance(blueprint, str) or authored_package(
            source if source.startswith("/Game/") else "/Game/" + source
        ) != authored_package(blueprint if blueprint.startswith("/Game/") else "/Game/" + blueprint):
            raise ValueError("Archive component source does not match its parent Blueprint")
    bundles = []
    for entry in manifest["forms"]:
        form = entry["form"]
        output = root / form["id"]
        output.mkdir()
        for kind, key in entry["files"].items():
            source = root / "objects" / key
            destination = output / model_file_name(kind)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, destination)
            except OSError:
                shutil.copyfile(source, destination)
        summaries = {
            kind: (_component_summary(output / FILES[kind])
                   if form.get("component") and kind != "animations"
                   else glb_summary(output / FILES[kind], allow_animations=kind == "animations"))
            for kind in ("preview", "download", *(["animations"] if "animations" in entry["files"] else []))
        }
        if summaries["preview"]["bones"] != summaries["download"]["bones"]:
            raise ValueError("Compact and full model rigs disagree")
        with (output / "character.glb").open("rb") as stream:
            length = struct.unpack_from("<I", stream.read(20), 12)[0]
            full = json.loads(stream.read(length))
        bones = {full["nodes"][index]["name"]
                 for skin in full.get("skins", []) for index in skin["joints"]}
        if "animations" in entry["files"]:
            animation_summary(output / "animations.glb", bones)
        packs = entry["provenance"].get("animation_packs", [])
        if packs:
            manifest_data = json.loads((output / "animation-manifest.json").read_text(encoding="utf-8"))
            if manifest_data != {"format": "solaris-animation-packs-v1", "packs": packs}:
                raise ValueError("Animation manifest does not match pack descriptors")
        for pack in packs:
            path = output / model_file_name(pack["key"])
            summary = animation_summary(path)
            available = {clip["name"] for clip in summary["clips"]}
            selected = set(pack["clips"])
            if not selected <= available:
                raise ValueError("Animation pack references missing clips")
            with path.open("rb") as stream:
                length = struct.unpack_from("<I", stream.read(20), 12)[0]
                data = json.loads(stream.read(length))
            for clip in data["animations"]:
                if clip["name"] in selected and any(
                    data["nodes"][channel["target"]["node"]]["name"] not in bones
                    for channel in clip["channels"]
                ):
                    raise ValueError("Selected animation targets a non-rig bone")
            summaries[pack["key"]] = summary
        if "facial" in entry["files"]:
            pack = validate_facial_pack(json.loads(
                (output / "facial.json").read_text()))
            targets = {name.casefold() for mesh in full["meshes"] for name in mesh.get(
                "extras", {}).get("targetNames", [])}
            if any(track["target"].casefold() not in targets for clip in pack["clips"] for track in clip["tracks"]):
                raise ValueError(
                    "Facial curves do not match the uploaded model")
        for field in ('data_asset', 'blueprint_model'):
            if form.get(field) and entry['provenance'].get(field) != form[field]:
                raise ValueError('Archive NPC setup does not match its authored data asset or Blueprint')
        model = form.get("model")
        if form.get("component"):
            model = entry["provenance"].get("model", model)
            if not isinstance(model, str):
                raise ValueError("Archive component has no model source")
            authored_package(model if model.startswith("/Game/") else "/Game/" + model)
        if form.get('data_asset') or form.get('blueprint_model'):
            model = entry['provenance'].get('model')
            if not isinstance(model, str):
                raise ValueError('Archive NPC setup has no resolved master mesh')
            authored_package('/Game/' + model)
        provenance = {**entry["provenance"], "model": model,
                      "form_source": form.get("source", form.get("component", {}).get("source")), "local_archive_sha256": publication.download_id}
        bundles.append((form, output, summaries, provenance))
    return publication, bundles


async def discover_upload_components(root, forms, asset_version, tier, *, include_animations=True):
    """Independently derive component identities from a matching installed client."""
    clients = [client for client in await asyncio.to_thread(inspect_installed_clients, asset_workspace())
               if client.get("version") == asset_version and client.get("tier") == tier
               and client.get("state") == "downloaded"]
    if len(clients) != 1:
        raise ValueError("Linked model upload requires exactly one matching ready installed client")
    client = clients[0]
    client_path = Path(client["path"])
    plan = json.loads((client_path / "plan.json").read_text(encoding="utf-8"))
    status = json.loads((client_path / "status.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    if (plan.get("id") != client.get("download_id") or plan.get("version") != asset_version
            or plan.get("tier") != tier or status.get("state") != "downloaded"
            or status.get("id") != plan.get("id")):
        raise ValueError("Linked model client identity changed")
    local_client = {"game": client_path / "game", "keys": client_path / "keys.txt", "processors": 1}
    result = {}
    for form in forms:
        if not form.get("component_blueprint"):
            continue
        recipes = await _discover_upload_form(root, form, local_client, include_animations)
        for recipe, (identity, relation) in zip(
            recipes, prepare_linked_components(form, recipes), strict=True
        ):
            if identity in result or len(result) >= MAX_MODEL_COMPONENTS:
                raise ValueError("Duplicate or excessive authored components")
            result[identity] = {"component": relation, "model": recipe["model"]}
    return result


async def _discover_upload_form(root, form, local_client, include_animations):
    from wuwa_story_worker.character_models import discover_scene_components, extract_model

    workspace = root / ("discovery-" + form["id"])
    workspace.mkdir()
    sources = dict(form)
    if sources.get("blueprint_model"):
        from wuwa_story_worker.npc_setup import blueprint_sources

        package, visited = sources["blueprint_model"], set()
        for depth in range(8):
            if package in visited:
                raise ValueError("Cyclic NPC Blueprint model inheritance")
            visited.add(package)
            setup_root = workspace / f"blueprint-{depth}"
            setup_root.mkdir()
            _, _, setup_cooked, _ = await extract_model(
                setup_root, {"model": package, "blueprints": []},
                tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
                False, metadata_only=True, local_client=local_client)
            path = setup_cooked / "Client/Content" / (package + ".json")
            resolved = blueprint_sources(json.loads(path.read_text(encoding="utf-8-sig")), package)
            if "model" in resolved:
                sources = {**sources, **resolved,
                           "blueprints": sources["blueprints"] or resolved.get("blueprints", [])}
                break
            package = resolved["parent"]
        else:
            raise ValueError("NPC Blueprint model inheritance exceeds eight authored parents")
    if sources.get("data_asset"):
        from wuwa_story_worker.npc_setup import setup_sources

        setup_root = workspace / "setup"
        setup_root.mkdir()
        _, _, setup_cooked, _ = await extract_model(
            setup_root, sources, tool_path("WUWA_FMODEL_PATH"),
            tool_path("WUWA_TEXTURE_CONVERTER_PATH"), False,
            metadata_only=True, local_client=local_client)
        path = setup_cooked / "Client/Content" / (sources["data_asset"] + ".json")
        sources = {**sources, **setup_sources(json.loads(path.read_text(encoding="utf-8-sig")),
                                               sources["data_asset"])}
    model = sources["model"]
    character_dir = ((model.rsplit("/Model/", 1)[0] if "/Model/" in model else model.rsplit("/", 1)[0])
                     if model.startswith("Aki/Character/NPC/") else "/".join(model.split("/")[:5]))
    filters = [sources["component_blueprint"], *sources.get("blueprints", [])]
    if include_animations:
        filters.extend([*sources.get("montages", []), character_dir + "/",
                        "/".join(model.split("/")[:4]) + "/BaseAnim",
                        "Engine/Content/Animation/DefaultAnim", "ACLAnim", "ACLPlugin.uplugin",
                        character_dir.rsplit("/", 1)[-1]])
    else:
        filters.append(model.rsplit("/", 1)[0] + "/")
    _, _, cooked, _ = await extract_model(
        workspace, sources, tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
        False, local_client=local_client, metadata_only=True, additional_filters=filters)
    from wuwa_story_worker.model_sources import npc_animation_references
    from wuwa_story_worker.npc_accessories import visibility_sources

    refs = set(visibility_sources(model))
    for blueprint in [*sources["blueprints"], *sources.get("montages", [])]:
        path = cooked / "Client/Content" / (blueprint + ".json")
        if path.is_file():
            refs.update(npc_animation_references(json.loads(path.read_text(encoding="utf-8-sig"))))
    missing = [ref for ref in refs if not (cooked / "Client/Content" / (ref + ".json")).is_file()]
    if missing:
        extra_root = workspace / "referenced-animations"
        extra_root.mkdir()
        _, _, extra_cooked, _ = await extract_model(
            extra_root, sources, tool_path("WUWA_FMODEL_PATH"),
            tool_path("WUWA_TEXTURE_CONVERTER_PATH"), False, metadata_only=True,
            additional_filters=sorted(missing), local_client=local_client)
        shutil.copytree(extra_cooked, cooked, dirs_exist_ok=True)
        if any(not (cooked / "Client/Content" / (ref + ".json")).is_file() for ref in missing):
            raise ValueError("An explicitly referenced NPC animation was not exported")
    return await discover_scene_components(workspace, sources, cooked, local_client=local_client)


async def process_model_upload(payload):
    from wuwa_story_worker.character_models import job_forms, publish_forms

    job = ModelUploadJob.model_validate(payload)
    storage = S3Storage(get_settings())
    async with engine.connect() as connection, job_lock(connection, 707, job.owner_node_id % 2147483648):
        async with SessionFactory() as session:
            run = await session.get(ProcessingRun, job.run_id)
            if run is None or run.metadata_json.get("payload") != job.model_dump():
                raise ValueError("Model upload identity changed")
            if run.status in {"completed", "cancelled"}:
                return
            forms = await job_forms(session, job)

        async def execute():
            await update_admin_run(job.run_id, "character_model", "running",
                                   raw_output={"stage": "validate_archive"})
            with transient_workspace(asset_workspace().parent / "transient-jobs") as root:
                archive = root / "model.zip"
                with archive.open("xb") as target:
                    for index in range(chunk_count(job)):
                        received = 0
                        expected = min(
                            CHUNK_BYTES, job.archive_bytes - index * CHUNK_BYTES)
                        async for block in storage.get(chunk_key(job, index)):
                            received += len(block)
                            if received > expected:
                                raise ValueError(
                                    "Stored upload chunk exceeds declared size")
                            target.write(block)
                        if received != expected:
                            raise ValueError(
                                "Stored model upload chunk is incomplete")
                manifest = await asyncio.to_thread(read_archive, archive, root)
                if ((manifest.get("character_key"), manifest.get("game_version"))
                        != (job.character_key, job.game_version)
                        or [entry["form"] for entry in manifest["forms"]
                            if not entry["form"]["id"].startswith("component-")] != forms):
                    raise ValueError("Archive identity or authored model forms do not match")
                expected_components = None
                if any(entry["form"].get("component") for entry in manifest["forms"]):
                    # Validate the declared client version before using it to find local assets.
                    CharacterModelJob(run_id=job.run_id, character_key=job.character_key,
                        owner_node_id=job.owner_node_id, release_id=job.release_id,
                        game_version=job.game_version, asset_version=manifest.get("asset_version"),
                        tier=manifest.get("tier"), download_id=await asyncio.to_thread(digest, archive))
                    expected_components = await discover_upload_components(
                        root, forms, manifest["asset_version"], manifest["tier"],
                        include_animations=any(
                            "animations" in entry["files"] or bool(entry["provenance"].get("animation_packs"))
                            for entry in manifest["forms"]
                            if not entry["form"]["id"].startswith("component-")))
                publication, bundles = await asyncio.to_thread(
                    decode_bundles, archive, root, job, forms,
                    expected_components=expected_components, _manifest=manifest)
                await update_admin_run(job.run_id, "character_model", "running", raw_output={"stage": "publish"})
                await publish_forms(publication, bundles, publication_payload=job.model_dump())
            for index in range(chunk_count(job)):
                try:
                    await storage.delete(chunk_key(job, index))
                except Exception:
                    logger.exception(
                        "Published model upload cleanup failed run=%s part=%s", job.run_id, index)

        await finish_writes(execute())
