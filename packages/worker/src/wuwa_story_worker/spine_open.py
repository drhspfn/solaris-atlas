"""Bounded 4.1 binary-to-open scene adapter; no official runtime dependency.

Sources: public data-format documentation and Spinal's independently authored
MIT 2022 prototype. See docs/spine-open-playback.md for provenance and limits.

MIT License

Copyright (c) 2022 gak

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import io
import json
import math
import struct
from pathlib import PurePosixPath


class SceneReader:
    def __init__(self, b):
        if not isinstance(b, bytes) or not 16 <= len(b) <= 16 * 1024 * 1024:
            raise ValueError("Invalid skeletal binary size")
        self.data = b
        self.offset = 0
        self.strings = []

    def raw(self, n):
        if n < 0 or self.offset + n > len(self.data):
            raise ValueError(("truncated", self.offset, n))
        v = self.data[self.offset : self.offset + n]
        self.offset += n
        return v

    def byte(self):
        return self.raw(1)[0]

    def varint(self):
        x = 0
        for i in range(5):
            b = self.byte()
            x |= (b & 127) << (7 * i)
            if b < 128:
                if x > 0xFFFFFFFF:
                    raise ValueError("Varint exceeds uint32")
                return x
        raise ValueError(("varint", self.offset))

    def count(self):
        n = self.varint()
        if n > 200000:
            raise ValueError(("count", self.offset, n))
        return range(n)

    def float32(self):
        n = struct.unpack(">f", self.raw(4))[0]
        if not math.isfinite(n):
            raise ValueError(("float", self.offset))
        return n

    def text(self):
        n = self.varint()
        return self.raw(n - 1).decode("utf8") if n > 1 else ""

    def string_ref(self):
        n = self.varint()
        if n > len(self.strings):
            raise ValueError("Invalid shared string index")
        return self.strings[n - 1] if n else ""

    def boolean(self):
        n = self.byte()
        if n > 1:
            raise ValueError(("bool", self.offset, n))
        return bool(n)

    def floats(self, n):
        if n < 0 or n > 2000000 or n * 4 > len(self.data) - self.offset:
            raise ValueError("Invalid floating-point array length")
        return [self.float32() for _ in range(n)]

    def indices(self):
        return [self.varint() for _ in self.count()]

    def color(self):
        return self.raw(4).hex()

    def sequence(self):
        if self.boolean():
            return [self.varint() for _ in range(4)]

    def vertices(self, n):
        if self.boolean():
            return [[(self.varint(), *self.floats(3)) for _ in self.count()] for _ in range(n)]
        return self.floats(n * 2)

    def attachment(self):
        name = self.string_ref()
        other = self.string_ref()
        k = self.byte()
        d = {"name": other or name, "type": k, "offset": self.offset}
        if k == 0:
            d.update(
                path=self.string_ref(),
                transform=self.floats(7),
                color=self.color(),
                sequence=self.sequence(),
            )
        elif k == 2:
            d.update(path=self.string_ref(), color=self.color())
            n = self.varint()
            d["uvs"] = self.floats(n * 2)
            d["triangles"] = [struct.unpack(">H", self.raw(2))[0] for _ in self.count()]
            d["vertices"] = self.vertices(n)
            d["hull"] = self.varint()
            d["sequence"] = self.sequence()
            if self.nonessential:
                d.update(
                    edges=[struct.unpack(">H", self.raw(2))[0] for _ in self.count()],
                    size=self.floats(2),
                )
        elif k == 1:
            d["vertices"] = self.vertices(self.varint())
            if self.nonessential:
                d["color"] = self.color()
        elif k == 6:
            d["end"] = self.varint()
            d["vertices"] = self.vertices(self.varint())
            if self.nonessential:
                d["color"] = self.color()
        else:
            raise ValueError(("attachment", k, self.offset))
        return name, d

    def skin(self, default):
        d = {"name": "default"}
        if not default:
            d["name"] = self.string_ref()
            d["bones"] = self.indices()
            d["ik"] = self.indices()
            d["transform"] = self.indices()
            d["path"] = self.indices()
        d["slots"] = {}
        for _ in self.count():
            slot = self.varint()
            d["slots"][slot] = dict(self.attachment() for _ in self.count())
        return d

    def curve(self, n):
        k = self.byte()
        if k not in (0, 1, 2):
            raise ValueError(("curve", k, self.offset))
        return self.floats(4 * n) if k == 2 else k

    def timeline(self, n, read=None):
        count = self.varint()
        _beziers = self.varint()
        read = read or (lambda: self.floats(n))
        frames = []
        if not 1 <= count <= 100000:
            raise ValueError("Invalid keyframe count")
        frames.append({"time": self.float32(), "values": read()})
        for _ in range(count - 1):
            frames.append({"time": self.float32(), "values": read()})
            frames[-2]["curve"] = self.curve(n)
        return frames

    def animation(self, d):
        a = {"name": self.text(), "timelines": []}
        declared = self.varint()
        types = {}
        for _ in self.count():
            slot = self.varint()
            for _ in self.count():
                k = self.byte()
                types["slot" + str(k)] = types.get("slot" + str(k), 0) + 1
                if k == 0:
                    frames = [
                        {"time": self.float32(), "name": self.string_ref()} for _ in self.count()
                    ]
                elif k == 1:
                    n = 4
                    frames = self.timeline(n, lambda n=n: [v / 255 for v in self.raw(n)])
                else:
                    raise ValueError(("slot timeline", k, self.offset))
                a["timelines"].append({"kind": "slot", "target": slot, "type": k, "frames": frames})
        for _ in self.count():
            bone = self.varint()
            for _ in self.count():
                k = self.byte()
                types["bone" + str(k)] = types.get("bone" + str(k), 0) + 1
                n = 2 if k in (1, 4, 7) else 1
                if k > 9:
                    raise ValueError(("bone timeline", k, self.offset))
                a["timelines"].append(
                    {"kind": "bone", "target": bone, "type": k, "frames": self.timeline(n)}
                )
        for kind in ("ik", "transform", "path"):
            n = self.varint()
            if n:
                raise ValueError((kind + " timelines", n, self.offset))
        for _ in self.count():
            skin = self.varint()
            for _ in self.count():
                slot = self.varint()
                for _ in self.count():
                    name = self.string_ref()
                    kind = self.byte()
                    count = self.varint()
                    _beziers = self.varint()
                    frames = []
                    if kind != 0:
                        raise ValueError("Unsupported attachment timeline")
                    if not 1 <= count <= 100000:
                        raise ValueError("Invalid deform frame count")
                    time = self.float32()
                    for i in range(count):
                        n = self.varint()
                        offset = self.varint() if n else 0
                        values = self.floats(n)
                        frames.append({"time": time, "values": values, "offset": offset})
                        if i < count - 1:
                            time = self.float32()
                            frames[-1]["curve"] = self.curve(1)
                    a["timelines"].append(
                        {
                            "kind": "deform",
                            "skin": skin,
                            "target": slot,
                            "name": name,
                            "frames": frames,
                        }
                    )
        if self.varint():
            raise ValueError("Draw-order animation is outside the verified open profile")
        n = self.varint()
        if n:
            raise ValueError(("event timelines", n, self.offset))
        if len(a["timelines"]) != declared:
            raise ValueError("Timeline inventory mismatch")
        return a

    def read(self):
        self.raw(8)
        d = {
            "format": "solaris.skeletal-scene.v1",
            "version": self.text(),
            "bounds": self.floats(4),
        }
        if not d["version"].startswith("4.1."):
            raise ValueError("Only verified 4.1 skeletal data is supported")
        self.nonessential = self.boolean()
        if self.nonessential:
            d["extra"] = [self.float32(), self.text(), self.text()]
        self.strings = [self.text() for _ in self.count()]
        d["strings"] = self.strings
        d["bones"] = []
        for i in self.count():
            b = {
                "name": self.text(),
                "parent": self.varint() if i else None,
                "transform": self.floats(8),
                "mode": self.varint(),
                "skin": self.boolean(),
            }
            if self.nonessential:
                b["color"] = self.color()
            if b["mode"] != 0 or b["skin"]:
                raise ValueError("Unsupported bone inheritance or skin bones")
            if b["parent"] is not None and b["parent"] >= i:
                raise ValueError("Bone parent must precede its child")
            d["bones"].append(b)
        d["slots"] = [
            {
                "name": self.text(),
                "bone": self.varint(),
                "color": self.color(),
                "dark": self.color(),
                "attachment": self.string_ref(),
                "blend": self.varint(),
            }
            for _ in self.count()
        ]
        d["ik"] = [
            {
                "name": self.text(),
                "order": self.varint(),
                "skin": self.boolean(),
                "bones": self.indices(),
                "target": self.varint(),
                "values": self.floats(2),
                "bend": self.byte(),
                "compress": self.boolean(),
                "stretch": self.boolean(),
                "uniform": self.boolean(),
            }
            for _ in self.count()
        ]
        d["transform"] = [
            {
                "name": self.text(),
                "order": self.varint(),
                "skin": self.boolean(),
                "bones": self.indices(),
                "target": self.varint(),
                "local": self.boolean(),
                "relative": self.boolean(),
                "values": self.floats(12),
            }
            for _ in self.count()
        ]
        n = self.varint()
        if n:
            raise ValueError(("paths", n, self.offset))
        d["skins"] = [self.skin(True)] + [self.skin(False) for _ in self.count()]
        d["events"] = self.varint()
        if d["events"]:
            raise ValueError(("events", self.offset))
        d["animations"] = [self.animation(d) for _ in self.count()]
        return d


def decode_scene(binary):
    reader = SceneReader(binary)
    scene = reader.read()
    if reader.offset != len(binary):
        raise ValueError(f"Unconsumed skeletal data at byte {reader.offset}")
    if scene.get("ik") or scene.get("transform"):
        raise ValueError("Constraint evaluation is outside the verified open profile")
    for slot in scene["slots"]:
        if slot["bone"] >= len(scene["bones"]) or slot["blend"] not in (0, 1, 2, 3):
            raise ValueError("Invalid slot binding or blend mode")
        if slot["dark"] != "ffffffff":
            raise ValueError("Two-color tint is outside the verified open profile")
    for skin in scene["skins"]:
        for slot, attachments in skin["slots"].items():
            if slot >= len(scene["slots"]):
                raise ValueError("Attachment slot is out of range")
            for attachment in attachments.values():
                if attachment["type"] != 2 or attachment.get("sequence"):
                    raise ValueError("Only non-sequence mesh attachments are verified")
                count = len(attachment["uvs"]) // 2
                if len(attachment["triangles"]) % 3 or any(
                    index >= count for index in attachment["triangles"]
                ):
                    raise ValueError("Invalid attachment triangle indices")
    del scene["strings"]
    return scene


def atlas_regions(data):
    """Parse documented modern text-atlas rectangles, retaining rotation/trim."""
    pages, regions, current = {}, {}, None
    new_page = True
    for raw in data.splitlines():
        value = raw.strip()
        if not value:
            new_page = True
            continue
        if ":" not in value:
            if new_page:
                if PurePosixPath(value).name != value or value in pages:
                    raise ValueError("Invalid or duplicate atlas page")
                page = value
                current = pages.setdefault(page, {})
                new_page = False
            else:
                if value in regions:
                    raise ValueError("Ambiguous atlas region name")
                current = regions.setdefault(value, {"page": page})
        else:
            if current is None:
                raise ValueError("Atlas property has no owner")
            key, _, raw_value = value.partition(":")
            if key in {"size", "bounds", "offsets"}:
                current[key] = [int(number.strip()) for number in raw_value.split(",")]
            elif key == "rotate":
                current[key] = {"true": 90, "false": 0}.get(
                    raw_value.strip(),
                    int(raw_value) if raw_value.strip().lstrip("-").isdigit() else -1,
                )
            elif key == "pma":
                current[key] = raw_value.strip() == "true"
    for region in regions.values():
        page = pages[region["page"]]
        if len(page.get("size", [])) != 2 or min(page["size"]) <= 0:
            raise ValueError("Atlas page dimensions are missing")
        if (
            len(region.get("bounds", [])) != 4
            or min(region["bounds"]) < 0
            or min(region["bounds"][2:]) <= 0
        ):
            raise ValueError("Invalid atlas rectangle")
        if region.get("rotate", 0) not in (0, 90, 180, 270):
            raise ValueError("Unsupported atlas rotation")
        x, y, width, height = region["bounds"]
        packed_width, packed_height = (
            (height, width) if region.get("rotate", 0) % 180 else (width, height)
        )
        if x + packed_width > page["size"][0] or y + packed_height > page["size"][1]:
            raise ValueError("Atlas rectangle exceeds its page")
        offsets = region.get("offsets", [0, 0, width, height])
        if (
            len(offsets) != 4
            or min(offsets) < 0
            or min(offsets[2:]) <= 0
            or offsets[0] + width > offsets[2]
            or offsets[1] + height > offsets[3]
        ):
            raise ValueError("Invalid atlas trim offsets")
        region["size"] = page["size"]
        region["pma"] = page.get("pma", False)
    return pages, regions


def masked_viewport(scene, entries, folder):
    """Recognize an authored four-sided opaque mask aperture, not an image-name guess."""
    from PIL import Image

    world, images, masks = [], {}, []
    animated_bones = {
        t["target"] for a in scene["animations"] for t in a["timelines"] if t["kind"] == "bone"
    }
    animated_slots = {
        t["target"]
        for a in scene["animations"]
        for t in a["timelines"]
        if t["kind"] in ("slot", "deform")
    }
    movable = []
    for index, bone in enumerate(scene["bones"]):
        rotation, x, y, sx, sy, shx, shy, _ = bone["transform"]
        ax, ay = math.radians(rotation + shx), math.radians(rotation + 90 + shy)
        local = [math.cos(ax) * sx, math.sin(ax) * sx, math.cos(ay) * sy, math.sin(ay) * sy, x, y]
        parent = bone["parent"]
        movable.append(index in animated_bones or parent is not None and movable[parent])
        if parent is not None:
            a, b, c, d, px, py = world[parent]
            aa, bb, cc, dd, xx, yy = local
            local = [
                a * aa + c * bb,
                b * aa + d * bb,
                a * cc + c * dd,
                b * cc + d * dd,
                a * xx + c * yy + px,
                b * xx + d * yy + py,
            ]
        world.append(local)
    try:
        for index, slot in enumerate(scene["slots"]):
            if (
                index in animated_slots
                or movable[slot["bone"]]
                or slot["blend"] != 0
                or slot["color"].lower() != "ffffffff"
            ):
                continue
            mesh = scene["skins"][0]["slots"].get(index, {}).get(slot["attachment"])
            if (
                not mesh
                or mesh["color"].lower() != "ffffffff"
                or not mesh["vertices"]
                or isinstance(mesh["vertices"][0], list)
                or mesh["hull"] != 4
            ):
                continue
            region = scene["regions"][mesh.get("path") or mesh["name"]]
            page = region["page"]
            if page not in images:
                with Image.open(io.BytesIO(entries[folder + page])) as image:
                    images[page] = image.convert("RGBA")
            x, y, w, h = region["bounds"]
            if region.get("rotate", 0) % 180:
                w, h = h, w
            extrema = images[page].crop((x, y, x + w, y + h)).getextrema()
            if any(channel != (0, 0) for channel in extrema[:3]) or extrema[3][0] < 254:
                continue
            a, b, c, d, px, py = world[slot["bone"]]
            points = [
                (a * x + c * y + px, b * x + d * y + py)
                for x, y in zip(mesh["vertices"][:8:2], mesh["vertices"][1:8:2], strict=True)
            ]
            xs, ys = zip(*points, strict=True)
            bounds = [min(xs), min(ys), max(xs), max(ys)]
            # Every hull corner must be a corner of the same axis-aligned rectangle.
            if all(
                min(abs(x - bounds[0]), abs(x - bounds[2])) < 0.1
                and min(abs(y - bounds[1]), abs(y - bounds[3])) < 0.1
                for x, y in points
            ):
                masks.append(bounds)
    finally:
        for image in images.values():
            image.close()
    if len(masks) != 4:
        return None
    x, y, w, h = scene["bounds"]
    cx, cy = x + w / 2, y + h / 2
    left = [r[2] for r in masks if r[2] < cx and r[1] < cy < r[3]]
    right = [r[0] for r in masks if r[0] > cx and r[1] < cy < r[3]]
    bottom = [r[3] for r in masks if r[3] < cy and r[0] < cx < r[2]]
    top = [r[1] for r in masks if r[1] > cy and r[0] < cx < r[2]]
    if (
        not all(len(side) == 1 for side in (left, right, bottom, top))
        or left[0] >= right[0]
        or bottom[0] >= top[0]
    ):
        return None
    return [left[0], bottom[0], right[0] - left[0], top[0] - bottom[0]]


def open_playback_entries(entries, resource, skeletons, dependencies):
    """Convert exact prefab-bound data; keep unsupported sources archived, never guess."""
    result, generated, failures = [], {}, []
    for variant in resource.get("variants", []):
        pending, reachable = [variant["prefab"]], set()
        while pending:
            source = pending.pop()
            if source in reachable:
                continue
            reachable.add(source)
            pending.extend(dependencies.get(source, []))
        matches = [skeleton for skeleton in skeletons if skeleton["source"] in reachable]
        try:
            if len(matches) != 1:
                raise ValueError("Prefab does not identify exactly one skeletal composition")
            skeleton = matches[0]
            folder = str(PurePosixPath(skeleton["file"]).parent) + "/"
            atlases = [
                path for path in entries if path.startswith(folder) and path.endswith(".atlas")
            ]
            if len(atlases) != 1:
                raise ValueError("Skeleton does not identify exactly one atlas")
            scene = decode_scene(entries[skeleton["file"]])
            pages, regions = atlas_regions(entries[atlases[0]].decode("utf-8"))
            for page in pages:
                if folder + page not in entries:
                    raise ValueError("An authored atlas page is missing")
            scene["regions"] = regions
            viewport = masked_viewport(scene, entries, folder)
            if viewport:
                scene["viewport"] = viewport
                scene["viewport_basis"] = "four_static_opaque_border_masks"
            for skin in scene["skins"]:
                for attachments in skin["slots"].values():
                    for attachment in attachments.values():
                        if (attachment.get("path") or attachment["name"]) not in regions:
                            raise ValueError("Mesh refers to an absent atlas region")
            animation = variant.get("animation")
            if not animation or animation not in {item["name"] for item in scene["animations"]}:
                raise ValueError("Authored animation is absent from the skeleton")
            path = folder + "open-scene.json"
            generated[path] = json.dumps(
                scene, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            result.append(
                {
                    "rover": "female"
                    if variant["rover"] is None
                    and any(v["rover"] == "male" for v in resource["variants"])
                    else variant["rover"],
                    "animation": animation,
                    "scene": path,
                    "textures": {page: folder + page for page in pages},
                }
            )
        except (ValueError, UnicodeError, KeyError, IndexError, OSError) as error:
            failures.append({"prefab": variant["prefab"], "reason": str(error)[:500]})
    return generated, {
        "format": "solaris.skeletal-scene.v1",
        "variants": result,
        "unsupported": failures,
    }
