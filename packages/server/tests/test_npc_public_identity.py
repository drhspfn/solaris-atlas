from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import npc_public_identity as module
from wuwa_story.ingestion.npc_public_identity import identities


def character(key, name, name_key, model=None, identity=None, overrides=False):
    return {
        "key": key,
        "name": name,
        "name_key": name_key,
        "identity": identity,
        "model_overrides": [{"ModelType": {"Type": "Npc"}}] if overrides else [],
        "npc": {"model": model, "model_preloads": [], "name_variants": []},
    }


def mesh(path):
    return {"ModelType": {"Type": "Npc", "NpcModel": {"Type": "Mesh", "Mesh": path}}}


def test_npc_group_requires_same_source_name_and_unambiguous_personal_mesh():
    chars = [
        character("character:npc:2", "Ada", "npc_ada", mesh("/Game/A")),
        character("character:npc:1", "Ada", "npc_ada", mesh("/Game/A")),
        character("character:npc:3", "Ada", "npc_ada", mesh("/Game/B")),
        character("character:npc:4", "Ada", "other_key", mesh("/Game/A")),
        character("character:npc:5", "Ada", "npc_ada", mesh("/Game/A"), overrides=True),
    ]
    result = identities(chars, [], [])
    assert set(result) == {"character:npc:1", "character:npc:2"}
    assert result["character:npc:2"]["canonical_key"] == "character:npc:1"
    assert result["character:npc:2"]["name_key"] == "npc_ada"
    assert result["character:npc:1"]["has_npc"] is True
    assert result["character:npc:1"]["has_story"] is False


def test_parented_roles_and_npc_map_to_playable_without_merging_name_only():
    chars = [
        character("character:10", "Ada", "role_ada"),
        character("character:11", "Ada", "role_ada"),
        character("character:12", "Other", "role_other"),
        character("character:npc:3", "Ada", "npc_ada", mesh("/Game/A")),
        character("character:npc:4", "Ada", "npc_ada", mesh("/Game/B")),
    ]
    roles = [
        {"Id": 10, "RoleType": 1, "Name": "role_ada", "MeshId": 100},
        {"Id": 11, "RoleType": 2, "Name": "role_ada", "ParentId": 10},
        {"Id": 12, "RoleType": 2, "Name": "role_other", "ParentId": 10},
    ]
    result = identities(chars, roles, [{"Id": 100, "Meshes": ["/Game/A"]}])
    assert result["character:11"]["resolution"] == "parent_role"
    assert result["character:npc:3"]["canonical_key"] == "character:10"
    assert result["character:10"]["has_npc"] is True
    assert result["character:10"]["has_story"] is True
    assert "character:12" not in result
    assert result["character:npc:4"]["canonical_key"] == "character:10"
    assert result["character:npc:4"]["mesh"] == "/Game/B"


def test_scoped_name_variant_rejects_another_playable_persons_mesh():
    chars = [
        character("character:10", "Ada", "role_ada"),
        character("character:20", "Bea", "role_bea"),
        character("character:npc:3", "Ada", "npc_ada", mesh("/Game/A")),
        character("character:npc:4", "Ada", "npc_ada", mesh("/Game/B")),
    ]
    roles = [
        {"Id": 10, "RoleType": 1, "Name": "role_ada", "MeshId": 100},
        {"Id": 20, "RoleType": 1, "Name": "role_bea", "MeshId": 200},
    ]
    assert (
        identities(
            chars, roles, [{"Id": 100, "Meshes": ["/Game/A"]}, {"Id": 200, "Meshes": ["/Game/B"]}]
        )
        == {}
    )


def test_role_personal_triple_resolves_foreign_parent_and_rejects_ambiguity():
    chars = [
        character("character:10", "Ada", "role_ada"),
        character("character:11", "Ada", "role_ada"),
        character("character:12", "Ada", "role_ada"),
        character("character:20", "Other", "other"),
    ]
    triple = {"Name": "role_ada", "Introduction": "intro_ada", "FormationRoleCard": "/Game/card"}
    roles = [
        {"Id": 10, "RoleType": 1, "MeshId": 1, **triple},
        {"Id": 11, "RoleType": 2, "ParentId": 20, "SkillId": 20, "MeshId": 2, **triple},
        {"Id": 12, "RoleType": 2, "ParentId": 0, "SkillId": 20, "MeshId": 3, **triple},
        {"Id": 20, "RoleType": 1, "Name": "other", "MeshId": 4},
    ]
    result = identities(chars, roles, [])
    assert result["character:11"]["canonical_key"] == "character:10"
    assert result["character:12"]["resolution"] == "role_name_intro_and_card"
    roles[1]["ParentId"] = 0
    roles[3].update(triple)
    assert "character:11" not in identities(chars, roles, [])


def test_same_scoped_name_and_rare_actor_class_join_different_meshes():
    models = [
        {"Id": 1, "Meshes": ["/Game/A"], "ActorClassPath": "/Game/BP"},
        {"Id": 2, "Meshes": ["/Game/B"], "ActorClassPath": "/Game/BP"},
    ]
    chars = []
    for identity, key, model in [(1, "abby", 1), (2, "abby", 2), (3, "other", 2)]:
        char = character(
            f"character:npc:{identity}",
            "Abby",
            key,
            {"ModelType": {"Type": "ModelId", "ModelId": model}},
        )
        char["npc"]["model_preloads"] = models
        chars.append(char)
    result = identities(chars, [], models)
    assert result["character:npc:2"]["canonical_key"] == "character:npc:1"
    assert result["character:npc:2"]["resolution"] == "npc_name_key_and_actor_class"
    assert "character:npc:3" not in result
    assert identities(chars, [], models + [{"Id": 3, "ActorClassPath": "/Game/BP"}]) == {}


def test_existing_rover_and_ambiguous_playable_models_are_preserved():
    rover = {"family": "rover", "canonical_key": "character:10"}
    chars = [
        character("character:10", "Rover", "rover", identity=rover),
        character("character:11", "Rover", "rover"),
        character("character:npc:2", "Rover", "npc_rover", mesh("/Game/A")),
    ]
    roles = [
        {"Id": 10, "RoleType": 1, "Name": "rover", "MeshId": 100},
        {"Id": 11, "RoleType": 1, "Name": "rover", "MeshId": 100},
    ]
    assert identities(chars, roles, [{"Id": 100, "Meshes": ["/Game/A"]}]) == {}


def test_existing_npc_anchor_remains_stable_when_a_lower_id_appears():
    existing = {
        "family": "character:npc:2",
        "canonical_key": "character:npc:2",
        "identity_source": "npc_public_identity",
        "identity_schema": 1,
    }
    chars = [
        character("character:npc:1", "Ada", "npc_ada", mesh("/Game/A")),
        character("character:npc:2", "Ada", "npc_ada", mesh("/Game/A"), existing),
        character("character:npc:3", "Ada", "npc_ada", mesh("/Game/A"), existing),
    ]
    assert identities(chars, [], [])["character:npc:1"]["canonical_key"] == "character:npc:2"


@pytest.mark.asyncio
async def test_dry_run_and_apply_are_idempotent_and_dirty_every_family_member(monkeypatch):
    chars = [
        character("character:npc:1", "Ada", "ada_key", mesh("/Game/A")),
        character("character:npc:2", "Ada", "ada_key", mesh("/Game/A")),
    ]
    for index, char in enumerate(chars, 1):
        char["id"] = index
    objects = [SimpleNamespace(node_id=i, metadata_json={"retained": i}) for i in (1, 2)]
    source_path = "ClientData/3.7.1/hash" + module.MODEL_SUFFIX
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(game_version="3.7.0")),
        scalar=AsyncMock(return_value=21),
        scalars=AsyncMock(),
        execute=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    session.scalars.side_effect = lambda statement: (
        [source_path] if "source_file" in str(statement) else SimpleNamespace(all=lambda: objects)
    )

    async def execute(statement, *args):
        if "source_record" in str(statement):
            return [(module.ROLE_PATH, {"Id": 10}), (source_path, {"Id": 100})]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: chars))

    session.execute.side_effect = execute
    dirty = AsyncMock()
    monkeypatch.setattr(module, "mark_dirty", dirty)
    preview = await module.backfill(session, 21)
    assert preview["changed"] == 2 and preview["refresh_ids"] == [1, 2]
    session.commit.assert_not_awaited()
    result = await module.backfill(session, 21, apply=True)
    assert result["changed"] == 2 and objects[0].metadata_json["retained"] == 1
    dirty.assert_awaited_once_with(session, "character", [1, 2])
    for char, obj in zip(chars, objects, strict=True):
        char["identity"] = obj.metadata_json["public_identity"]
    dirty.reset_mock()
    assert (await module.backfill(session, 21, apply=True))["changed"] == 0
    dirty.assert_not_awaited()
    chars[1]["npc"]["model"] = mesh("/Game/changed")
    removed = await module.backfill(session, 21, apply=True)
    assert removed["changed"] == 2 and removed["refresh_ids"] == [1, 2]
    assert all("public_identity" not in obj.metadata_json for obj in objects)
    dirty.assert_awaited_once_with(session, "character", [1, 2])
