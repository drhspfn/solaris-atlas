import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

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
        return SimpleNamespace(stdout=json.dumps({'report': {'losses': [], 'unsupported': [], 'bytesConsumed': 8}}))

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
        return SimpleNamespace(stdout=json.dumps({'report': {'losses': [], 'unsupported': [],
                                                             'bytesConsumed': Path(args[2]).stat().st_size}}))

    monkeypatch.setattr('wuwa_story_worker.riglyph_convert.subprocess.run', run)
    result = convert_bundle(source, tmp_path / 'converted.zip', ['riglyph'])
    with zipfile.ZipFile(result) as archive:
        output = json.loads(archive.read('manifest.json'))
        assert {component['source'] for component in output['components']} == {'/Game/background', '/Game/character'}
        assert output['composition_status'] == 'source_controls_required'
        assert all(component['manifest'] in archive.namelist() for component in output['components'])
    assert len(calls) == 2 and source.exists()


@pytest.mark.parametrize('report', [
    {'losses': ['deform'], 'unsupported': [], 'bytesConsumed': 4},
    {'losses': [], 'unsupported': ['material'], 'bytesConsumed': 4},
    {'losses': [], 'unsupported': [], 'bytesConsumed': 3},
])
def test_failed_semantic_conversion_keeps_source_and_publishes_no_archive(tmp_path, monkeypatch, report):
    entries = {'rig/a.skel': b'rig!', 'rig/a.atlas': b'page'}
    manifest = {'skeletons': [{'file': 'rig/a.skel'}], 'files': {
        name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in entries.items()}}
    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)
    monkeypatch.setattr('wuwa_story_worker.riglyph_convert.subprocess.run',
                        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({'report': report})))
    output = tmp_path / 'converted.zip'
    with pytest.raises(ValueError):
        convert_bundle(source, output, ['riglyph'])
    assert source.exists() and not output.exists()

def test_atlas_page_resolves_verified_texture_with_different_casing(tmp_path, monkeypatch):
    entries = {'spine/a.skel': b'rig!',
               'spine/a.atlas': b'Portraits_Daniya_2.png\nsize: 1,1\n',
               'spine/Portraits_DaNiYa_2.png': b'verified image'}
    manifest = {'skeletons': [{'file': 'spine/a.skel'}], 'files': {
        name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in entries.items()}}
    bundle = tmp_path / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)
    original = bundle.read_bytes()

    def run(args, **kwargs):
        atlas = Path(args[args.index('--atlas') + 1])
        page = atlas.read_text().splitlines()[0]
        assert (atlas.parent / page).read_bytes() == b'verified image'
        out = Path(args[-1])
        out.mkdir()
        (out / 'manifest.json').write_text('{}')
        (out / 'skeleton.ir.json').write_text('{}')
        return SimpleNamespace(stdout=json.dumps({'report': {
            'losses': [], 'unsupported': [], 'bytesConsumed': 4}}))

    monkeypatch.setattr('wuwa_story_worker.riglyph_convert.subprocess.run', run)
    convert_archive(bundle, tmp_path / 'out', ['riglyph'])
    assert bundle.read_bytes() == original


def test_explicit_component_binding_can_select_a_shared_atlas(tmp_path, monkeypatch):
    entries = {'rig/a.skel': b'rig!', 'atlas/shared.atlas': b'page.png\nsize: 1,1\n',
               'atlas/page.png': b'image', 'other/unused.atlas': b'other'}
    manifest = {'skeletons': [{'file': 'rig/a.skel'}], 'files': {
        name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in entries.items()}}
    bundle = tmp_path / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)

    def run(args, **kwargs):
        atlas = Path(args[args.index('--atlas') + 1])
        assert atlas.as_posix().endswith('/atlas/shared.atlas')
        assert (atlas.parent / 'page.png').read_bytes() == b'image'
        output = Path(args[-1])
        output.mkdir()
        (output / 'manifest.json').write_text('{}')
        (output / 'skeleton.ir.json').write_text('{}')
        return SimpleNamespace(stdout=json.dumps({'report': {
            'losses': [], 'unsupported': [], 'bytesConsumed': 4}}))

    monkeypatch.setattr('wuwa_story_worker.riglyph_convert.subprocess.run', run)
    with pytest.raises(ValueError, match='one atlas'):
        convert_archive(bundle, tmp_path / 'ambiguous', ['riglyph'])
    with pytest.raises(ValueError, match='one atlas'):
        convert_archive(bundle, tmp_path / 'missing', ['riglyph'], atlas_file='missing.atlas')
    convert_archive(bundle, tmp_path / 'out', ['riglyph'], atlas_file='atlas/shared.atlas')


@pytest.mark.parametrize('page', ['../escape.png', '/absolute.png', r'bad\page.png'])
async def test_unsafe_atlas_page_is_rejected_before_cli(tmp_path, monkeypatch, page):
    entries = {'spine/a.skel': b'rig!', 'spine/a.atlas': (page+'\n').encode()}
    manifest = {'skeletons': [{'file': 'spine/a.skel'}], 'files': {
        name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in entries.items()}}
    bundle = tmp_path / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in entries.items():
            archive.writestr(name, data)
    monkeypatch.setattr('wuwa_story_worker.riglyph_convert.subprocess.run',
                        lambda *args, **kwargs: pytest.fail('unsafe page reached converter'))
    with pytest.raises(ValueError, match='Unsafe atlas page'):
        convert_archive(bundle, tmp_path / 'out', ['riglyph'])
