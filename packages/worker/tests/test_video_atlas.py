import io

import pytest
from PIL import Image

from wuwa_story_worker.video_atlas import write_atlas


def jpeg(color="red", size=(640, 360)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "JPEG")
    return stream.getvalue()


def test_dense_atlas_keeps_order_timestamps_labels_and_letterboxes(tmp_path):
    path = tmp_path / "atlas.jpg"
    times = [i * 3.125 for i in range(12)]
    layout = write_atlas([jpeg() for _ in times], times, path)
    assert (layout["width"], layout["height"]) == (1536, 732)
    assert [t["time"] for t in layout["tiles"]] == times
    assert layout["tiles"][4] == {"time": 12.5, "row": 1, "column": 0,
                                  "rect": [0, 244, 384, 216]}
    with Image.open(path) as sheet:
        assert sheet.getpixel((192, 100))[0] > 240
        # Timestamp is drawn in the label strip below the actual video.
        assert max(sheet.crop((0, 216, 384, 244)).getextrema()[0]) > 200
    portrait = tmp_path / "portrait.jpg"
    write_atlas([jpeg("blue", (100, 400))], [0], portrait)
    with Image.open(portrait) as sheet:
        assert max(sheet.getpixel((10, 100))) < 30  # pillarbox, never crop a portrait
        assert sheet.getpixel((192, 100))[2] > 240


@pytest.mark.parametrize("times", [[], [1, 0], [0, 0], [float("nan")], [-1]])
def test_atlas_rejects_invalid_time_mapping(tmp_path, times):
    with pytest.raises(ValueError):
        write_atlas([jpeg()] * len(times), times, tmp_path / "bad.jpg")
