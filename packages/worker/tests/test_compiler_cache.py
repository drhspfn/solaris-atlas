import json

from wuwa_story_worker.compiler_cache import compiled_cache_valid, compiler_fingerprint


def test_old_compiled_snapshot_is_rebuilt_when_compiler_changes(tmp_path):
    compiler = tmp_path / "compiler"
    compiler.mkdir()
    source = compiler / "items.py"
    source.write_text("items = ['items']")
    first = compiler_fingerprint(compiler)
    manifest, marker = tmp_path / "manifest.json", tmp_path / "compiled-code.json"
    manifest.write_text("{}")
    assert not compiled_cache_valid(manifest, marker, first)
    marker.write_text(json.dumps({"compiler_sha256": first}))
    assert compiled_cache_valid(manifest, marker, first)
    source.write_text("items = ['items', 'weapons']")
    second = compiler_fingerprint(compiler)
    assert second != first
    assert not compiled_cache_valid(manifest, marker, second)
