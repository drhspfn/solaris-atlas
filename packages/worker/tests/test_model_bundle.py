import json
from types import SimpleNamespace

from wuwa_story_worker.model_bundle import animation_sources, package_path, retime_tracks


def test_package_identity_ignores_object_suffix_and_preserves_path():
    assert package_path("/Game/Aki/Character/Test.Test") == package_path(
        "Client/Content/Aki/Character/Test.42"
    )
    assert package_path("Client\\Content\\Aki\\Character\\Test") == package_path(
        "/Game/Aki/Character/Test"
    )


def test_animation_catalog_uses_exact_skeleton_reference_not_folder_name(tmp_path):
    folder = tmp_path / "Client/Content/Aki/Shouanren"
    folder.mkdir(parents=True)
    for name, kind, skeleton in (
        ("Stand1", "AnimSequence", "/Game/Aki/Model/Shorekeeper_Skeleton"),
        ("Stand2", "AnimMontage", "Client/Content/Aki/Model/Shorekeeper_Skeleton.3"),
        ("Other", "AnimSequence", "/Game/Aki/Model/Other_Skeleton"),
        ("Mesh", "SkeletalMesh", "/Game/Aki/Model/Shorekeeper_Skeleton"),
    ):
        (folder / f"{name}.json").write_text(
            json.dumps(
                [
                    {
                        "Type": kind,
                        "Name": name,
                        "Properties": {"Skeleton": {"ObjectPath": skeleton}},
                    }
                ]
            ),
            encoding="utf-8",
        )
    rows = animation_sources(tmp_path, "/Game/Aki/Model/Shorekeeper_Skeleton")
    assert len(rows) == 3
    assert {row["name"] for row in rows.values() if row["matches_skeleton"]} == {"Stand1", "Stand2"}


def test_constant_tracks_retain_values_and_duration_without_single_key_baking():
    position = [SimpleNamespace(frame=0, value=(1, 2, 3))]
    rotation = [
        SimpleNamespace(frame=0, value=(0, 0, 0, 1)),
        SimpleNamespace(frame=60, value=(0, 0, 1, 0)),
    ]
    dto = SimpleNamespace(
        metadata=SimpleNamespace(num_frames=61, frames_per_second=60),
        tracks=[SimpleNamespace(position_keys=position, rotation_keys=rotation, scale_keys=[])],
    )
    retime_tracks(dto)
    assert [key.frame for key in position] == [0, 30]
    assert position[0].value == position[1].value == (1, 2, 3)
    assert position[0] is not position[1]
    assert [key.frame for key in rotation] == [0, 30]
    assert dto.tracks[0].scale_keys == []
