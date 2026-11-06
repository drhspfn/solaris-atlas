"""Resolve playable forms through authored IDs, never nearby meshes or portraits."""

import json

from .model_sources import authored_package, model_sources, npc_model_sources


def resolve_npc_forms(npc):
    """Keep distinct effective instance recipes instead of choosing an arbitrary spawn."""
    if not isinstance(npc, dict) or type(npc.get("blueprint_id")) is not int:
        raise ValueError("Invalid NPC source identity")
    base = npc.get("components") or {}
    if not isinstance(base, dict):
        raise ValueError("Invalid NPC component recipe")

    def merge(left, right):
        result = dict(left)
        for key, value in right.items():
            old = result.get(key)
            result[key] = merge(old, value) if isinstance(
                old, dict) and isinstance(value, dict) else value
        return result

    candidates = [(base, None)]
    for instance in npc.get("instances", []):
        overrides = instance.get("components") or {}
        if not isinstance(overrides, dict):
            raise ValueError("Invalid NPC instance components")
        candidates.append((merge(base, overrides), instance))
    forms, seen = [], {}

    def montage_paths(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if "montage" in key.casefold() and isinstance(child, str) and child.startswith("/Game/"):
                    yield authored_package(child)
                else:
                    yield from montage_paths(child)
        elif isinstance(value, list):
            for child in value:
                yield from montage_paths(child)

    def appearance_fields(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                current = (*path, key)
                if any(word in key.casefold() for word in ("material", "accessor", "attach", "section")):
                    yield ".".join(current), child
                else:
                    yield from appearance_fields(child, current)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from appearance_fields(child, (*path, str(index)))

    for components, instance in candidates:
        component = components.get("ModelComponent") or npc.get("model")
        # Some templates describe only instance models; preserve those instead.
        if not component or component.get('Disabled') is True:
            continue
        model_type = component.get("ModelType") or {}
        if model_type.get('Type') == 'ModelId' and model_type.get('ModelId') == 0:
            continue  # Authored invisible/gameplay-only template, not a missing mesh.
        resolved = npc_model_sources(component, npc.get('model_preloads') or [])
        blueprint = model_type.get("BlueprintPath")
        appearance_components = {
            name: value for name, value in components.items()
            if any(word in name.casefold() for word in ("material", "accessor", "attach", "section"))
        }
        appearance = {
            **resolved,
            "component_blueprint": authored_package(blueprint) if blueprint else resolved.get('component_blueprint'),
            "overrides": dict(appearance_fields(component)),
            "components": appearance_components,
        }
        identity = json.dumps(appearance, sort_keys=True, ensure_ascii=True)
        montages = (set(montage_paths(components)) | set(montage_paths(npc.get("montages", [])))
                    | set(resolved.get('animation_resources', [])))
        if identity in seen:
            form = seen[identity]
            form["montages"] = sorted(set(form["montages"]) | montages)
            continue
        index = len(forms)
        form = {
            "id": "default" if not index else f"mesh-{npc['blueprint_id']}-form-{index + 1}",
            "label": "Default" if not index else f"Instance variant {index + 1}",
            "order": index,
            **resolved,
            "component_blueprint": appearance["component_blueprint"],
            "montages": sorted(montages),
            "npc_components": {"ModelComponent": component, **appearance_components},
            "source": {"table": "blueprintconfig", "id": npc["blueprint_id"],
                       "entity_id": instance.get("entity_id") if instance else None,
                       "map_id": instance.get("map_id") if instance else None},
        }
        forms.append(form)
        seen[identity] = form
    if not forms:
        raise ValueError("NPC has no authored model component")
    return forms


def resolve_recipe_forms(recipe):
    if "npc" in recipe:
        return resolve_npc_forms(recipe["npc"])
    return resolve_model_forms(recipe["role"], recipe["preloads"], recipe["morphs"])


def resolve_model_forms(role, preloads, morphs):
    by_id = {}
    for row in preloads:
        mesh_id = row.get("Id")
        if type(mesh_id) is not int or mesh_id <= 0:
            raise ValueError("Invalid preload Id")
        if mesh_id in by_id:
            raise ValueError(f"Duplicate preload Id: {mesh_id}")
        by_id[mesh_id] = row
    primary_id = role["MeshId"]
    if type(primary_id) is not int or primary_id <= 0:
        raise ValueError("Invalid authored form MeshId")
    primary = model_sources(role, by_id.get(primary_id, {}))
    forms = []
    seen = set()

    def add(mesh_id, label, source):
        if type(mesh_id) is not int or mesh_id <= 0:
            raise ValueError("Invalid authored form MeshId")
        if mesh_id not in by_id:
            raise ValueError(f"Authored form preload is missing: {mesh_id}")
        resolved = model_sources({"MeshId": mesh_id}, by_id[mesh_id])
        # Several gameplay/preload IDs may refer to exactly the same mesh.
        if resolved["model"] in seen:
            return
        seen.add(resolved["model"])
        forms.append(
            {
                "id": f"mesh-{mesh_id}",
                "label": label,
                "order": len(forms),
                **resolved,
                "source": source,
            }
        )

    ui_id = role.get("UiMeshId")
    if type(ui_id) is int and ui_id > 0 and ui_id != primary_id and ui_id in by_id:
        try:
            ui = model_sources({"MeshId": ui_id}, by_id[ui_id])
        except ValueError:
            # Optional UI resources may point to borrowed, unsupported rigs.
            ui = None
        # Older RoleInfo rows borrow another character's UI model. Unlike Morph,
        # UiMeshId alone does not authorize crossing the authored character root.
        if ui and ui["model"].split("/")[:5] == primary["model"].split("/")[:5]:
            add(ui_id, "Default", {"table": "roleinfo",
                "id": role["Id"], "field": "UiMeshId"})
    add(
        primary_id,
        "Battle form" if forms else "Default",
        {"table": "roleinfo", "id": role["Id"], "field": "MeshId"},
    )

    declared_morphs = [
        row for row in morphs if row.get("RoleId") == role["Id"]]
    for row in declared_morphs:
        if any(type(row.get(key)) is not int or row[key] <= 0 for key in ("Id", "Morph")):
            raise ValueError("Invalid declared RoleMorph identity")
    for row in sorted(declared_morphs, key=lambda row: (row["Morph"], row["Id"])):
        # These are display labels for verified RoleMorph identities, not lookup rules.
        label = {(1409, 1): "Fleurdelys", (1210, 1): "Mecha form"}.get(
            (role["Id"], row.get("Morph")
             ), f"Transformed form {row.get('Morph', row['Id'])}"
        )
        add(
            row.get("MeshId"), label, {
                "table": "rolemorph", "id": row["Id"], "role_id": role["Id"]}
        )
    # Denia's two appearances share a gameplay preload, unlike RoleMorph forms.
    # The complete light mesh and the dark Body + Up2 composition were checked
    # against the cooked assets, Blueprint and in-game appearance.
    body = "Aki/Character/Role/FemaleMS/Daniya/R2T1DaniyaMd10011/Model/R2T1DaniyaMd10011_Body"
    if role["Id"] == 1211 and len(forms) == 1 and forms[0]["model"] == body:
        light = {
            **forms[0],
            "label": "Stagecraft",
            "model": body.removesuffix("_Body"),
            "source": {**forms[0]["source"], "composition": "denia-stagecraft-v1"},
        }
        dark = {
            **forms[0],
            "id": f"{forms[0]['id']}-form-2",
            "label": "Breakdown",
            "order": 1,
            "extra_models": [body.removesuffix("_Body") + "_Up2"],
            "source": {**forms[0]["source"], "composition": "denia-breakdown-v1"},
        }
        return [light, dark]
    return forms
