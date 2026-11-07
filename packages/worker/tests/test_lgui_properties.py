import struct

import pytest

from wuwa_story_worker.lgui_properties import properties


def test_native_compact_prefab_widget_and_spine_asset_references():
    names = ['width', 'FloatProperty', 'None', 'SkeletonData', 'ObjectProperty']
    # LGUI v3: uint16 compact names, int32 size/index, optional property GUID.
    width = bytes([0, 0, 1, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 32, 69])
    reference = struct.pack('<HHiiB', 3, 4, 3, 0, 0) + bytes([2, 0, 0])
    asset = {'ObjectPath': '/Game/Scene/Rig'}
    assert properties(width + reference + bytes([2, 0]), names, [asset]) == {
        'width': 2560, 'SkeletonData': asset,
    }


@pytest.mark.parametrize('data', [
    b'\xff\xff',  # Unknown compact name.
    struct.pack('<HHiiB', 0, 1, 4, 0, 0),  # Missing value.
    struct.pack('<HHiiBf', 0, 1, 4, 0, 0, float('nan')) + b'\x02\x00',
    b'\x02\x00\x01',  # Nonzero trailing bytes must not be silently ignored.
])
def test_invalid_or_truncated_prefab_controls_are_not_accepted(data):
    with pytest.raises(ValueError):
        properties(data, ['width', 'FloatProperty', 'None'], [])


def test_duplicate_property_and_invalid_asset_reference_are_rejected():
    tag = struct.pack('<HHiiB', 0, 1, 3, 0, 0)
    names = ['SkeletonData', 'ObjectProperty', 'None']
    with pytest.raises(ValueError, match='asset reference'):
        properties(tag + bytes([2, 1, 0]) + b'\x02\x00', names, [{}])
    with pytest.raises(ValueError, match='duplicate'):
        properties((tag + bytes([2, 0, 0])) * 2 + b'\x02\x00', names, [{}])
