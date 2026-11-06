import json
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from wuwa_story.ingestion.model_archives import write_archive
from wuwa_story.ingestion.model_uploads import ModelUploadJob

from wuwa_story_worker import character_models, model_uploads


def portable(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    data = {"asset": {"version": "2.0"}, "nodes": [{"name": "Root"}],
            "skins": [{"joints": [0]}], "meshes": [{"primitives": []}]}
    payload = json.dumps(data).encode()
    payload += b" " * (-len(payload) % 4)
    raw = struct.pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    for name in ("preview.glb", "character.glb"):
        (output / name).write_bytes(raw)
    form = {"id": "mesh-1", "label": "Default", "order": 0, "model": "Aki/Character/Role/Test/Model/Rig",
            "blueprints": [], "source": {"table": "roleinfo", "id": 1}}
    archive = tmp_path / "model.zip"
    write_archive(archive, {"character_key": "character:1", "game_version": "3.3.0",
        "asset_version": "3.7.0", "tier": "hd"}, [(form, output, {}, {"model": form["model"]})])
    job = ModelUploadJob(run_id=1, character_key="character:1", owner_node_id=2, release_id=3,
        game_version="3.3.0", archive_bytes=archive.stat().st_size, storage_token="a" * 32)
    return archive, form, job


def test_decoding_uses_archive_identity_and_rejects_stale_authored_forms(tmp_path):
    archive, form, job = portable(tmp_path)
    publication, bundles = model_uploads.decode_bundles(archive, tmp_path / "decoded", job, [form])
    assert publication.asset_version == "3.7.0" and not publication.include_animations
    assert len(bundles) == 1 and bundles[0][2]["download"]["bones"] == 1
    with pytest.raises(ValueError, match="no longer match"):
        model_uploads.decode_bundles(archive, tmp_path / "stale", job, [{**form, "model": "Other"}])
    wrong = job.model_copy(update={"character_key": "character:2"})
    with pytest.raises(ValueError, match="another character"):
        model_uploads.decode_bundles(archive, tmp_path / "wrong", wrong, [form])


def test_npc_data_asset_archive_retains_resolved_mesh_and_checks_setup_identity(tmp_path):
    _, base, job = portable(tmp_path)
    form = {**base, 'model': 'Aki/Npc/Setup', 'data_asset': 'Aki/Npc/Setup'}
    job = job.model_copy(update={'character_key': 'character:npc:1908'})
    provenance = {'model': 'Aki/Npc/MasterMesh', 'data_asset': form['data_asset']}
    identity = {'character_key': job.character_key, 'game_version': job.game_version,
                'asset_version': '3.7.1', 'tier': 'hd'}
    archive = tmp_path / 'npc.zip'
    write_archive(archive, identity, [(form, tmp_path/'bundle', {}, provenance)])
    _, bundles = model_uploads.decode_bundles(archive, tmp_path/'npc-decoded', job, [form])
    assert bundles[0][3]['model'] == 'Aki/Npc/MasterMesh'
    wrong = tmp_path/'wrong-setup.zip'
    write_archive(wrong, identity, [(form, tmp_path/'bundle', {}, {**provenance, 'data_asset': 'Other'})])
    with pytest.raises(ValueError, match='setup does not match'):
        model_uploads.decode_bundles(wrong, tmp_path/'npc-wrong', job, [form])


@pytest.mark.asyncio
async def test_uploaded_model_publishes_without_game_client_or_extraction(tmp_path, monkeypatch):
    archive, form, job = portable(tmp_path)
    run = NS(status="queued", metadata_json={"payload": job.model_dump()})
    session = NS(get=AsyncMock(return_value=run))

    @asynccontextmanager
    async def context(*args):
        yield session

    storage = NS(delete=AsyncMock())

    async def get(key):
        yield archive.read_bytes()

    storage.get = get
    monkeypatch.setattr(model_uploads, "S3Storage", lambda _: storage)
    monkeypatch.setattr(model_uploads, "engine", NS(connect=context))
    monkeypatch.setattr(model_uploads, "job_lock", context)
    monkeypatch.setattr(model_uploads, "SessionFactory", context)
    monkeypatch.setattr(model_uploads, "asset_workspace", lambda: tmp_path / "not-installed-client")
    monkeypatch.setattr(model_uploads, "update_admin_run", AsyncMock())
    monkeypatch.setattr(character_models, "source_row", AsyncMock(return_value={"Id": 1}))
    monkeypatch.setattr(character_models, "source_forms", AsyncMock(return_value=[form]))
    monkeypatch.setattr(character_models, "extract_model", AsyncMock(side_effect=AssertionError("must not extract")))
    publish = AsyncMock()
    monkeypatch.setattr(character_models, "publish_forms", publish)
    await character_models.process_character_model(job.model_dump())
    publish.assert_awaited_once()
    assert publish.call_args.kwargs["publication_payload"] == job.model_dump()
    storage.delete.assert_awaited_once()
    assert not list((tmp_path / "transient-jobs").glob("*/model.zip"))


def test_decode_component_checks_parent_source_and_allows_static_mesh(tmp_path):
    _, root, job = portable(tmp_path)
    root = {**root, "component_blueprint": "Aki/Npc/Blueprint"}
    output = tmp_path / "static"
    output.mkdir()
    payload = json.dumps({"asset": {"version": "2.0"}, "nodes": [],
                          "meshes": [{"primitives": []}]}).encode()
    payload += b" " * (-len(payload) % 4)
    raw = struct.pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    for name in ("preview.glb", "character.glb"):
        (output / name).write_bytes(raw)
    child = {"id": "component-0123456789abcdef", "label": "Fox", "order": 1,
             "component": {"id": "fox", "parent_form_id": root["id"],
                           "source": {"blueprint": "Aki/Npc/Blueprint", "component": "Fox"}}}
    metadata = {"character_key": job.character_key, "game_version": job.game_version,
                "asset_version": "3.7.0", "tier": "hd"}
    bundles = [(root, tmp_path / "bundle", {}, {"model": root["model"]}),
               (child, output, {}, {"model": "Aki/Npc/Fox"})]
    archive = tmp_path / "linked.zip"
    write_archive(archive, metadata, bundles)
    with pytest.raises(ValueError, match="trusted authored"):
        model_uploads.decode_bundles(archive, tmp_path / "untrusted", job, [root])
    trusted = {child["id"]: {"component": child["component"].copy(), "model": "Aki/Npc/Fox"}}
    _, decoded = model_uploads.decode_bundles(archive, tmp_path / "linked", job, [root],
                                              expected_components=trusted)
    assert len(decoded) == 2 and decoded[1][2]["download"]["bones"] == 0
    child["component"]["source"] = {"blueprint": "Aki/Npc/Other", "component": "Fox"}
    wrong = tmp_path / "wrong-blueprint.zip"
    write_archive(wrong, metadata, bundles)
    with pytest.raises(ValueError, match="parent Blueprint"):
        model_uploads.decode_bundles(wrong, tmp_path / "wrong-blueprint", job, [root],
                                     expected_components={child["id"]: {"component": child["component"], "model": "Aki/Npc/Fox"}})

@pytest.mark.asyncio
async def test_trusted_discovery_prepares_matching_component(tmp_path, monkeypatch):
    import hashlib

    _, form, job = portable(tmp_path)
    form["component_blueprint"] = "Aki/Npc/Blueprint"
    client = tmp_path / "client"
    client.mkdir()
    (client / "plan.json").write_text(json.dumps({"id": "x", "version": "3.7.0", "tier": "hd"}))
    (client / "status.json").write_text(json.dumps({"id": "x", "state": "downloaded"}))
    monkeypatch.setattr(model_uploads, "inspect_installed_clients", lambda _: [
        {"path": str(client), "download_id": "x", "version": "3.7.0", "tier": "hd", "state": "downloaded"}])
    monkeypatch.setattr(model_uploads, "validate_plan", lambda _: None)
    monkeypatch.setattr(model_uploads, "tool_path", lambda _: tmp_path / "tool")
    extract = AsyncMock(return_value=(None, None, tmp_path, {}))
    monkeypatch.setattr(character_models, "extract_model", extract)
    relation = {"id": "Fox", "name": "Fox", "label": "Fox", "parent_component": "CharacterMesh0",
                "socket": "hand", "default_visible": True, "bindings": [], "cues": [],
                "source": {"blueprint": form["component_blueprint"], "component": "Fox"}}
    recipe = {"model": "Aki/Npc/Fox", "blueprints": [], "montages": [], "component": relation}
    child_relation = {**relation, "id": "Hat", "name": "Hat", "parent_component": "Fox", "socket": None,
                      "source": {"blueprint": form["component_blueprint"], "component": "Hat"}}
    nested = {**recipe, "model": "Aki/Npc/Hat", "component": child_relation}
    discover = AsyncMock(return_value=[nested, recipe])
    monkeypatch.setattr(character_models, "discover_scene_components", discover)
    expected = await model_uploads.discover_upload_components(tmp_path, [form], "3.7.0", "hd")
    identity = "component-" + hashlib.sha256((form["id"] + "|Fox").encode()).hexdigest()[:16]
    child_id = "component-" + hashlib.sha256((form["id"] + "|Hat").encode()).hexdigest()[:16]
    assert expected == {
        identity: {"model": recipe["model"], "component": {
            **relation, "baked": True, "parent_form_id": form["id"]}},
        child_id: {"model": nested["model"], "component": {
            **child_relation, "baked": False, "parent_form_id": identity}},
    }
    assert extract.await_args.kwargs["metadata_only"] is True
    discover.assert_awaited_once()


@pytest.mark.asyncio
async def test_linked_upload_requires_unambiguous_matching_client(tmp_path, monkeypatch):
    monkeypatch.setattr(model_uploads, "inspect_installed_clients", lambda _: [])
    with pytest.raises(ValueError, match="matching ready installed client"):
        await model_uploads.discover_upload_components(tmp_path, [], "3.7.0", "hd")

@pytest.mark.asyncio
async def test_process_linked_upload_uses_trusted_discovery(tmp_path, monkeypatch):
    _, form, job = portable(tmp_path)
    form["component_blueprint"] = "Aki/Npc/Blueprint"
    child = {"id": "component-0123456789abcdef", "label": "Fox", "order": 1,
             "component": {"id": "fox", "parent_form_id": form["id"],
                           "source": {"blueprint": form["component_blueprint"], "component": "Fox"}}}
    archive = tmp_path / "linked.zip"
    write_archive(archive, {"character_key": job.character_key, "game_version": job.game_version,
                            "asset_version": "3.7.0", "tier": "hd"},
                  [(form, tmp_path / "bundle", {}, {"model": form["model"]}),
                   (child, tmp_path / "bundle", {}, {"model": "Aki/Npc/Fox"})])
    job = job.model_copy(update={"archive_bytes": archive.stat().st_size})
    run = NS(status="queued", metadata_json={"payload": job.model_dump()})
    session = NS(get=AsyncMock(return_value=run))

    @asynccontextmanager
    async def context(*args):
        yield session

    async def get(key):
        yield archive.read_bytes()

    monkeypatch.setattr(model_uploads, "S3Storage", lambda _: NS(get=get, delete=AsyncMock()))
    monkeypatch.setattr(model_uploads, "engine", NS(connect=context))
    monkeypatch.setattr(model_uploads, "job_lock", context)
    monkeypatch.setattr(model_uploads, "SessionFactory", context)
    monkeypatch.setattr(model_uploads, "asset_workspace", lambda: tmp_path / "assets")
    monkeypatch.setattr(model_uploads, "update_admin_run", AsyncMock())
    discover = AsyncMock(return_value={child["id"]: {"component": child["component"], "model": "Aki/Npc/Fox"}})
    monkeypatch.setattr(model_uploads, "discover_upload_components", discover)
    monkeypatch.setattr(character_models, "job_forms", AsyncMock(return_value=[form]))
    publish = AsyncMock()
    monkeypatch.setattr(character_models, "publish_forms", publish)
    await model_uploads.process_model_upload(job.model_dump())
    assert discover.await_args.args[1:] == ([form], "3.7.0", "hd")
    assert len(publish.await_args.args[1]) == 2
    discover.return_value[child["id"]]["model"] = "Aki/Npc/Other"
    with pytest.raises(ValueError, match="trusted authored identity"):
        await model_uploads.process_model_upload(job.model_dump())
    assert publish.await_count == 1


def test_decode_nested_component_uses_root_blueprint_not_parent_mesh(tmp_path):
    _, root, job = portable(tmp_path)
    root = {**root, "component_blueprint": "Aki/Npc/Blueprint"}
    def component(identity, parent, source):
        return {"id": identity, "label": source, "order": 1, "component": {
            "id": source, "parent_form_id": parent,
            "source": {"blueprint": root["component_blueprint"], "component": source}}}
    first = component("component-0123456789abcdef", root["id"], "OtherCase1")
    nested = component("component-fedcba9876543210", first["id"], "Sword")
    metadata = {"character_key": job.character_key, "game_version": job.game_version,
                "asset_version": "3.7.0", "tier": "hd"}
    entries = [(root, tmp_path / "bundle", {}, {"model": root["model"]}),
               (first, tmp_path / "bundle", {}, {"model": "Aki/Prop/Case"}),
               (nested, tmp_path / "bundle", {}, {"model": "Aki/Prop/Sword"})]
    archive = tmp_path / "nested.zip"
    write_archive(archive, metadata, entries)
    trusted = {form["id"]: {"component": form["component"], "model": provenance["model"]}
               for form, _, _, provenance in entries[1:]}
    _, decoded = model_uploads.decode_bundles(archive, tmp_path / "nested", job, [root],
                                               expected_components=trusted)
    assert decoded[2][0]["component"]["parent_form_id"] == first["id"]
    nested["component"]["source"]["blueprint"] = "Aki/Npc/Other"
    wrong = tmp_path / "nested-wrong.zip"
    write_archive(wrong, metadata, entries)
    with pytest.raises(ValueError, match="parent Blueprint"):
        model_uploads.decode_bundles(wrong, tmp_path / "nested-wrong", job, [root],
                                     expected_components=trusted)
