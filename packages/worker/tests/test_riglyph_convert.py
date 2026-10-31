import hashlib
import json
import zipfile

import pytest

from wuwa_story_worker.riglyph_convert import convert_archive


def test_converter_validates_bundle_before_running_cli(tmp_path, monkeypatch):
    entries = {"spine/a/test.skel": b"skeleton", "spine/b/test.atlas": b"atlas"}
    manifest = {"skeletons": [{"file": "spine/a/test.skel"}], "files": {
        name: {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in entries.items()}}
    bundle = tmp_path / "source.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        out = tmp_path / "out"
        out.mkdir()
        (out / "manifest.json").write_text("{}")
        (out / "skeleton.ir.json").write_text("{}")

    monkeypatch.setattr("wuwa_story_worker.riglyph_convert.subprocess.run", run)
    convert_archive(bundle, tmp_path / "out", ["riglyph"])
    assert calls[0][1] == "convert"
    assert bundle.exists()
    manifest["files"]["spine/a/test.skel"]["sha256"] = "bad"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="checksum"):
        convert_archive(bundle, tmp_path / "out2", ["riglyph"])
    assert len(calls) == 1
