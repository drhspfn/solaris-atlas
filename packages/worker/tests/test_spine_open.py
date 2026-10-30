"""Self-authored neutral-format fixtures; no proprietary assets or runtime oracle."""

import copy
import io
import json
import struct

import pytest
from PIL import Image

from wuwa_story_worker.spine_open import (
    SceneReader,
    atlas_regions,
    decode_scene,
    masked_viewport,
    open_playback_entries,
)


def test_viewport_requires_four_static_opaque_border_rectangles():
    image = io.BytesIO()
    Image.new('RGBA', (1, 1), (0, 0, 0, 255)).save(image, format='PNG')
    rectangles = [(-10, -10, -5, 10), (5, -10, 10, 10),
                  (-10, -10, 10, -3), (-10, 3, 10, 10)]
    scene = {'bounds': [-10, -10, 20, 20], 'animations': [],
             'bones': [{'parent': None, 'transform': [0, 0, 0, 1, 1, 0, 0, 0]}],
             'slots': [{'bone': 0, 'blend': 0, 'color': 'ffffffff', 'attachment': str(i)}
                       for i in range(4)],
             'skins': [{'slots': {i: {str(i): {'name': 'black', 'color': 'ffffffff',
                        'hull': 4, 'vertices': [x, y, x, y2, x2, y2, x2, y]}}
                        for i, (x, y, x2, y2) in enumerate(rectangles)}}],
             'regions': {'black': {'page': 'p.png', 'bounds': [0, 0, 1, 1]}}}
    entries = {'p.png': image.getvalue()}
    assert masked_viewport(scene, entries, '') == pytest.approx([-5, -3, 10, 6])
    scene['animations'] = [{'timelines': [{'kind': 'slot', 'target': 0}]}]
    assert masked_viewport(scene, entries, '') is None
    scene['animations'] = []
    scene['slots'][0]['blend'] = 1
    assert masked_viewport(scene, entries, '') is None


def uint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def text(value):
    encoded = value.encode("utf-8")
    return uint(len(encoded) + 1) + encoded


def floats(*values):
    return struct.pack(">" + "f" * len(values), *values)


def empty_animation(name="idle", *, draw_order=0, events=0):
    # Inventory, slot groups, bone groups, IK, transform, path, deform groups.
    return text(name) + b"\0" * 7 + uint(draw_order) + uint(events)


def binary_scene(*, version="4.1.23", bounds=(0, 0, 32, 16),
                 transform=(0, 0, 0, 1, 1, 0, 0, 0), strings=(), slots=(),
                 skin=b"\0", animations=()):
    # Hash, length-prefixed version, bounds, nonessential flag, shared strings.
    header = b"\0" * 8 + text(version) + floats(*bounds) + b"\0"
    header += uint(len(strings)) + b"".join(text(value) for value in strings)
    # One root: there is no parent field for the first bone.
    root = uint(1) + text("root") + floats(*transform) + uint(0) + b"\0"
    # Slots, three constraint inventories, default skin, extra skins, events.
    body = uint(len(slots)) + b"".join(slots) + b"\0" * 3 + skin + b"\0\0"
    return header + root + body + uint(len(animations)) + b"".join(animations)


def slot(*, attachment=0, bone=0, blend=0):
    return text("face") + uint(bone) + b"\xff" * 8 + uint(attachment) + uint(blend)


def mesh_scene():
    # Name ref, optional override, mesh type, path ref, RGBA, vertex count.
    attachment = uint(1) + uint(0) + b"\x02" + uint(1) + bytes.fromhex("80402080") + uint(3)
    attachment += floats(0, 0, 1, 0, 0, 1)
    attachment += uint(3) + struct.pack(">HHH", 0, 1, 2)
    attachment += b"\0" + floats(0, 0, 8, 0, 0, 12) + uint(3) + b"\0"
    # One populated slot, slot index zero, one attachment.
    skin = uint(1) + uint(0) + uint(1) + attachment
    return binary_scene(strings=["face"], slots=[slot(attachment=1)], skin=skin,
                        animations=[empty_animation()])


ATLAS = """page.png
size: 64, 32
pma: true
face
  rotate: true
  bounds: 4, 6, 8, 12
  offsets: 2, 3, 20, 24
"""


def playback_fixture():
    entries = {
        "model/face.skel": mesh_scene(),
        "model/face.atlas": ATLAS.encode(),
        "model/page.png": b"synthetic texture placeholder, never decoded",
        "unrelated/archive.bin": b"preserve this byte for byte",
    }
    resource = {"variants": [{"prefab": "prefab", "animation": "idle", "rover": None}]}
    skeletons = [{"source": "skeleton-source", "file": "model/face.skel"}]
    dependencies = {"prefab": ["intermediate"], "intermediate": ["skeleton-source", "prefab"]}
    return entries, resource, skeletons, dependencies


def test_minimal_binary_decodes_exactly_to_eof():
    binary = binary_scene()
    reader = SceneReader(binary)
    reader.read()
    assert reader.offset == len(binary)
    scene = decode_scene(binary)
    assert scene["format"] == "solaris.skeletal-scene.v1"
    assert scene["version"] == "4.1.23"
    assert scene["bounds"] == [0, 0, 32, 16]
    assert scene["bones"] == [{"name": "root", "parent": None,
                               "transform": [0, 0, 0, 1, 1, 0, 0, 0],
                               "mode": 0, "skin": False}]
    assert scene["slots"] == []
    assert scene["skins"] == [{"name": "default", "slots": {}}]
    assert scene["animations"] == []
    assert "strings" not in scene


def test_every_truncated_prefix_is_rejected():
    for binary in (binary_scene(animations=[empty_animation()]), mesh_scene()):
        for length in range(len(binary)):
            with pytest.raises(ValueError):
                decode_scene(binary[:length])


def test_synthetic_mesh_decodes_vertices_triangles_tint_and_shared_path():
    scene = decode_scene(mesh_scene())
    attachment = scene["skins"][0]["slots"][0]["face"]
    assert attachment["type"] == 2
    assert attachment["name"] == attachment["path"] == "face"
    assert attachment["vertices"] == [0, 0, 8, 0, 0, 12]
    assert attachment["uvs"] == [0, 0, 1, 0, 0, 1]
    assert attachment["triangles"] == [0, 1, 2]
    assert attachment["hull"] == 3
    assert attachment["color"] == "80402080"
    assert attachment["sequence"] is None


@pytest.mark.parametrize("suffix", [b"\0", b"extra bytes"])
def test_trailing_data_is_not_silently_ignored(suffix):
    with pytest.raises(ValueError, match="Unconsumed skeletal data"):
        decode_scene(binary_scene() + suffix)


@pytest.mark.parametrize("version", ["3.8.99", "4.0.64", "4.2.0", "5.0.0", ""])
def test_unsupported_versions_are_rejected(version):
    with pytest.raises(ValueError, match="4.1"):
        decode_scene(binary_scene(version=version))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("field", ["bounds", "transform"])
def test_nonfinite_floats_are_rejected(value, field):
    values = [0, 0, 32, 16] if field == "bounds" else [0, 0, 0, 1, 1, 0, 0, 0]
    # Exercise every component, including scales, shears and length.
    for i in range(len(values)):
        changed = values.copy()
        changed[i] = value
        with pytest.raises(ValueError, match="float"):
            decode_scene(binary_scene(**{field: changed}))


@pytest.mark.parametrize("encoded", [b"\x80" * 5 + b"\0", b"\xff\xff\xff\xff\x10"])
def test_overlong_or_overflowing_varints_are_rejected(encoded):
    reader = SceneReader(encoded + b"\0" * 16)
    with pytest.raises(ValueError, match="[Vv]arint"):
        reader.varint()
    # The same guard protects a varint encountered inside a full binary header.
    with pytest.raises(ValueError, match="[Vv]arint"):
        decode_scene(b"\0" * 8 + encoded + b"\0" * 64)


def test_largest_uint32_varint_is_read_without_signed_wraparound():
    reader = SceneReader(uint(0xFFFFFFFF) + b"\0" * 16)
    assert reader.varint() == 0xFFFFFFFF
    assert reader.offset == 5


def test_shared_string_references_are_one_based_with_zero_empty():
    scene = decode_scene(binary_scene(strings=["face"], slots=[slot(attachment=1)]))
    assert scene["slots"][0]["attachment"] == "face"
    assert decode_scene(binary_scene(slots=[slot()]))["slots"][0]["attachment"] == ""


@pytest.mark.parametrize("strings,reference", [((), 1), (("face",), 2), (("face",), 200000)])
def test_out_of_range_shared_string_references_are_rejected(strings, reference):
    with pytest.raises(ValueError, match="shared string index"):
        decode_scene(binary_scene(strings=strings, slots=[slot(attachment=reference)]))


@pytest.mark.parametrize("kwargs", [{"bone": 1}, {"blend": 4}])
def test_invalid_slot_bindings_and_blend_are_rejected(kwargs):
    with pytest.raises(ValueError, match="slot binding or blend"):
        decode_scene(binary_scene(slots=[slot(**kwargs)]))


@pytest.mark.parametrize("rotation,expected", [("false", 0), ("true", 90), ("0", 0), ("90", 90), ("180", 180), ("270", 270)])
def test_atlas_rotation_trim_and_page_metadata_are_retained(rotation, expected):
    pages, regions = atlas_regions(ATLAS.replace("rotate: true", f"rotate: {rotation}"))
    assert pages["page.png"]["size"] == [64, 32]
    assert regions["face"] == {"page": "page.png", "size": [64, 32], "pma": True,
                               "rotate": expected, "bounds": [4, 6, 8, 12],
                               "offsets": [2, 3, 20, 24]}


@pytest.mark.parametrize("old,new", [
    ("size: 64, 32", "size: 0, 32"),
    ("size: 64, 32", "size: 64"),
    ("bounds: 4, 6, 8, 12", "bounds: -1, 6, 8, 12"),
    ("bounds: 4, 6, 8, 12", "bounds: 4, 6, 0, 12"),
    ("bounds: 4, 6, 8, 12", "bounds: 4, 6, 8"),
    ("rotate: true", "rotate: 45"),
    ("page.png", "../page.png"),
])
def test_atlas_rejects_malformed_dimensions_rotation_and_page_paths(old, new):
    with pytest.raises(ValueError):
        atlas_regions(ATLAS.replace(old, new))


def test_atlas_duplicate_regions_and_pages_are_ambiguous():
    with pytest.raises(ValueError, match="Ambiguous atlas region"):
        atlas_regions(ATLAS + "face\n  bounds: 0, 0, 1, 1\n")
    with pytest.raises(ValueError, match="duplicate atlas page"):
        atlas_regions(ATLAS + "\n" + ATLAS)


@pytest.mark.parametrize("bounds", ["60, 0, 8, 12", "0, 30, 8, 12"])
def test_atlas_rectangles_must_fit_inside_the_page(bounds):
    # Regression specification: positive dimensions alone do not prove containment.
    data = ATLAS.replace("rotate: true", "rotate: false").replace("4, 6, 8, 12", bounds)
    with pytest.raises(ValueError):
        atlas_regions(data)


@pytest.mark.parametrize("offsets", ["-1, 3, 20, 24", "2, 3, 0, 24", "2, 3, 4, 4", "2, 3, 20"])
def test_atlas_trim_must_describe_a_valid_original_rectangle(offsets):
    # Use no rotation to make trimmed/original dimensions unambiguous.
    data = ATLAS.replace("rotate: true", "rotate: false").replace("2, 3, 20, 24", offsets)
    with pytest.raises(ValueError):
        atlas_regions(data)


def test_open_playback_follows_exact_transitive_binding_and_preserves_archive():
    args = playback_fixture()
    before = copy.deepcopy(args)
    generated, metadata = open_playback_entries(*args)
    assert args == before
    assert set(generated) == {"model/open-scene.json"}
    scene = json.loads(generated["model/open-scene.json"])
    assert scene["animations"] == [{"name": "idle", "timelines": []}]
    assert scene["regions"]["face"]["page"] == "page.png"
    assert metadata["unsupported"] == []
    assert metadata["variants"] == [{"rover": None, "animation": "idle",
                                      "scene": "model/open-scene.json",
                                      "textures": {"page.png": "model/page.png"}}]


@pytest.mark.parametrize("failure", ["no_binding", "ambiguous_binding", "ambiguous_atlas", "missing_page", "missing_region", "missing_animation", "unsupported_version", "truncated", "draw_order", "events"])
def test_unsupported_or_ambiguous_playback_preserves_every_archive_entry(failure):
    entries, resource, skeletons, dependencies = playback_fixture()
    if failure == "no_binding":
        dependencies.clear()
    elif failure == "ambiguous_binding":
        skeletons.append({"source": "second-source", "file": "other/face.skel"})
        dependencies["prefab"].append("second-source")
    elif failure == "ambiguous_atlas":
        entries["model/second.atlas"] = entries["model/face.atlas"]
    elif failure == "missing_page":
        del entries["model/page.png"]
    elif failure == "missing_region":
        entries["model/face.atlas"] = ATLAS.replace("\nface\n", "\nother\n").encode()
    elif failure == "missing_animation":
        resource["variants"][0]["animation"] = "not-authored"
    elif failure == "unsupported_version":
        entries["model/face.skel"] = binary_scene(version="4.2.0")
    elif failure == "truncated":
        entries["model/face.skel"] = entries["model/face.skel"][:-1]
    elif failure == "draw_order":
        entries["model/face.skel"] = binary_scene(animations=[empty_animation(draw_order=1)])
    elif failure == "events":
        entries["model/face.skel"] = binary_scene(animations=[empty_animation(events=1)])
    args = (entries, resource, skeletons, dependencies)
    before = copy.deepcopy(args)
    generated, metadata = open_playback_entries(*args)
    assert args == before
    assert generated == {}
    assert metadata["variants"] == []
    assert len(metadata["unsupported"]) == 1
    assert metadata["unsupported"][0]["prefab"] == "prefab"
    assert metadata["unsupported"][0]["reason"]


@pytest.mark.parametrize("kwargs,reason", [({"draw_order": 1}, "Draw-order"), ({"events": 1}, "event timelines")])
def test_unsupported_animation_sections_are_explicit_errors(kwargs, reason):
    with pytest.raises(ValueError, match=reason):
        decode_scene(binary_scene(animations=[empty_animation(**kwargs)]))
