"""Source-independent contact sheets with explicit original-video coordinates."""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def write_atlas(frames: list[bytes], times: list[float], output: Path, *,
                tile_width: int = 384, quality: int = 85) -> dict:
    if (not frames or len(frames) != len(times) or len(frames) > 24
            or not 160 <= tile_width <= 640 or not 60 <= quality <= 95
            or any(not math.isfinite(t) or t < 0 for t in times)
            or times != sorted(set(times))):
        raise ValueError("Invalid bounded video atlas")
    columns = min(4, len(frames))
    image_height = tile_width * 9 // 16
    label_height = 28
    tile_height = image_height + label_height
    sheet = Image.new("RGB", (columns * tile_width,
                              math.ceil(len(frames) / columns) * tile_height), "#0b1112")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    tiles = []
    for index, (frame, seconds) in enumerate(zip(frames, times, strict=True)):
        x, y = (index % columns) * tile_width, (index // columns) * tile_height
        with Image.open(io.BytesIO(frame)) as image:
            image = ImageOps.contain(image.convert("RGB"), (tile_width, image_height))
            sheet.paste(image, (x + (tile_width - image.width) // 2,
                                y + (image_height - image.height) // 2))
        minutes, second = divmod(seconds, 60)
        label = f"{index + 1:02d}  {int(minutes):02d}:{second:06.3f}  ({seconds:.4f}s)"
        label_font = ImageFont.load_default(size=max(
            8, min(18, int(18 * (tile_width - 12) / draw.textlength(label, font=font)))))
        draw.text((x + 6, y + image_height + 3), label, font=label_font, fill="white")
        tiles.append({"time": seconds, "row": index // columns, "column": index % columns,
                      "rect": [x, y, tile_width, image_height]})
    sheet.save(output, "JPEG", quality=quality, optimize=True, subsampling=0)
    if output.stat().st_size > 8_000_000:
        raise ValueError("Video atlas exceeds bounded JPEG size")
    return {"width": sheet.width, "height": sheet.height, "columns": columns,
            "tile_width": tile_width, "label_height": label_height,
            "quality": quality, "tiles": tiles}
