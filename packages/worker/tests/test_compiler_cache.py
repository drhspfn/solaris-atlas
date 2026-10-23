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


def test_schema_changes_invalidate_cache_but_generated_bytecode_does_not(tmp_path):
    compiler = tmp_path / "compiler"
    compiler.mkdir()
    (compiler / "cli.py").write_text("pass", encoding="utf-8")
    schema = compiler / "schemas" / "baseline.json"
    schema.parent.mkdir()
    schema.write_text("{}", encoding="utf-8")
    before = compiler_fingerprint(compiler)
    schema.write_text('{"action_names":["new"]}', encoding="utf-8")
    after = compiler_fingerprint(compiler)
    assert before != after
    generated = compiler / "__pycache__"
    generated.mkdir()
    (generated / "cli.pyc").write_bytes(b"generated")
    assert compiler_fingerprint(compiler) == after


def test_compiler_fingerprint_uses_relative_paths_and_stable_order(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    for root, names in [(left, ["a.py", "b.json"]), (right, ["b.json", "a.py"])]:
        root.mkdir()
        for name in names:
            (root / name).write_text(name, encoding="utf-8")
    assert compiler_fingerprint(left) == compiler_fingerprint(right)
