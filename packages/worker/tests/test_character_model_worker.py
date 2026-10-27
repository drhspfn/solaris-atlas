import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker.model_publication import ensure_glb_download, publish_viewer
from wuwa_story_worker.model_sources import (
    authored_package,
    glb_summary,
    model_sources,
    spring_profile,
)


def test_model_identity_comes_from_mesh_id_and_preload_not_portrait_names():
    role = {"MeshId": 23, "RolePortrait": "/Game/WrongCharacter.WrongCharacter"}
    preload = {
        "Id": 23,
        "Meshes": ["/Game/Aki/Character/Role/Female/Test/Model/Rig.Rig"],
        "AnimationBlueprints": ["/Game/Aki/Character/Role/Female/Test/ABP_PC.ABP_PC_C"],
    }
    assert model_sources(role, preload) == {
        "model": "Aki/Character/Role/Female/Test/Model/Rig",
        "blueprints": ["Aki/Character/Role/Female/Test/ABP_PC"],
    }
    with pytest.raises(ValueError, match="MeshId"):
        model_sources({"MeshId": 99}, preload)
    with pytest.raises(ValueError, match="composite"):
        model_sources(role, {**preload, "Meshes": preload["Meshes"] * 2})


@pytest.mark.parametrize(
    "path", ["/Game/Aki/../Bad.X", "/Game/Aki/X|Other.X", "/Engine/Test.X", "/Game/Aki//Bad.X"]
)
def test_untrusted_model_paths_are_rejected(path):
    with pytest.raises(ValueError):
        authored_package(path)


def test_spring_profile_preserves_source_groups_and_units():
    rows = [
        {
            "Name": "Default__PC",
            "Properties": {
                "Kawaii": {
                    "RootBones": [{"BoneName": "Chest"}],
                    "ExcludeBones": [],
                    "BoneForwardAxis": "EBoneForwardAxis::X_Positive",
                    "DummyBoneLength": 5,
                    "PhysicsSettings": {"Damping": 0.35, "Stiffness": 0.36, "LimitAngle": 15},
                }
            },
        }
    ]
    profile = spring_profile(rows, "Aki/PC", "abc")
    assert profile["groups"][0]["dummyLength"] == 0.05
    assert profile["groups"][0]["roots"] == ["Chest"]
    assert profile["groups"][0]["limitAngle"] == 15


def glb(path, **extra):
    data = json.dumps({"skins": [{"joints": [0]}], "meshes": [{}], **extra}).encode()
    data += b" " * (-len(data) % 4)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 20 + len(data))
        + struct.pack("<II", len(data), 0x4E4F534A)
        + data
    )


def test_glb_publication_rejects_embedded_animation_and_external_texture(tmp_path):
    path = tmp_path / "model.glb"
    glb(path)
    assert glb_summary(path)["bones"] == 1
    glb(path, animations=[{}])
    with pytest.raises(ValueError, match="animation"):
        glb_summary(path)
    glb(path, images=[{"uri": "http://external/texture.png"}])
    with pytest.raises(ValueError, match="embedded"):
        glb_summary(path)


@pytest.mark.asyncio
async def test_viewer_is_published_once_with_index_last(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "viewer.js").write_text("export {};")
    storage = SimpleNamespace(stat=AsyncMock(return_value=None), put_file=AsyncMock())
    key = await publish_viewer(storage, tmp_path)
    assert key.startswith("model-viewer/") and key.endswith("/index.html")
    assert storage.put_file.call_args_list[-1].args[1] == key
    storage.stat.return_value = object()
    storage.put_file.reset_mock()
    assert await publish_viewer(storage, tmp_path) == key
    storage.put_file.assert_not_called()


@pytest.mark.asyncio
async def test_download_metadata_keeps_cache_and_source_metadata_without_copying_bytes():
    class Client:
        def head_object(self, **kwargs):
            return {
                "CacheControl": "public, immutable",
                "ContentType": "model/gltf-binary",
                "Metadata": {"source": "retained"},
            }

        def copy_object(self, **kwargs):
            self.copy = kwargs

    client = Client()
    await ensure_glb_download(
        SimpleNamespace(client=client, bucket="public"), "objects/key", "a" * 64
    )
    assert client.copy["Key"] == client.copy["CopySource"]["Key"]
    assert client.copy["CacheControl"] == "public, immutable"
    assert client.copy["ContentDisposition"].endswith('.glb"')
    assert client.copy["Metadata"] == {"source": "retained"}
