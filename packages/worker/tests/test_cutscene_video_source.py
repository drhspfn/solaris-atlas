import pytest

from wuwa_story_worker.cutscene_video_source import exported_video_path


@pytest.mark.parametrize("source,expected", [
    ("/Game/Aki/Movies/Storyboard/R0012.mp4", "Client/Content/Aki/Movies/Storyboard/R0012.mp4"),
    ("/Game/Aki/Movies/Storyboard/R0012.MP4", "Client/Content/Aki/Movies/Storyboard/R0012.MP4"),
    ("/Game/Aki/Sequence/Main/Scene.Scene", "Client/Content/Aki/Sequence/Main/Scene.uexp"),
])
def test_exported_source_preserves_authored_direct_or_wrapper_identity(source, expected):
    assert exported_video_path(source) == expected


@pytest.mark.parametrize("source", [
    "/Game/Aki/Movies/../Secret.mp4", "/Game/Aki/Movies//Scene.mp4",
    "/Game/Aki/Movies/Scene.mp4?token=x", "/Game/Aki/Movies/Scene.mp4/../../Secret.mp4",
    "/Game/Aki/Sequence/Scene.mp4", "/Game/Aki/Sequence/Scene.Other",
    "/Game/Aki/Sequence/Scene", "asset:ue:/Game/Aki/Movies/Scene.mp4",
    "https://example.com/Scene.mp4", "/Game/Aki/Movies/Scene\\Other.mp4",
])
def test_video_source_rejects_unsafe_paths_and_guessed_wrapper_identities(source):
    with pytest.raises(ValueError):
        exported_video_path(source)
