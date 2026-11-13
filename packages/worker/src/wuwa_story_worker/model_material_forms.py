"""Discover material-replacement character forms from KuroMaterialContainerDataCache assets."""

import json
import os
from pathlib import Path

from wuwa_story_worker.character_models import exported_package

_BODY_FILTER_ALLOWED = {
    None,
    "",
    "EKuroCharBodySpecifiedType::Body",
    "EKuroCharBodySpecifiedType::All",
}


def _read_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _skeletal_mesh_material_packages(cooked: Path, model_package: str) -> list[str | None]:
    rows = _read_json(cooked / "Client/Content" / (model_package + ".json"))
    if not isinstance(rows, list):
        return []
    mesh_name = model_package.rsplit("/", 1)[-1]
    for row in rows:
        if not isinstance(row, dict) or row.get("Type") != "SkeletalMesh":
            continue
        if row.get("Name") != mesh_name:
            continue
        materials = row.get("Properties", {}).get("Materials") or []
        result: list[str | None] = []
        for entry in materials:
            if not isinstance(entry, dict):
                result.append(None)
                continue
            obj = entry.get("MaterialInterface")
            obj_path = obj.get("ObjectPath") if isinstance(obj, dict) else None
            result.append(exported_package(obj_path) if isinstance(
                obj_path, str) and obj_path else None)
        return result
    return []


def _find_character_da_dir(cooked: Path, character_name: str) -> Path | None:
    base = cooked / "Client/Content/Aki/Effect/MaterialController/DA_Character"
    if not base.is_dir():
        return None
    target = character_name.casefold()
    for child in sorted(base.iterdir()):
        if child.is_dir() and child.name.casefold() == target:
            return child
    return None


def _replace_base_materials_entries(da_dir: Path) -> list[tuple[str, list[str | None]]]:
    entries: list[tuple[str, list[str | None]]] = []
    for path in sorted(da_dir.rglob("*.json")):
        rows = _read_json(path)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("Type") != "KuroMaterialContainerDataCache":
                continue
            props = row.get("Properties") or {}
            if props.get("MaterialApplyType") != "EKuroCharacterControllerApplyType::ReplaceBaseMaterials":
                continue
            flt = props.get("Filter") or {}
            if flt.get("BodySpecifiedType") not in _BODY_FILTER_ALLOWED:
                continue
            replace_param = props.get("BaseMaterialReplaceParam") or {}
            if replace_param.get("bMatchMaterialIndex") is False:
                continue
            raw_materials = replace_param.get("ReplaceBaseMaterials") or []
            if not raw_materials:
                continue
            packages: list[str | None] = []
            for item in raw_materials:
                obj_path = item.get("ObjectPath") if isinstance(
                    item, dict) else None
                packages.append(
                    exported_package(obj_path) if isinstance(
                        obj_path, str) and obj_path else None
                )
            rel = path.relative_to(
                da_dir.parents[4]).with_suffix("").as_posix()
            entries.append((rel, packages))
            break
    return entries


def discover_material_replacement_forms_from_cooked(
    cooked: Path,
    forms: list[dict],
    *,
    collapse_variants: bool = False,
) -> list[dict]:
    """Append or attach deduplicated ReplaceBaseMaterials variants for each matching base/skin form."""
    expanded: list[dict] = []
    for form in forms:
        model = form.get("model")
        if (
            not isinstance(model, str)
            or not model.startswith("Aki/Character/Role/")
            or form.get("component")
            or form.get("part_material_overrides")
            or form.get("material_variants")
        ):
            expanded.append(form)
            continue
        parts = model.split("/")
        if len(parts) < 5:
            expanded.append(form)
            continue
        da_dir = _find_character_da_dir(cooked, parts[4])
        if da_dir is None:
            expanded.append(form)
            continue
        base_materials = _skeletal_mesh_material_packages(cooked, model)
        model_dir_cf = (model.rsplit("/", 1)[0] + "/").casefold()
        char_root_cf = ("/".join(parts[:5]) + "/").casefold()
        base_sig = tuple((pkg or "").casefold() for pkg in base_materials)
        seen_sigs: set[tuple[str, ...]] = {
            base_sig} if base_materials else set()
        variant_index = 2
        discovered_variants: list[dict] = []
        for da_package, replace_packages in _replace_base_materials_entries(da_dir):
            if base_materials and len(replace_packages) != len(base_materials):
                continue
            non_empty = [pkg for pkg in replace_packages if pkg]
            if not any(pkg.casefold().startswith(model_dir_cf) for pkg in non_empty):
                continue
            if any(
                pkg.casefold().startswith(char_root_cf) and not pkg.casefold().startswith(model_dir_cf)
                for pkg in non_empty
            ):
                continue
            sig = tuple((pkg or "").casefold() for pkg in replace_packages)
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            overrides = {
                str(slot): pkg
                for slot, pkg in enumerate(replace_packages)
                if pkg and (slot >= len(base_materials) or pkg.casefold() != (base_materials[slot] or "").casefold())
            }
            if not overrides:
                continue
            discovered_variants.append({
                "id": f"form-{variant_index}",
                "label": f"Form {variant_index}",
                "overrides": {model: overrides},
                "material_controller": da_package,
            })
            variant_index += 1

        if collapse_variants and discovered_variants:
            expanded.append({
                **form,
                "material_variants": [
                    {"id": "form-1", "label": "Form 1", "overrides": {}},
                    *discovered_variants,
                ],
            })
        else:
            expanded.append(form)
            base_label = form.get("label") or "Default"
            for variant in discovered_variants:
                expanded.append({
                    **form,
                    "id": f"{form['id']}-{variant['id']}",
                    "label": f"{base_label} · {variant['label']}",
                    "part_material_overrides": variant["overrides"],
                    "source": {
                        **(form.get("source") or {}),
                        "material_controller": variant["material_controller"],
                    },
                })
    return [{**item, "order": idx} for idx, item in enumerate(expanded)]


async def expand_material_replacement_forms(
    staging: Path,
    forms: list[dict],
    *,
    local_client=None,
    include_animations: bool = True,
    asset_cache: dict | None = None,
    collapse_variants: bool = False,
) -> list[dict]:
    """Extract DA_Character material controllers once and expand matching ReplaceBaseMaterials forms."""
    role_forms = [
        form for form in forms
        if isinstance(form.get("model"), str)
        and form["model"].startswith("Aki/Character/Role/")
        and len(form["model"].split("/")) >= 5
        and not form.get("component")
        and not form.get("part_material_overrides")
    ]
    if not role_forms:
        return list(forms)
    from wuwa_story_worker import character_models

    if local_client is None and not os.getenv("WUWA_FMODEL_PATH"):
        return list(forms)
    try:
        fmodel = character_models.tool_path("WUWA_FMODEL_PATH")
        converter = character_models.tool_path("WUWA_TEXTURE_CONVERTER_PATH")
    except (FileNotFoundError, ValueError):
        return list(forms)

    da_filters = sorted({
        f"Aki/Effect/MaterialController/DA_Character/{form['model'].split('/')[4]}/"
        for form in role_forms
    })
    model_filters = sorted({form["model"] for form in role_forms})
    discovery_root = staging / ".material-forms-discovery"
    discovery_root.mkdir(parents=True, exist_ok=True)
    if local_client is None:
        character_models.prepare_entity_workspace(staging, discovery_root)
    extract_options = {
        "local_client": local_client} if local_client is not None else {}
    if asset_cache is not None:
        probe_sources = role_forms[0]
        model, converted, cooked, manifest = await character_models.extract_model(
            discovery_root,
            probe_sources,
            fmodel,
            converter,
            include_animations,
            additional_filters=sorted(set(da_filters + model_filters)),
            **extract_options,
        )
        asset_key = character_models.model_asset_key(
            probe_sources["model"],
            include_animations,
        )
        asset_cache[asset_key] = (model, converted, cooked, manifest)
    else:
        probe_sources = {
            "model": role_forms[0]["model"],
            "blueprints": [],
        }
        _, _, cooked, _ = await character_models.extract_model(
            discovery_root,
            probe_sources,
            fmodel,
            converter,
            False,
            additional_filters=sorted(set(da_filters + model_filters)),
            metadata_only=True,
            **extract_options,
        )
    return discover_material_replacement_forms_from_cooked(
        cooked, forms, collapse_variants=collapse_variants
    )
