import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from wuwa_story_worker.riglyph_convert import convert_archive, convert_bundle


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


def test_multi_rig_bundle_preserves_components_and_does_not_invent_layer_order(tmp_path, monkeypatch):
    entries = {f'spine/{rig}/test.{extension}': rig.encode()
               for rig in ('background', 'character') for extension in ('skel', 'atlas')}
    manifest = {'skeletons': [{'file': f'spine/{rig}/test.skel', 'source': '/Game/' + rig}
                              for rig in ('background', 'character')],
                'files': {name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in entries.items()}}
    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        destination = Path(args[-1])
        destination.mkdir(parents=True)
        (destination / 'manifest.json').write_text('{}')
        (destination / 'skeleton.ir.json').write_text('{}')

    monkeypatch.setattr('wuwa_story_worker.riglyph_convert.subprocess.run', run)
    result = convert_bundle(source, tmp_path / 'converted.zip', ['riglyph'])
    with zipfile.ZipFile(result) as archive:
        output = json.loads(archive.read('manifest.json'))
        assert {component['source'] for component in output['components']} == {'/Game/background', '/Game/character'}
        assert output['composition_status'] == 'source_controls_required'
        assert all(component['manifest'] in archive.namelist() for component in output['components'])
    assert len(calls) == 2 and source.exists()
