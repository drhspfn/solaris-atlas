import json

from wuwa_story_worker.model_bundle import animation_sources, package_path


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
