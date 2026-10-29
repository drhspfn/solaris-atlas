from types import SimpleNamespace as NS

import pytest

from wuwa_story_worker.model_bundle import build_animation_plan
from wuwa_story_worker.model_facial import FORMAT, facial_clip, validate_facial_pack


def dto(keys):
    return NS(metadata=NS(frames_per_second=60, num_frames=61), curves=[
        NS(name="e_close", keys=[NS(frame=frame, value=value) for frame, value in keys]),
        NS(name="gameplay_damage", keys=[NS(frame=0, value=100)]),
    ])


def test_facial_curves_match_authored_targets_and_keep_original_seconds():
    clip = facial_clip(dto([(0, 0), (30, 1), (60, 0)]), "Attack", {"E_Close"}, body_clip="Attack")
    assert clip == {"name": "Attack", "body_clip": "Attack", "seconds": 1.0, "tracks": [
        {"target": "E_Close", "keys": [[0, 0], [0.5, 1], [1, 0]]},
    ]}
    assert facial_clip(dto([(0, 1)]), "Attack", {"Other"}) is None
    assert facial_clip(dto([(0, 1)]), "Pose", {"E_Close"})["body_clip"] is None


@pytest.mark.parametrize("keys", [[(0, float("nan"))], [(-1, 0)], [(1, 0), (1, 1)], [(2, 0), (1, 1)]])
def test_invalid_facial_keys_fail_before_publication(keys):
    with pytest.raises(ValueError, match="facial curve"):
        facial_clip(dto(keys), "Attack", {"E_Close"})


def test_facial_pack_rejects_duplicate_names():
    clip = facial_clip(dto([(0, 1)]), "Pose", {"E_Close"})
    with pytest.raises(ValueError, match="unique"):
        validate_facial_pack({"format": FORMAT, "clips": [clip, clip]})


def test_constant_runs_are_compacted_without_changing_transition_times():
    clip = facial_clip(dto([(0, 0), (10, 0), (20, 0), (30, 1), (40, 1), (60, 1)]),
                       "Book", {"E_Close"})
    assert clip["tracks"][0]["keys"] == [[0, 0], [1 / 3, 0], [0.5, 1], [1, 1]]


def test_pack_rejects_ambiguous_body_mapping_and_nonobject_rows():
    clip = facial_clip(dto([(0, 1)]), "Book", {"E_Close"}, body_clip="Book")
    with pytest.raises(ValueError, match="Ambiguous"):
        validate_facial_pack({"format": FORMAT, "clips": [clip, {**clip, "name": "Duplicate"}]})
    with pytest.raises(ValueError, match="Invalid facial animation clip"):
        validate_facial_pack({"format": FORMAT, "clips": [1]})


def test_plan_keeps_synchronized_curves_and_independent_facial_poses(tmp_path):
    model = tmp_path / "model.uemodel"
    model.write_bytes(b"model")
    sources = {}
    for name in ("Book", "Smile"):
        (tmp_path / (name + ".ueanim")).write_bytes(name.encode())
        sources[name.lower()] = {"source": name + ".json", "matches_skeleton": True}

    def read(path, _):
        result = dto([(0, 0), (30, 1), (60, 0)])
        result.metadata.additive_anim_type = 1 if path.stem == "Smile" else 0
        result.tracks = [] if path.stem == "Smile" else [NS(name="root")]
        return NS(object_path=path.stem, object_name=path.stem), result

    plan = build_animation_plan(model, tmp_path, "rig", sources, {"root"}, read,
                                morph_names={"E_Close"})
    assert [clip["name"] for clip in plan["clips"]] == ["Book"]
    book, smile = plan["facial"]["clips"]
    assert book["body_clip"] == "Book" and smile["body_clip"] is None
    assert book["tracks"][0]["keys"][1] == [0.5, 1]
    assert len(plan["facial"]["clips"]) == 2
