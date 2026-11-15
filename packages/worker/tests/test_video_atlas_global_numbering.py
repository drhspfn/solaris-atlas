"""Tests for global atlas/frame numbering in video_atlas and VideoWorkspace."""

import io
from pathlib import Path

import pytest
from PIL import Image
from wuwa_story.agents.cutscene_vision import dynamic_sampling_plan
from wuwa_story.agents.settings import AgentSettings

from wuwa_story_worker.video_analysis_agent import VideoWorkspace
from wuwa_story_worker.video_atlas import write_atlas


def _fake_jpeg(color: tuple[int, int, int] = (20, 40, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(buf, "JPEG")
    return buf.getvalue()


def test_write_atlas_assigns_global_atlas_and_frame_indices(tmp_path: Path):
    frames = [_fake_jpeg((10 * i, 20, 30)) for i in range(3)]
    out = tmp_path / "atlas_002.jpg"
    layout = write_atlas(
        frames,
        [12.0, 15.0, 18.0],
        out,
        atlas_index=2,
        frame_number_start=13,
    )
    assert out.is_file()
    assert layout["atlas_index"] == 2
    assert layout["frame_number_start"] == 13
    assert layout["frame_number_end"] == 15
    assert [t["global_frame_index"] for t in layout["tiles"]] == [13, 14, 15]
    assert all(t["atlas_index"] == 2 for t in layout["tiles"])


@pytest.mark.asyncio
async def test_video_workspace_maintains_global_numbering_across_resamples(monkeypatch, tmp_path: Path):
    settings = AgentSettings(_env_file=None, vision_atlas_frames=2)
    limits = dynamic_sampling_plan(30.0, settings)
    movie = tmp_path / "source.mp4"
    movie.write_bytes(b"fake")

    ws = VideoWorkspace(
        root=tmp_path,
        ffmpeg=Path("ffmpeg"),
        movie=movie,
        duration=30.0,
        settings=settings,
        limits=limits,
    )

    async def fake_extract(_self, timestamp: float, global_index: int) -> bytes:
        path = tmp_path / f"frame_{global_index:04d}.jpg"
        data = _fake_jpeg()
        path.write_bytes(data)
        ws.frame_paths[global_index] = path
        ws.frame_times[global_index] = timestamp
        return data

    monkeypatch.setattr(VideoWorkspace, "extract_frame", fake_extract)

    overview = await ws.build_atlases([0.0, 10.0, 20.0], reason="overview")
    assert len(overview) == 2
    assert overview[0].atlas_index == 1
    assert (overview[0].frame_number_start,
            overview[0].frame_number_end) == (1, 2)
    assert overview[1].atlas_index == 2
    assert (overview[1].frame_number_start,
            overview[1].frame_number_end) == (3, 3)

    zoom = await ws.build_atlases([10.5, 11.0], reason="zoom")
    assert len(zoom) == 1
    assert zoom[0].atlas_index == 3
    assert (zoom[0].frame_number_start, zoom[0].frame_number_end) == (4, 5)
    assert [entry.atlas_index for entry in ws.atlases] == [1, 2, 3]
