import json
import struct
from pathlib import Path

from wuwa_story_worker.model_gltf_variants import (
    merge_glb_material_variants,
    parse_glb,
    write_glb,
)
from wuwa_story_worker.model_uploads import _canonical_authored_forms


def _make_glb(path: Path, mat_name: str, img_bytes: bytes) -> None:
    bin_data = bytearray(img_bytes)
    while len(bin_data) % 4:
        bin_data.append(0)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_data)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(img_bytes)}],
        "images": [{"bufferView": 0, "mimeType": "image/png"}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987}],
        "textures": [{"source": 0, "sampler": 0}],
        "materials": [
            {
                "name": mat_name,
                "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
            }
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
    }
    write_glb(path, gltf, bin_data)


def test_merge_glb_material_variants_merges_and_dedups(tmp_path: Path):
    base = tmp_path / "base.glb"
    var2 = tmp_path / "var2.glb"
    var3 = tmp_path / "var3.glb"
    _make_glb(base, "Mat01", b"IMG_ONE_BYTES")
    _make_glb(var2, "Mat02", b"IMG_TWO_BYTES")
    _make_glb(var3, "Mat01", b"IMG_ONE_BYTES")

    merge_glb_material_variants(
        base, [("Form 2", var2), ("Form 3", var3)], base_name="Form 1")
    merged, merged_bin = parse_glb(base)

    assert "KHR_materials_variants" in merged["extensionsUsed"]
    assert merged["extensions"]["KHR_materials_variants"]["variants"] == [
        {"name": "Form 1"},
        {"name": "Form 2"},
        {"name": "Form 3"},
    ]
    assert len(merged["materials"]) == 2
    assert len(merged["images"]) == 2
    assert len(merged_bin) % 4 == 0
    mappings = merged["meshes"][0]["primitives"][0]["extensions"]["KHR_materials_variants"]["mappings"]
    assert mappings == [
        {"material": 0, "variants": [0, 2]},
        {"material": 1, "variants": [1]},
    ]


def test_canonical_authored_forms_normalizes_variants_and_synthesized_forms():
    authored = [{"id": "mesh-1", "label": "Default",
                 "order": 0, "model": "A/B/C"}]
    collapsed = [
        {**authored[0], "material_variants": [{"id": "form-1", "label": "Form 1"}]}]
    expanded = [
        authored[0],
        {
            "id": "mesh-1-form-2",
            "label": "Default · Form 2",
            "order": 1,
            "model": "A/B/C",
            "part_material_overrides": {"A/B/C": {"0": "Mat02"}},
            "source": {"material_controller": "DA_Test"},
        },
    ]
    assert _canonical_authored_forms(
        collapsed) == _canonical_authored_forms(authored)
    assert _canonical_authored_forms(
        expanded) == _canonical_authored_forms(authored)
