"""Read bounded tagged property streams used by LGUI v3 prefab components.

The prefab carries its own compact uint16 FName and object-reference tables.
Unknown values remain tagged bytes; they are not interpreted as render controls.
"""

import math
import struct


def properties(data, names, assets, *, depth=0):
    result, consumed = property_prefix(data, names, assets, depth=depth)
    if any(data[consumed:]) or len(data) - consumed > 4:
        raise ValueError('Unexpected LGUI property trailer')
    return result


def property_prefix(data, names, assets, *, depth=0):
    """Return a tagged struct and its extent when embedded in a native map."""
    if depth > 12 or not isinstance(data, (list, bytes)) or len(data) > 4 * 1024**2:
        raise ValueError('Invalid LGUI property stream bounds')
    data, offset, result = bytes(data), 0, {}

    def take(size):
        nonlocal offset
        if size < 0 or offset + size > len(data):
            raise ValueError('Truncated LGUI property stream')
        value = data[offset:offset + size]
        offset += size
        return value

    def name():
        index, = struct.unpack('<H', take(2))
        if index >= len(names) or not isinstance(names[index], str):
            raise ValueError('Invalid LGUI FName reference')
        return names[index]

    while offset < len(data):
        key = name()
        if key == 'None':
            return result, offset
        kind = name()
        size, index = struct.unpack('<ii', take(8))
        if not 0 <= size <= len(data) or index != 0 or key in result:
            raise ValueError('Invalid or duplicate LGUI property tag')
        extra = None
        if kind == 'StructProperty':
            extra = name()
            take(16)  # Native struct GUID.
        elif kind in {'EnumProperty', 'ByteProperty', 'ArrayProperty', 'SetProperty'}:
            extra = name()
        elif kind == 'MapProperty':
            extra = [name(), name()]
        elif kind == 'BoolProperty':
            value = take(1)[0]
            if value not in (0, 1):
                raise ValueError('Invalid LGUI boolean')
            extra = bool(value)
        guid = take(1)[0]
        if guid not in (0, 1):
            raise ValueError('Invalid LGUI property GUID flag')
        if guid:
            take(16)
        raw = take(size)
        value = {'type': kind, 'extra': extra, 'raw': raw.hex()}
        if kind == 'StructProperty' and extra == 'UIWidget':
            value = properties(raw, names, assets, depth=depth + 1)
        elif kind == 'StructProperty' and extra in {'Vector', 'Vector2D'}:
            count = 3 if extra == 'Vector' else 2
            if size != count * 4:
                raise ValueError('Unsupported LGUI vector precision')
            value = list(struct.unpack('<' + 'f' * count, raw))
        elif kind == 'StructProperty' and extra == 'Color' and size == 4:
            b, g, r, a = raw
            value = [r, g, b, a]
        elif kind in {'FloatProperty', 'IntProperty'} and size == 4:
            value, = struct.unpack('<f' if kind == 'FloatProperty' else '<i', raw)
        elif kind == 'BoolProperty' and size == 0:
            value = extra
        elif kind == 'EnumProperty' and size == 2:
            enum, = struct.unpack('<H', raw)
            if enum >= len(names):
                raise ValueError('Invalid LGUI enum value')
            value = names[enum]
        elif kind == 'StrProperty' and size >= 4:
            length, = struct.unpack_from('<i', raw)
            if not length or 4 + abs(length) * (2 if length < 0 else 1) != size:
                raise ValueError('Invalid LGUI string size')
            value = raw[4:].decode('utf-16le' if length < 0 else 'utf-8').rstrip('\0')
        elif kind == 'ObjectProperty' and size == 3 and raw[0] == 2:
            reference, = struct.unpack_from('<H', raw, 1)
            if reference >= len(assets):
                raise ValueError('Invalid LGUI asset reference')
            value = assets[reference]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Non-finite LGUI property')
        if isinstance(value, list) and any(isinstance(v, float) and not math.isfinite(v) for v in value):
            raise ValueError('Non-finite LGUI vector')
        result[key] = value
    raise ValueError('LGUI property stream has no terminator')
