"""Bounded image decoding and crop helpers for visual memory."""

import hashlib
import io
import math
import warnings
from collections.abc import Sequence
from statistics import median

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "BMP")


def load_image(data: bytes, *, max_bytes: int = MAX_IMAGE_BYTES) -> Image.Image:
    if not data or len(data) > max_bytes:
        raise ValueError(f"Image must be between 1 and {max_bytes} bytes")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=FORMATS) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image exceeds 20 million pixels")
                image.seek(0)
                return ImageOps.exif_transpose(image).convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        SyntaxError,
    ) as exc:
        raise ValueError("Invalid or unsupported image") from exc


def crop_image(image: Image.Image, bbox: Sequence[float] | None = None) -> Image.Image:
    if bbox is None:
        return image.copy()
    if len(bbox) != 4 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox
    ):
        raise ValueError("Bounding box must contain four finite numbers")
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
        raise ValueError("Bounding box must be nonempty and within the image")
    return image.crop((math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)))


def encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def phash(image: Image.Image) -> str:
    """Return a 64-bit DCT perceptual hash as 16 hexadecimal characters."""
    pixels = image.convert("L").resize((32, 32), Image.Resampling.LANCZOS).tobytes()
    cosines = [[math.cos((2 * x + 1) * u * math.pi / 64) for x in range(32)] for u in range(8)]
    rows = [
        [sum(pixels[y * 32 + x] * cosines[u][x] for x in range(32)) for u in range(8)]
        for y in range(32)
    ]
    coefficients = [
        sum(rows[y][u] * cosines[v][y] for y in range(32)) for v in range(8) for u in range(8)
    ]
    threshold = median(coefficients[1:])
    value = 0
    for coefficient in coefficients:
        value = (value << 1) | int(coefficient > threshold)
    return f"{value:016x}"
