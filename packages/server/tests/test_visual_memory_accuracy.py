import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from wuwa_story.visual_memory.service import identification_status
from wuwa_story.visual_memory.video import VideoReviewService


def test_ccip_candidate_policy_abstains_on_weak_or_close_matches():
    def row(score):
        return {"score": score, "best_similarity": score}

    assert identification_status([row(.844), row(.827)], .85, .04) == "unknown"
    assert identification_status([row(.9), row(.88)], .85, .04) == "unresolved"
    assert identification_status([row(.92), row(.804)], .85, .04) == "candidate"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_weights", [False, True])
async def test_pending_scan_keeps_its_original_backend_and_fingerprint(monkeypatch, changed_weights):
    from wuwa_story.visual_memory import inference

    class Engine:
        def __init__(self, backend=None):
            self.backend = backend or "ccip"

        def model_identity(self):
            return {"embedding": "dinov2-small:cls:" + ("changed" if changed_weights else "original"),
                    "detector": "unchanged-detector"}

    monkeypatch.setattr(inference, "VisualMemoryEngine", Engine)
    service = VideoReviewService(None, storage=object(), engine=Engine())
    expected = {"embedding": "dinov2-small:cls:original", "detector": "unchanged-detector"}
    service._run = AsyncMock(side_effect=[
        SimpleNamespace(status="pending", metadata_json={"models": expected}),
        SimpleNamespace(status="succeeded"),
    ])
    service.get_scan = AsyncMock(return_value={"status": "succeeded"})
    if changed_weights:
        with pytest.raises(ValueError, match="Configured inference models changed"):
            await service.process(1)
        service.get_scan.assert_not_awaited()
    else:
        assert await service.process(1) == {"status": "succeeded"}
    assert service.memory.engine.backend == "dinov2"


@pytest.mark.asyncio
async def test_model_readiness_checks_selected_backend_without_dino_weights(monkeypatch, tmp_path):
    from wuwa_story.api.routes import visual_memory

    monkeypatch.setenv("VISUAL_MEMORY_EMBEDDER", "ccip")
    monkeypatch.setenv("VISUAL_MEMORY_CCIP_PATH", str(tmp_path))
    monkeypatch.delenv("VISUAL_MEMORY_DINO_PATH", raising=False)
    monkeypatch.setattr(visual_memory.importlib.util, "find_spec", lambda _: object())
    assert (await visual_memory.models())["embedding"]["available"] is False
    (tmp_path / "model_feat.onnx").write_bytes(b"unverified model")
    result = (await visual_memory.models())["embedding"]
    assert result["backend"] == "ccip" and result["available"] is None
    monkeypatch.setenv("VISUAL_MEMORY_EMBEDDER", "typo")
    assert (await visual_memory.models())["embedding"]["available"] is False


def test_evaluation_excludes_video_siblings_and_counts_absent_identities():
    script = Path(__file__).resolve().parents[3] / "scripts" / "benchmark-visual-retrieval.py"
    spec = importlib.util.spec_from_file_location("visual_benchmark", script)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    refs = [
        {"id": 1, "source_group": "same-video", "links": [{"identity_id": 1}]},
        {"id": 2, "source_group": "same-video", "links": [{"identity_id": 1}]},
        {"id": 3, "source_group": "another-video", "links": [{"identity_id": 2}]},
    ]
    vectors = np.array([[1., 0.], [1., 0.], [0., 1.]])
    result = benchmark.evaluate(vectors, refs, [])
    assert result["evaluated"] == 0
    assert result["unrepresented_after_source_exclusion"] == 3
    assert result["absent_identity_queries"] == 3
    assert result["absent_identity_false_candidates"] == 0
