import json
import zipfile

import pytest

from wuwa_story.ingestion.model_archives import read_archive, write_archive


def bundle(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "preview.glb").write_bytes(b"compact")
    (output / "character.glb").write_bytes(b"full")
    (output / "keys.txt").write_text("must never leave the local machine")
    form = {"id": "mesh-1", "label": "Default", "order": 0, "model": "Aki/Model"}
    return form, output, {}, {"model": "Aki/Model"}


def test_portable_bundle_deduplicates_forms_and_omits_local_secrets(tmp_path):
    first = bundle(tmp_path)
    second = ({**first[0], "id": "mesh-2"}, *first[1:])
    path = tmp_path / "model.zip"
    manifest = write_archive(path, {"character_key": "character:1"}, [first, second])
    assert len(manifest["objects"]) == 2
    with zipfile.ZipFile(path) as archive:
        assert len(archive.namelist()) == 3
        assert "keys.txt" not in str(archive.namelist())
    assert read_archive(path, tmp_path / "decoded") == manifest
    with pytest.raises(FileExistsError):
        write_archive(path, {"character_key": "character:1"}, [first])


def test_npc_model_archive_round_trips(tmp_path):
    path = tmp_path / "npc.zip"
    manifest = write_archive(path, {"character_key": "character:npc:17288"}, [bundle(tmp_path)])
    assert read_archive(path, tmp_path / "decoded") == manifest


@pytest.mark.parametrize("mutation", ["traversal", "checksum", "duplicate"])
def test_archive_rejects_unsafe_or_corrupt_members(tmp_path, mutation):
    first = bundle(tmp_path)
    path = tmp_path / "model.zip"
    manifest = write_archive(path, {"character_key": "character:1"}, [first])
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if mutation == "traversal":
        entries["../outside"] = b"bad"
    elif mutation == "checksum":
        key = next(iter(manifest["objects"]))
        entries["objects/" + key] = b"X" * manifest["objects"][key]
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if mutation == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError):
        read_archive(path, tmp_path / "decoded")
    assert not (tmp_path / "outside").exists()


def component(form_id, parent, source_id, blueprint="Aki/Model"):
    return {"id": form_id, "label": source_id, "order": 1, "component": {
        "id": source_id, "parent_form_id": parent,
        "source": {"blueprint": blueprint, "component": source_id}}}


def test_component_graph_round_trip_deduplicates_shared_objects(tmp_path):
    root = bundle(tmp_path)
    child = component("component-0123456789abcdef", root[0]["id"], "fox")
    grandchild = component("component-fedcba9876543210", child["id"], "hat")
    path = tmp_path / "graph.zip"
    manifest = write_archive(path, {"character_key": "character:1"},
                             [root, (child, root[1], {}, {}), (grandchild, root[1], {}, {})])
    assert len(manifest["objects"]) == 2
    assert read_archive(path, tmp_path / "decoded") == manifest


@pytest.mark.parametrize("mutation", ["cycle", "missing", "duplicate"])
def test_component_graph_rejects_invalid_links(tmp_path, mutation):
    root = bundle(tmp_path)
    child = component("component-0123456789abcdef", root[0]["id"], "fox")
    other = component("component-fedcba9876543210", child["id"], "hat")
    if mutation == "cycle":
        child["component"]["parent_form_id"] = other["id"]
    elif mutation == "missing":
        child["component"]["parent_form_id"] = "mesh-9"
    else:
        other["component"]["id"] = "fox"
    with pytest.raises(ValueError):
        write_archive(tmp_path / "bad.zip", {"character_key": "character:1"},
                      [root, (child, root[1], {}, {}), (other, root[1], {}, {})])


@pytest.mark.parametrize("field,value", [
    ("default_visible", "false"), ("socket", 3), ("bindings", {}), ("cues", None),
    ("transform", {"location": {}, "rotation": []}), ("transform", None), ("name", ""),
])
def test_component_optional_metadata_must_have_contract_types(tmp_path, field, value):
    root = bundle(tmp_path)
    child = component("component-0123456789abcdef", root[0]["id"], "fox")
    child["component"][field] = value
    with pytest.raises(ValueError, match="component"):
        write_archive(tmp_path / "bad.zip", {"character_key": "character:1"},
                      [root, (child, root[1], {}, {})])


def test_scene_references_only_its_own_component_graph(tmp_path):
    root = bundle(tmp_path)
    child = component("component-0123456789abcdef", root[0]["id"], "fox")
    scene = {"format": "solaris-model-scene-v1", "components": [child["id"]], "cues": []}
    bundles = [(root[0], root[1], {}, {**root[3], "scene": scene}), (child, root[1], {}, {})]
    write_archive(tmp_path / "ok.zip", {"character_key": "character:1"}, bundles)
    scene["components"] = ["component-fedcba9876543210"]
    with pytest.raises(ValueError, match="missing component"):
        write_archive(tmp_path / "bad.zip", {"character_key": "character:1"}, bundles)
    scene["components"] = [child["id"], child["id"]]
    with pytest.raises(ValueError, match="scene"):
        write_archive(tmp_path / "duplicate.zip", {"character_key": "character:1"}, bundles)


def test_scene_cannot_reference_other_root_component(tmp_path):
    root = bundle(tmp_path)
    other = {**root[0], "id": "mesh-2"}
    child = component("component-0123456789abcdef", other["id"], "fox")
    scene = {"format": "solaris-model-scene-v1", "components": [child["id"]], "cues": []}
    with pytest.raises(ValueError, match="another root"):
        write_archive(tmp_path / "bad.zip", {"character_key": "character:1"}, [
            (root[0], root[1], {}, {**root[3], "scene": scene}),
            (other, root[1], {}, root[3]), (child, root[1], {}, {})])


def test_same_authored_component_can_be_reused_under_distinct_roots(tmp_path):
    root = bundle(tmp_path)
    other = {**root[0], "id": "mesh-2"}
    first = component("component-0123456789abcdef", root[0]["id"], "fox")
    second = component("component-fedcba9876543210", other["id"], "fox")
    path = tmp_path / "reused.zip"
    manifest = write_archive(path, {"character_key": "character:1"}, [
        root, (other, root[1], {}, root[3]),
        (first, root[1], {}, {}), (second, root[1], {}, {})])
    assert read_archive(path, tmp_path / "decoded") == manifest
