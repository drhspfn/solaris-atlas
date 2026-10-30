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
