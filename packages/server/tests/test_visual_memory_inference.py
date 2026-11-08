import io
import math

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
    monkeypatch.delenv("VISUAL_MEMORY_DINO_PATH", raising=False)
    monkeypatch.delenv("VISUAL_MEMORY_DETECTOR_PATH", raising=False)
    engine = VisualMemoryEngine()
    with pytest.raises(VisualMemoryUnavailable, match="VISUAL_MEMORY_DINO_PATH"):
        engine.embed(Image.new("RGB", (4, 4)))
    with pytest.raises(VisualMemoryUnavailable, match="VISUAL_MEMORY_DETECTOR_PATH"):
        engine.detect(Image.new("RGB", (4, 4)))
    assert get_engine() is get_engine()
