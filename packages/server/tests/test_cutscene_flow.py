import pytest
from pydantic import ValidationError

from wuwa_story.ingestion.cutscenes import CutsceneRecipe, Option, PlaybackFlow

ASSET = "asset:ue:/Game/Movies/Test.Test"


def test_rover_identity_is_optional_and_validated():
    assert Option(label="Answer", next="clip").rover is None
    assert Option(label="A", next="clip", rover="female").rover == "female"
    with pytest.raises(ValidationError):
        Option(label="A", next="clip", rover="unknown")


def clip(key, next=None):
    return {"id": key, "kind": "clip", "asset": ASSET, "next": next}


def choice():
    return {
        "id": "choice",
        "kind": "choice",
        "prompt": "Choose",
        "options": [{"label": "A", "next": "a"}, {"label": "B", "next": "b"}],
    }


def test_shared_intro_branches_and_join():
    flow = PlaybackFlow.model_validate(
        {
            "entry": "intro",
            "evidence": "authored source",
            "nodes": [
                clip("intro", "choice"),
                choice(),
                clip("a", "join"),
                clip("b", "join"),
                clip("join"),
            ],
        }
    )
    assert len(flow.nodes) == 5
    assert flow.model_dump()["nodes"][2]["next"] == "join"


def test_single_video_requires_no_choice():
    assert (
        PlaybackFlow.model_validate(
            {"entry": "single", "evidence": "source", "nodes": [clip("single")]}
        ).entry
        == "single"
    )


@pytest.mark.parametrize(
    "nodes",
    [
        [clip("a", "missing")],
        [clip("a", "a")],
        [clip("a"), clip("unused")],
        [clip("a"), clip("a")],
        [{**clip("a"), "start": 5, "end": 2}],
        [{**clip("a"), "start": float("nan")}],
    ],
)
def test_invalid_graph_or_range_rejected(nodes):
    with pytest.raises(ValidationError):
        PlaybackFlow.model_validate({"entry": "a", "evidence": "source", "nodes": nodes})


def test_unpublished_clip_rejected():
    with pytest.raises(ValidationError):
        CutsceneRecipe.model_validate(
            {
                "cutscene": "cutscene:Test",
                "asset_version": "3.7.0",
                "videos": [{"asset": ASSET}],
                "flow": {
                    "entry": "a",
                    "evidence": "source",
                    "nodes": [{**clip("a"), "asset": "asset:ue:/Game/Other.Other"}],
                },
            }
        )
