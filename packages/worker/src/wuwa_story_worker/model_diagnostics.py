"""Preflight and workspace diagnostics for Solaris Atlas character models."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from wuwa_story_worker import character_models
from wuwa_story_worker.model_bundle import animation_sources
from wuwa_story_worker.model_material_dependencies import missing_material_resources
from wuwa_story_worker.model_material_inventory import material_input_inventory
from wuwa_story_worker.model_montage import montage_references


def _read_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _skeleton_from_cooked(cooked: Path, model_package: str) -> str | None:
    rows = _read_json(cooked / "Client/Content" / (model_package + ".json"))
    if not isinstance(rows, list):
        return None
    target_name = model_package.rsplit("/", 1)[-1]
    for row in rows:
        if isinstance(row, dict) and row.get("Type") == "SkeletalMesh" and row.get("Name") == target_name:
            obj_path = row.get("Properties", {}).get(
                "Skeleton", {}).get("ObjectPath")
            if isinstance(obj_path, str) and obj_path:
                return character_models.exported_package(obj_path)
    return None


def _material_inventory_summary(cooked: Path, converted: Path, model_package: str,
                                report_data: dict | None) -> dict:
    """Summarize materials, inheritance, textures, masks, and shader inputs."""
    missing_deps = []
    if cooked.is_dir() and converted.is_dir():
        try:
            missing_deps = missing_material_resources(
                cooked, converted, [model_package])
        except (OSError, ValueError) as exc:
            missing_deps = [f"error:{exc}"]
    materials = []
    seen = set()
    for item in (report_data or {}).get("materials", []):
        name = item.get("name")
        if name:
            seen.add(name)
        materials.append(item)
    content_converted = converted / "Client/Content"
    content_cooked = cooked / "Client/Content"
    if content_converted.is_dir():
        for meta_path in sorted(content_converted.rglob("*.json")):
            rel = meta_path.relative_to(content_converted).with_suffix("")
            if meta_path.stem in seen or not meta_path.stem.startswith(("MI_", "M_")):
                continue
            converted_meta = _read_json(meta_path)
            if not isinstance(converted_meta, dict):
                continue
            cooked_meta = _read_json(
                content_cooked / (rel.as_posix() + ".json"))
            parent = None
            if isinstance(cooked_meta, list):
                for row in cooked_meta:
                    if isinstance(row, dict) and isinstance(row.get("Properties"), dict):
                        parent_obj = row["Properties"].get("Parent")
                        if isinstance(parent_obj, dict) and parent_obj.get("ObjectPath"):
                            parent = parent_obj["ObjectPath"]
                            break
            inv = material_input_inventory(converted_meta, converted)
            missing_tex = [k for k, v in inv["textures"].items()
                           if v.get("image") is None]
            materials.append({
                "name": meta_path.stem,
                "package": rel.as_posix(),
                "parent": parent,
                "switches": inv["switches"],
                "textures_total": len(inv["textures"]),
                "missing_texture_inputs": missing_tex,
            })
    return {
        "materials": materials,
        "missing_textures": (report_data or {}).get("missing_textures", []),
        "missing_material_dependencies": missing_deps,
    }


def _check_animation_metadata(cooked: Path, skeleton: str | None, form: dict) -> dict:
    """Check animation and montage links and skeleton compatibility from cooked JSON."""
    result: dict = {
        "animations_encoded": False,
        "total_sequences": 0,
        "matching_skeleton_sequences": 0,
        "mismatched_skeleton_sequences": [],
        "montages_checked": 0,
        "missing_montage_references": [],
        "binding_checks": [],
        "binding_errors": [],
    }
    if not skeleton or not cooked.is_dir():
        return result
    sources = animation_sources(cooked, "/Game/" + skeleton)
    result["total_sequences"] = len(sources)
    for clip_name, entry in sorted(sources.items()):
        if entry.get("matches_skeleton"):
            result["matching_skeleton_sequences"] += 1
        else:
            result["mismatched_skeleton_sequences"].append({
                "clip": clip_name,
                "skeleton": entry.get("skeleton"),
            })
    content = cooked / "Client/Content"
    for path in sorted(content.rglob("*.json")):
        rows = _read_json(path)
        if not isinstance(rows, list):
            continue
        if any(isinstance(row, dict) and row.get("Type") == "AnimMontage" for row in rows):
            result["montages_checked"] += 1
            for ref in montage_references(rows):
                pkg = character_models.exported_package(ref)
                if not (content / (pkg + ".json")).is_file():
                    result["missing_montage_references"].append({
                        "montage": path.relative_to(content).with_suffix("").as_posix(),
                        "missing_sequence": pkg,
                    })
    comp = form.get("component") or {}
    if comp.get("bindings"):
        try:
            checked = character_models.component_animation_skeletons(
                form, cooked, skeleton)
            for clip, skel in sorted(checked.items()):
                result["binding_checks"].append({
                    "clip": clip,
                    "skeleton": skel,
                    "matches_mesh_skeleton": skel.casefold() == skeleton.casefold(),
                })
        except ValueError as exc:
            result["binding_errors"].append(str(exc))
    return result


async def _diagnose_single_form(root: Path, form: dict, *, local_client=None,
                                report_progress=None, check_animations: bool = True,
                                asset_cache: dict | None = None,
                                asset_locks: dict | None = None) -> tuple[dict, list[dict]]:
    """Run non-destructive preflight build + inspection on one form or component."""
    root.mkdir(parents=True, exist_ok=True)
    comp = form.get("component") or {}
    entry: dict = {
        "id": form.get("id"),
        "label": form.get("label"),
        "kind": comp.get("kind", "root_form") if comp else "root_form",
        "model": form.get("model"),
        "attachment": {
            "parent_form_id": comp.get("parent_form_id"),
            "parent_component": comp.get("parent_component"),
            "socket": comp.get("socket"),
            "default_visible": comp.get("default_visible"),
            "baked": comp.get("baked"),
            "source": comp.get("source"),
            "transform": comp.get("transform"),
        } if comp else None,
        "explicit_dependencies": {
            "blueprints": list(form.get("blueprints") or []),
            "extra_models": list(form.get("extra_models") or []),
            "montages": list(form.get("montages") or []),
            "part_material_overrides": form.get("part_material_overrides") or {},
            "bindings": list(comp.get("bindings") or []),
        },
        "mesh_found": False,
        "skeleton": None,
        "skeleton_found": False,
        "probe_outputs": {},
        "materials": {},
        "linked_components": [],
        "animation_check": None,
        "errors": [],
        "warnings": [],
        "status": "ok",
    }
    linked: list[dict] = []
    options = {**({"local_client": local_client}
                  if local_client is not None else {}), "asset_cache": asset_cache, "asset_locks": asset_locks}
    try:
        output, summaries, metadata = await character_models.build_bundle(
            root, form, include_animations=False, report_progress=report_progress, **options
        )
        linked = list(metadata.get("linked_components") or [])
        entry["linked_components"] = [
            {
                "id": item.get("component", {}).get("id") or item.get("id"),
                "kind": item.get("component", {}).get("kind") or item.get("kind"),
                "model": item.get("model"),
                "parent_component": item.get("component", {}).get("parent_component"),
                "socket": item.get("component", {}).get("socket"),
                "baked": item.get("component", {}).get("baked"),
                "diagnostics": item.get("component", {}).get("diagnostics") or [],
                "required_resources": item.get("component", {}).get("required_resources") or [],
            }
            for item in linked
        ]
        for glb_name, key in (("preview.glb", "preview"), ("character.glb", "download")):
            glb_path = output / glb_name
            if glb_path.is_file():
                entry["probe_outputs"][glb_name] = summaries.get(
                    key) or character_models.glb_summary(glb_path)
        blend_path = output / "model.blend"
        if blend_path.is_file():
            entry["probe_outputs"]["model.blend"] = {
                "bytes": blend_path.stat().st_size}
    except Exception as exc:  # noqa: BLE001 - diagnostic mode records errors and continues
        entry["status"] = "error"
        entry["errors"].append({
            "stage": "build_bundle",
            "error_type": type(exc).__name__,
            "message": str(exc),
        })

    cooked = root / "source-json"
    converted = root / "converted"
    model_pkg = form.get("model")
    if isinstance(model_pkg, str) and model_pkg:
        if not (converted / "Client/Content" / (model_pkg + ".uemodel")).is_file() and asset_cache:
            asset_key = ("/".join(model_pkg.split("/")[:5]).casefold(), False)
            cached = asset_cache.get(asset_key)
            if isinstance(cached, tuple) and len(cached) >= 3:
                _, cached_converted, cached_cooked, *_ = cached
                if (cached_converted / "Client/Content" / (model_pkg + ".uemodel")).is_file():
                    converted = cached_converted
                    cooked = cached_cooked
        entry["mesh_found"] = (
            converted / "Client/Content" / (model_pkg + ".uemodel")).is_file()
        skeleton = _skeleton_from_cooked(cooked, model_pkg)
        entry["skeleton"] = skeleton
        if skeleton:
            entry["skeleton_found"] = (
                cooked / "Client/Content" / (skeleton + ".json")).is_file()
            if not entry["skeleton_found"]:
                entry["warnings"].append(
                    f"Skeleton metadata not in primary source-json: {skeleton}")
        report_data = _read_json(root / "bundle/report.json")
        if not isinstance(report_data, dict):
            report_data = _read_json(root / "report.json")
        entry["materials"] = _material_inventory_summary(
            cooked, converted, model_pkg, report_data if isinstance(
                report_data, dict) else None
        )
        if entry["materials"].get("missing_textures"):
            entry["warnings"].append(
                f"Missing textures: {len(entry['materials']['missing_textures'])}"
            )
        if entry["materials"].get("missing_material_dependencies"):
            entry["warnings"].append(
                "Missing material dependencies: "
                + ", ".join(entry["materials"]
                            ["missing_material_dependencies"])
            )
        if check_animations and (form.get("montages") or comp.get("bindings")):
            anim_cooked = cooked
            if local_client is not None or os.getenv("WUWA_FMODEL_PATH"):
                try:
                    fmodel = character_models.tool_path("WUWA_FMODEL_PATH")
                    converter = character_models.tool_path(
                        "WUWA_TEXTURE_CONVERTER_PATH")
                    anim_root = root / "anim-metadata"
                    anim_root.mkdir(exist_ok=True)
                    extract_options = {
                        "local_client": local_client} if local_client is not None else {}
                    _, _, anim_cooked, _ = await character_models.extract_model(
                        anim_root, form, fmodel, converter, True, metadata_only=True, **extract_options
                    )
                except Exception as exc:  # noqa: BLE001
                    entry["warnings"].append(
                        f"Animation metadata check failed: {exc}")
            entry["animation_check"] = _check_animation_metadata(
                anim_cooked, skeleton, form)
            if entry["animation_check"]["binding_errors"]:
                entry["status"] = "error"
                for err in entry["animation_check"]["binding_errors"]:
                    entry["errors"].append({
                        "stage": "animation_metadata",
                        "error_type": "ValueError",
                        "message": err,
                    })
    blender_log = root / "blender.log"
    if entry["status"] == "error" and blender_log.is_file():
        lines = blender_log.read_text(
            encoding="utf-8", errors="replace").splitlines()
        entry["blender_log_tail"] = lines[-20:]
    return entry, linked


async def diagnose_scene_bundles(
    staging: Path,
    forms: list[dict],
    *,
    local_client=None,
    report_progress=None,
    export_blender: bool = True,
    check_animations: bool = True,
    material_variants: bool | None = None,
) -> dict:
    """Run full preflight diagnostics across all forms and linked components without packing animations."""
    from wuwa_story_worker.model_material_forms import expand_material_replacement_forms

    collapse_variants = bool(
        material_variants
        if material_variants is not None
        else os.getenv("WUWA_MODEL_MATERIAL_VARIANTS", "").lower() in {"1", "true"}
    )
    forms = await expand_material_replacement_forms(
        staging,
        forms,
        local_client=local_client,
        collapse_variants=collapse_variants,
    )
    previous_blender_export = os.environ.get("WUWA_MODEL_BLENDER_EXPORT")
    if export_blender:
        os.environ["WUWA_MODEL_BLENDER_EXPORT"] = "1"
    entries: list[dict] = []
    asset_cache: dict = {}
    asset_locks: dict = {}
    semaphore = asyncio.Semaphore(
        character_models.model_form_workers(include_animations=False))

    async def diagnose_bounded(root: Path, item: dict):
        async with semaphore:
            return await _diagnose_single_form(
                root, item, local_client=local_client,
                report_progress=report_progress, check_animations=check_animations,
                asset_cache=asset_cache, asset_locks=asset_locks,
            )

    try:
        if forms:
            first_root = staging / (forms[0].get("id") or "form-0")
            first_result = await diagnose_bounded(first_root, forms[0])
            rest_tasks = [
                asyncio.create_task(
                    diagnose_bounded(
                        staging / (form.get("id") or f"form-{index}"), form)
                )
                for index, form in enumerate(forms[1:], start=1)
            ]
            rest_results = await asyncio.gather(*rest_tasks) if rest_tasks else []
            form_results = [first_result, *rest_results]

            child_task_groups: list[list[asyncio.Task]] = []
            for form, (entry, linked) in zip(forms, form_results, strict=True):
                prepared_components = character_models.prepare_linked_components(
                    form, linked) if linked else []
                group = []
                for child_index, (recipe, (component_id, relation)) in enumerate(
                    zip(linked, prepared_components, strict=True)
                ):
                    child_form = {**recipe, "id": component_id, "label": relation["label"],
                                  "order": child_index, "component": relation}
                    child_root = staging / component_id
                    group.append(asyncio.create_task(
                        diagnose_bounded(child_root, child_form)))
                child_task_groups.append(group)

            all_child_tasks = [
                task for group in child_task_groups for task in group]
            if all_child_tasks:
                await asyncio.gather(*all_child_tasks)

            for (entry, _), child_group in zip(form_results, child_task_groups, strict=True):
                entries.append(entry)
                for task in child_group:
                    child_entry, _ = task.result()
                    entries.append(child_entry)
    finally:
        if export_blender:
            if previous_blender_export is None:
                os.environ.pop("WUWA_MODEL_BLENDER_EXPORT", None)
            else:
                os.environ["WUWA_MODEL_BLENDER_EXPORT"] = previous_blender_export

    error_count = sum(1 for item in entries if item["status"] != "ok")
    return {
        "format": "solaris-model-preflight-diagnostic-v1",
        "animations_packed": False,
        "forms_total": len(forms),
        "entries_total": len(entries),
        "ok_count": len(entries) - error_count,
        "error_count": error_count,
        "entries": entries,
    }


def diagnose_workspace(workspace: Path) -> dict:
    """Inspect a retained model export workspace without modifying files."""
    components = []
    for folder in sorted(workspace.iterdir()):
        if not folder.is_dir() or not (folder / "source-json").is_dir():
            continue
        cooked = folder / "source-json"
        converted = folder / "converted"
        meshes = []
        for uemodel in sorted(converted.rglob("*.uemodel")):
            relative = uemodel.relative_to(converted).with_suffix("")
            rows = _read_json(cooked / (relative.as_posix() + ".json"))
            materials = []
            skeleton = None
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for slot in row.get("SkeletalMaterials", []):
                        if isinstance(slot, dict):
                            ref = (slot.get("MaterialInterface") or slot.get(
                                "Material") or {}).get("ObjectPath")
                            if ref:
                                materials.append(ref)
                    skel = row.get("Properties", {}).get(
                        "Skeleton", {}).get("ObjectPath")
                    if skel and not skeleton:
                        skeleton = skel
            meshes.append({
                "mesh": relative.as_posix(),
                "skeleton": skeleton,
                "materials": materials,
            })
        errors = []
        log = folder / "blender.log"
        if log.is_file():
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                if any(token in line for token in ("Error", "ValueError", "RuntimeError", "Traceback")):
                    errors.append(
                        {"source": "blender.log", "error": line.strip()})
        report = _read_json(
            folder / "bundle/report.json") or _read_json(folder / "report.json")
        components.append({
            "workspace": folder.name,
            "meshes": meshes,
            "report": report,
            "errors": errors,
        })
    return {"format": "solaris-model-workspace-diagnostic-v1", "components": components}
