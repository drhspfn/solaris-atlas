"""Resolve playable forms through authored IDs, never nearby meshes or portraits."""

from .model_sources import model_sources


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
            add(ui_id, "Default", {"table": "roleinfo", "id": role["Id"], "field": "UiMeshId"})
    add(
        primary_id,
        "Battle form" if forms else "Default",
        {"table": "roleinfo", "id": role["Id"], "field": "MeshId"},
    )

    declared_morphs = [row for row in morphs if row.get("RoleId") == role["Id"]]
    for row in declared_morphs:
        if any(type(row.get(key)) is not int or row[key] <= 0 for key in ("Id", "Morph")):
            raise ValueError("Invalid declared RoleMorph identity")
    for row in sorted(declared_morphs, key=lambda row: (row["Morph"], row["Id"])):
        # These are display labels for verified RoleMorph identities, not lookup rules.
        label = {(1409, 1): "Fleurdelys", (1210, 1): "Mecha form"}.get(
            (role["Id"], row.get("Morph")), f"Transformed form {row.get('Morph', row['Id'])}"
        )
        add(
            row.get("MeshId"), label, {"table": "rolemorph", "id": row["Id"], "role_id": role["Id"]}
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
