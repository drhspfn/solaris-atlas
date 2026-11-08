import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("isolated_worker", Path(__file__).parents[1] / "start-illustrated-worker.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def test_dry_run_reads_identity_without_starting_or_pulling(monkeypatch, tmp_path):
    calls = []
    def docker(*args):
        calls.append(args)
        assert args == ("inspect", "source")
        return json.dumps([{"Id": "source-id", "Image": "old-image", "State": {"Running": True},
                            "NetworkSettings": {"Networks": {"app": {}}}}])
    monkeypatch.setattr(script, "docker", docker)
    args = SimpleNamespace(name="illustrated", source_container="source", image="example/worker:" + "a" * 40,
                           apply=False, receipt=tmp_path / "receipt.json")
    script.main(args)
    assert len(calls) == 1
    report = json.loads(args.receipt.read_text())
    assert report["apply"] is False
    assert report["queue"] == "wuwa.illustrated-backgrounds.v1"
    assert report["source_container_id"] == "source-id"


@pytest.mark.parametrize("networks", [{}, {"one": {}, "two": {}}])
def test_ambiguous_networks_block_startup(monkeypatch, tmp_path, networks):
    monkeypatch.setattr(script, "docker", lambda *args: json.dumps([{
        "Id": "source-id", "Image": "old-image", "State": {"Running": True},
        "NetworkSettings": {"Networks": networks}}]))
    with pytest.raises(ValueError, match="one explicitly inherited worker network"):
        script.main(SimpleNamespace(name="worker", source_container="source", image="worker:" + "a" * 40,
                                   apply=True, receipt=tmp_path / "receipt.json"))
    assert not (tmp_path / "receipt.json").exists()
