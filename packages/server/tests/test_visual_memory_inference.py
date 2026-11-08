import hashlib
import io
import json
import math
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from wuwa_story.visual_memory.images import crop_image, encode_png, image_hash, load_image, phash
from wuwa_story.visual_memory.inference import (
    VisualMemoryEngine,
    VisualMemoryUnavailable,
    get_engine,
)


def test_image_validation_and_crop():
    image = Image.new("RGB", (12, 8), "red")
    data = encode_png(image)
    assert load_image(data).size == (12, 8)
    assert len(image_hash(data)) == 64
    assert crop_image(image, [1.2, 2, 6.1, 7]).size == (6, 5)
    for box in ([0, 0, 13, 8], [1, 1, 1, 2], [0, 0, math.nan, 1], [True, 0, 1, 2]):
        with pytest.raises(ValueError):
            crop_image(image, box)
    for invalid in (b"not an image", b"x" * (8 * 1024 * 1024 + 1)):
        with pytest.raises(ValueError):
            load_image(invalid)


def test_pixel_limit():
    output = io.BytesIO()
    Image.new("RGB", (5000, 4001)).save(output, format="PNG")
    with pytest.raises(ValueError, match="pixels"):
        load_image(output.getvalue(), max_bytes=80 * 1024 * 1024)


def test_larger_limit_is_opt_in():
    data = encode_png(Image.new("RGB", (2, 2))) + bytes(8 * 1024 * 1024)
    with pytest.raises(ValueError, match="bytes"):
        load_image(data)
    assert load_image(data, max_bytes=80 * 1024 * 1024).size == (2, 2)
    with pytest.raises(ValueError, match="bytes"):
        load_image(data, max_bytes=len(data) - 1)
    assert crop_image(Image.new("RGB", (2, 2))).size == (2, 2)


def test_phash_is_stable_across_png_round_trip():
    image = Image.new("RGB", (12, 8), "red")
    image.paste("blue", (0, 0, 6, 4))
    value = phash(image)
    assert len(value) == 16
    assert int(value, 16) >= 0
    assert phash(load_image(encode_png(image))) == value


def test_missing_models_fail_explicitly(monkeypatch):
    monkeypatch.delenv("VISUAL_MEMORY_EMBEDDER", raising=False)
    monkeypatch.delenv("VISUAL_MEMORY_DINO_PATH", raising=False)
    monkeypatch.delenv("VISUAL_MEMORY_DETECTOR_PATH", raising=False)
    engine = VisualMemoryEngine()
    with pytest.raises(VisualMemoryUnavailable, match="VISUAL_MEMORY_DINO_PATH"):
        engine.embed(Image.new("RGB", (4, 4)))
    with pytest.raises(VisualMemoryUnavailable, match="VISUAL_MEMORY_DETECTOR_PATH"):
        engine.detect(Image.new("RGB", (4, 4)))
    assert get_engine() is get_engine()


def test_dinov2_fingerprint_is_unchanged_with_backend_override(monkeypatch, tmp_path):
    import PIL

    for name in ("model.safetensors", "config.json", "preprocessor_config.json"):
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setenv("VISUAL_MEMORY_DINO_PATH", str(tmp_path))
    monkeypatch.setenv("VISUAL_MEMORY_EMBEDDER", "ccip")
    model = SimpleNamespace(config=SimpleNamespace(
        model_type="dinov2", hidden_size=384))
    model.to = lambda _: model
    model.eval = lambda: model
    transformers = SimpleNamespace(
        __version__="test-transformers",
        AutoImageProcessor=SimpleNamespace(
            from_pretrained=lambda *a, **kw: object()),
        AutoModel=SimpleNamespace(from_pretrained=lambda *a, **kw: model),
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        __version__="test-torch", set_num_threads=lambda _: None))
    engine = VisualMemoryEngine(backend="dinov2")
    engine._load_embedder()

    def digest(name):
        return hashlib.sha256(name.encode()).hexdigest()
    config = {"dimensions": 384, "model": "facebook/dinov2-small", "pooling": "cls",
              "normalization": "l2", "weights_sha256": digest("model.safetensors"),
              "model_config_sha256": digest("config.json"),
              "preprocessor_sha256": digest("preprocessor_config.json"),
              "transformers_version": "test-transformers", "torch_version": "test-torch",
              "pillow_version": PIL.__version__}
    expected = hashlib.sha256(json.dumps(
        config, sort_keys=True).encode()).hexdigest()
    assert engine.backend == "dinov2" and engine._model_key(
    ) == "dinov2-small:cls:" + expected


@pytest.fixture
def ccip_runtime(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    model_path = tmp_path / "model_feat.onnx"
    model_path.write_bytes(b"fake local weights")
    sessions = []

    class Options:
        intra_op_num_threads = 0
        inter_op_num_threads = 0

    class Session:
        def __init__(self, path, sess_options, providers):
            self.path, self.options, self.providers = path, sess_options, providers
            self.inputs = [SimpleNamespace(
                name="image", shape=["batch", 3, 384, 384], type="tensor(float)")]
            self.outputs = [SimpleNamespace(
                name="feature", shape=["batch", 768], type="tensor(float)")]
            self.result = np.array([[3., 4.] + [0.] * 766], dtype=np.float32)
            self.feed = None
            sessions.append(self)

        def get_inputs(self):
            return self.inputs

        def get_outputs(self):
            return self.outputs

        def run(self, _outputs, feed):
            self.feed = feed
            return [self.result]

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        __version__="test-ort", SessionOptions=Options, InferenceSession=Session))
    monkeypatch.setenv("VISUAL_MEMORY_EMBEDDER", "ccip")
    monkeypatch.setenv("VISUAL_MEMORY_CCIP_PATH", str(tmp_path))
    monkeypatch.delenv("VISUAL_MEMORY_DINO_PATH", raising=False)
    return SimpleNamespace(np=np, path=model_path, sessions=sessions, session_type=Session)


def test_ccip_preprocessing_fingerprint_and_instance_isolation(ccip_runtime, monkeypatch):
    fixture = ccip_runtime
    np = fixture.np
    engine = VisualMemoryEngine()
    monkeypatch.setenv("VISUAL_MEMORY_EMBEDDER", "dinov2")
    monkeypatch.delenv("VISUAL_MEMORY_CCIP_PATH")
    assert engine.backend == "ccip" and VisualMemoryEngine().backend == "dinov2"
    with pytest.raises(AttributeError):
        engine.backend = "dinov2"
    image = Image.new("RGBA", (7, 5), (255, 128, 0, 200))
    result = engine.embed(image)
    session = fixture.sessions[0]
    assert session.providers == ["CPUExecutionProvider"]
    assert session.options.intra_op_num_threads == session.options.inter_op_num_threads == 1
    blob = session.feed["image"]
    assert blob.shape == (1, 3, 384, 384) and blob.dtype == np.float32
    expected = (np.array([1., 128 / 255, 0.], dtype=np.float32)
                - np.array([.48145466, .4578275, .40821073], dtype=np.float32)) / np.array(
                    [.26862954, .26130258, .27577711], dtype=np.float32)
    np.testing.assert_allclose(blob[0, :, 100, 100], expected, rtol=1e-6)
    assert len(result.vector) == 768 and result.vector[:2] == pytest.approx(
        [.6, .8])
    assert result.config["retrieval_min_similarity"] == .85
    assert result.config["retrieval_min_margin"] == .04
    assert result.model_key.startswith("ccip-caformer-24:onnx:")
    engine._detector_sha256 = "detector-sha"
    monkeypatch.setattr(engine, "_load_detector", lambda: None)
    assert engine.model_identity() == {
        "embedding": result.model_key, "detector": "detector-sha"}
    assert engine.embed(image).model_key == result.model_key and len(
        fixture.sessions) == 1
    fixture.path.write_bytes(b"different weights")
    monkeypatch.setenv("VISUAL_MEMORY_CCIP_PATH", str(fixture.path.parent))
    assert VisualMemoryEngine(backend="ccip").embed(
        image).model_key != result.model_key
    assert VisualMemoryEngine(backend="dinov2").backend == "dinov2"


def test_ccip_rejects_missing_invalid_models_and_embeddings(ccip_runtime, monkeypatch):
    fixture = ccip_runtime
    monkeypatch.delenv("VISUAL_MEMORY_CCIP_PATH")
    with pytest.raises(VisualMemoryUnavailable, match="VISUAL_MEMORY_CCIP_PATH"):
        VisualMemoryEngine(backend="ccip").embed(Image.new("RGB", (1, 1)))
    monkeypatch.setenv("VISUAL_MEMORY_CCIP_PATH", str(fixture.path.parent))
    fixture.path.unlink()
    with pytest.raises(VisualMemoryUnavailable, match="model_feat.onnx is missing"):
        VisualMemoryEngine().embed(Image.new("RGB", (1, 1)))
    fixture.path.write_bytes(b"fake local weights")
    original = fixture.session_type.get_outputs
    monkeypatch.setattr(fixture.session_type, "get_outputs", lambda _: [
        SimpleNamespace(shape=[1, 384], type="tensor(float)")])
    with pytest.raises(VisualMemoryUnavailable, match="Expected float32 CCIP"):
        VisualMemoryEngine().embed(Image.new("RGB", (1, 1)))
    monkeypatch.setattr(fixture.session_type, "get_outputs", original)
    engine = VisualMemoryEngine()
    engine._load_embedder()
    for result in (fixture.np.zeros((1, 768)), fixture.np.full((1, 768), fixture.np.nan),
                   fixture.np.zeros((1, 384))):
        engine._model.result = result
        with pytest.raises(VisualMemoryUnavailable, match="CCIP inference failed"):
            engine.embed(Image.new("RGB", (1, 1)))
    with pytest.raises(VisualMemoryUnavailable, match="must be dinov2 or ccip"):
        VisualMemoryEngine(backend="unsupported").embed(
            Image.new("RGB", (1, 1)))
