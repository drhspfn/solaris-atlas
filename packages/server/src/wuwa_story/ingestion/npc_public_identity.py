"""Group source-proven NPC and story-role identities without changing source nodes.

Run with --release-id and optionally --apply; --index rebuilds lexical search.
"""

import argparse
import asyncio
import json
from collections import Counter, defaultdict

from sqlalchemy import select, text

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.read_models.refresh import mark_dirty
from wuwa_story.search.indexer import build_lexical_index

ROLE_PATH = "BinData/role/roleinfo.json"
MODEL_SUFFIX = "/BinData/model_config_preload/modelconfigpreload.json"


def _owned(identity):
    return (
        isinstance(identity, dict)
        and identity.get("identity_source") == "npc_public_identity"
        and identity.get("identity_schema") == 1
    )


def _mesh(model, preloads):
    if not isinstance(model, dict):
        return None
    model = model.get("ModelType") or {}
    if not isinstance(model, dict):
        return None
    paths = []
    if model.get("Type") == "Npc":
        value = model.get("NpcModel") or {}
        if isinstance(value, dict) and value.get("Type") == "Mesh":
            paths = [value.get("Mesh")]
    elif model.get("Type") == "ModelId" and type(model.get("ModelId")) is int:
        paths = (preloads.get(model["ModelId"]) or {}).get("Meshes") or []
    paths = {p for p in paths if isinstance(p, str) and p.startswith("/Game/")}
    return next(iter(paths)) if len(paths) == 1 else None


def identities(characters, roles, preloads):
    """Return canonical-key -> identity; reject ambiguous source ownership."""
    by_key = {c["key"]: c for c in characters}
    role_counts = Counter(r.get("Id") for r in roles)
    roles_by_id = {
        r["Id"]: r for r in roles if type(r.get("Id")) is int and role_counts[r["Id"]] == 1
    }
    preload_counts = Counter(p.get("Id") for p in preloads)
    models = {
        p["Id"]: p for p in preloads if type(p.get("Id")) is int and preload_counts[p["Id"]] == 1
    }
    class_models = defaultdict(set)
    for model_id, model in models.items():
        path = model.get("ActorClassPath")
        if isinstance(path, str) and path.startswith("/Game/"):
            class_models[path].add(model_id)
    playable = {
        r["Id"]: r
        for r in roles_by_id.values()
        if r.get("RoleType") == 1 and f"character:{r['Id']}" in by_key
    }
    groups = defaultdict(dict)

    def add(anchor, key, resolution, **evidence):
        groups[anchor][key] = {"resolution": resolution, **evidence}

    for role in roles_by_id.values():
        role_id = role["Id"]
        key = f"character:{role_id}"
        if role.get("RoleType") not in (2, 4, 5) or key not in by_key:
            continue
        parent = playable.get(role.get("ParentId"))
        if parent and role.get("Name") and role["Name"] == parent.get("Name"):
            add(
                f"character:{parent['Id']}",
                key,
                "parent_role",
                parent_id=parent["Id"],
                name_key=role["Name"],
            )
            continue
        personal_fields = ("Name", "Introduction", "FormationRoleCard")
        personal = tuple(role.get(field) for field in personal_fields)
        if all(isinstance(value, str) and value for value in personal):
            owners = [
                r
                for r in playable.values()
                if tuple(r.get(field) for field in personal_fields) == personal
            ]
            template = roles_by_id.get(role.get("SkillId"))
            inherited_foreign = (
                template
                and len(owners) == 1
                and template["Id"] != owners[0]["Id"]
                and tuple(template.get(field) for field in personal_fields) == personal
            )
            if len(owners) == 1 and not inherited_foreign:
                add(
                    f"character:{owners[0]['Id']}",
                    key,
                    "role_name_intro_and_card",
                    name_key=personal[0],
                    introduction_key=personal[1],
                    card=personal[2],
                )
                continue
        matches = [
            r
            for r in playable.values()
            if role.get("Name")
            and role["Name"] == r.get("Name")
            and type(role.get("MeshId")) is int
            and role["MeshId"] > 0
            and role["MeshId"] == r.get("MeshId")
        ]
        if len(matches) == 1:
            add(
                f"character:{matches[0]['Id']}",
                key,
                "role_name_and_model",
                name_key=role["Name"],
                model_id=role["MeshId"],
            )

    role_meshes = defaultdict(set)
    mesh_owners = defaultdict(set)
    for rid, role in playable.items():
        character = by_key[f"character:{rid}"]
        mid = role.get("MeshId")
        if type(mid) is int and mid > 0 and character.get("name"):
            model = {"ModelType": {"Type": "ModelId", "ModelId": mid}}
            mesh = _mesh(model, models)
            if mesh:
                role_meshes[(character["name"].strip(), mesh)].add(f"character:{rid}")
                mesh_owners[mesh].add(f"character:{rid}")

    npc_groups = defaultdict(list)
    npc_classes = {}
    for c in characters:
        if not c["key"].startswith("character:npc:") or not c.get("name"):
            continue
        npc = c.get("npc") or {}
        name_key = c.get("name_key")
        if not isinstance(name_key, str) or not name_key:
            continue
        variants = npc.get("name_variants") or []
        keys = {(v.get("name") or {}).get("key") for v in variants if isinstance(v, dict)}
        if keys and keys != {name_key}:
            continue
        local_models = {
            p.get("Id"): p for p in (npc.get("model_preloads") or []) if isinstance(p, dict)
        }
        mesh = _mesh(npc.get("model"), local_models)
        base = npc.get("model") or {}
        overrides = c.get("model_overrides") or []
        if any(
            not isinstance(value, dict) or _mesh({**base, **value}, local_models) != mesh
            for value in overrides
        ):
            continue
        model_type = (base.get("ModelType") or {}) if isinstance(base, dict) else {}
        model_id = model_type.get("ModelId") if isinstance(model_type, dict) else None
        preload = local_models.get(model_id) or {}
        actor_class = preload.get("ActorClassPath")
        if (
            type(model_id) is int
            and isinstance(actor_class, str)
            and len(class_models.get(actor_class, ())) == 2
            and model_id in class_models[actor_class]
        ):
            npc_classes[c["key"]] = actor_class
        npc_groups[(name_key, c["name"].strip())].append((c["key"], mesh))

    for (name_key, name), entries in npc_groups.items():
        candidates = set().union(
            *(role_meshes.get((name, mesh), set()) for _, mesh in entries if mesh)
        )
        conflicting = set().union(*(mesh_owners.get(mesh, set()) for _, mesh in entries if mesh))
        if len(candidates) > 1 or (candidates and conflicting - candidates):
            continue
        if not candidates:
            by_class = defaultdict(list)
            for key, _ in entries:
                if key in npc_classes:
                    by_class[npc_classes[key]].append(key)
            class_partitions = [
                (keys, None, actor_class) for actor_class, keys in by_class.items() if len(keys) > 1
            ]
            used = {key for keys, _, _ in class_partitions for key in keys}
            by_mesh = defaultdict(list)
            for key, mesh in entries:
                if mesh and key not in used:
                    by_mesh[mesh].append(key)
            partitions = class_partitions + [
                (keys, mesh, None) for mesh, keys in by_mesh.items() if len(keys) > 1
            ]
        else:
            partitions = [([key for key, _ in entries], None, None)]
        anchor_mesh = next(
            (
                mesh
                for _, mesh in entries
                if mesh and role_meshes.get((name, mesh), set()) & candidates
            ),
            None,
        )
        for members, partition_mesh, actor_class in partitions:
            _add_npc_group(
                groups,
                by_key,
                members,
                candidates,
                name_key,
                partition_mesh,
                entries,
                anchor_mesh,
                actor_class,
            )

    result = {}
    for anchor, members in groups.items():
        if anchor not in by_key:
            continue
        all_keys = {anchor, *members}
        # Never overwrite Rover or another independently owned family.
        if any(
            by_key[k].get("identity")
            and (not _owned(by_key[k]["identity"]) or by_key[k]["identity"].get("family") != anchor)
            for k in all_keys
        ):
            continue
        has_npc = any(key.startswith("character:npc:") for key in all_keys)
        has_story = any(
            key.startswith("character:")
            and key.rsplit(":", 1)[-1].isdigit()
            and roles_by_id.get(int(key.rsplit(":", 1)[-1]), {}).get("RoleType") in (2, 4, 5)
            for key in all_keys
            if not key.startswith("character:npc:")
        )
        for key in all_keys:
            proof = members.get(key, {"resolution": "public_identity_representative"})
            result[key] = {
                "family": anchor,
                "canonical_key": anchor,
                "variant_key": key,
                "selectable": False,
                "has_npc": has_npc,
                "has_story": has_story,
                "identity_source": "npc_public_identity",
                "identity_schema": 1,
                **proof,
            }
    return result


def _add_npc_group(
    groups, by_key, members, candidates, name_key, partition_mesh, entries, anchor_mesh, actor_class
):
    existing = {(by_key[key].get("identity") or {}).get("canonical_key") for key in members}
    existing.discard(None)
    if len(existing) > 1 or (existing and next(iter(existing)) not in {*members, *candidates}):
        return
    if candidates and existing and next(iter(existing)) not in candidates:
        return
    anchor = (
        next(iter(candidates))
        if candidates
        else next(iter(existing))
        if existing
        else min(members, key=lambda k: int(k.rsplit(":", 1)[1]))
    )
    if len(members) == 1 and not candidates:
        return
    for key in members:
        if key != anchor:
            groups[anchor][key] = {
                "resolution": "scoped_name_key_with_playable_mesh"
                if candidates
                else "npc_name_key_and_actor_class"
                if actor_class
                else "npc_name_key_and_mesh",
                "name_key": name_key,
                "mesh": dict(entries).get(key) if candidates else partition_mesh,
                "anchor_mesh": anchor_mesh,
                "actor_class": actor_class,
            }


async def backfill(
    session, release_id, *, apply=False, index=False, commit=True, model_source=None
):
    if index and not apply:
        raise ValueError("--index requires --apply")
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Unknown release")
    latest = await session.scalar(
        select(GameRelease.id).order_by(GameRelease.sequence.desc()).limit(1)
    )
    if latest != release_id:
        raise ValueError("Public identity backfill requires the latest release")
    paths = list(
        await session.scalars(
            select(SourceFile.logical_source_path).where(
                SourceFile.release_id == release_id,
                SourceFile.logical_source_path.endswith(MODEL_SUFFIX),
            )
        )
    )
    if model_source is not None:
        paths = [path for path in paths if path == model_source]
    if len(paths) != 1:
        raise ValueError("Select exactly one retained native model preload table")
    source_rows = list(
        await session.execute(
            select(SourceFile.logical_source_path, SourceRecord.data)
            .join(SourceRecord, SourceRecord.source_file_id == SourceFile.id)
            .where(
                SourceFile.release_id == release_id,
                SourceRecord.release_id == release_id,
                SourceFile.logical_source_path.in_([ROLE_PATH, paths[0]]),
            )
        )
    )
    roles = [data for path, data in source_rows if path == ROLE_PATH]
    preloads = [data for path, data in source_rows if path == paths[0]]
    if not roles or not preloads:
        raise ValueError("Missing retained role or native model source")
    rows = (
        (
            await session.execute(
                text("""
        SELECT c.node_id id, n.canonical_key key, k.key name_key, t.content name,
               c.metadata->'public_identity' identity,
               jsonb_path_query_array(c.metadata, '$.npc.instances[*].components.ModelComponent') model_overrides,
               jsonb_build_object('model',c.metadata->'npc'->'model',
                 'model_preloads',c.metadata->'npc'->'model_preloads',
                 'name_variants',c.metadata->'npc'->'name_variants') npc
        FROM core.character c JOIN graph.node n ON n.id=c.node_id
        LEFT JOIN i18n.localization_key k ON k.id=c.name_key_id
        LEFT JOIN i18n.locale l ON l.code='en'
        LEFT JOIN i18n.localization_value v ON v.key_id=k.id AND v.locale_id=l.id AND v.release_id=:release
        LEFT JOIN i18n.localization_content t ON t.id=v.content_id
        WHERE n.status='active'
    """),
                {"release": release_id},
            )
        )
        .mappings()
        .all()
    )
    characters = [dict(row) for row in rows]
    proposed = identities(characters, roles, preloads)
    changed = [
        c
        for c in characters
        if (c["key"] in proposed and c["identity"] != proposed[c["key"]])
        or (c["key"] not in proposed and _owned(c["identity"]))
    ]
    affected = {
        identity["canonical_key"]
        for c in changed
        for identity in (c["identity"], proposed.get(c["key"]))
        if isinstance(identity, dict) and identity.get("canonical_key")
    }
    refresh = sorted(
        c["id"]
        for c in characters
        if c["key"] in affected
        or any(
            isinstance(identity, dict) and identity.get("canonical_key") in affected
            for identity in (c["identity"], proposed.get(c["key"]))
        )
    )
    if apply and (changed or index):
        for offset in range(0, len(changed), 500):
            batch = changed[offset : offset + 500]
            objects = (
                await session.scalars(
                    select(Character).where(Character.node_id.in_([c["id"] for c in batch]))
                )
            ).all()
            for character in objects:
                key = next(c["key"] for c in batch if c["id"] == character.node_id)
                metadata = dict(character.metadata_json or {})
                if key in proposed:
                    metadata["public_identity"] = proposed[key]
                else:
                    metadata.pop("public_identity", None)
                character.metadata_json = metadata
        for offset in range(0, len(refresh), 1000):
            await mark_dirty(session, "character", refresh[offset : offset + 1000])
        await session.flush()
        if index:
            await build_lexical_index(
                session, release.game_version, categories={"character"}, release_id=release_id
            )
        if commit:
            await session.commit()
    return {
        "release_id": release_id,
        "applied": apply,
        "groups": len({v["canonical_key"] for v in proposed.values()}),
        "members": len(proposed),
        "changed": len(changed),
        "refresh_ids": refresh,
        "changes": [
            {"id": c["id"], "key": c["key"], "identity": proposed.get(c["key"])} for c in changed
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--model-source", help="Pinned retained ClientData model preload path")
    args = parser.parse_args()

    async def run():
        async with SessionFactory() as session:
            print(
                json.dumps(
                    await backfill(
                        session,
                        args.release_id,
                        apply=args.apply,
                        index=args.index,
                        model_source=args.model_source,
                    )
                )
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
