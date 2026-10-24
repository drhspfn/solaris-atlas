"""Render authored Wwise events with the pinned WWiser/vgmstream toolchain.

WWiser preserves clips, fades, bus gains and switches in TXTP. In particular,
the male/female sources in a bank must never be mixed together indiscriminately.
"""

import asyncio
import hashlib
import re
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from wuwa_story_worker.tooling import tool_path


def bank_media_ids(data: bytes) -> list[int]:
    """Read bounded v172 sound/music source tables, without choosing a branch."""
    if len(data) < 12 or data[:4] != b"BKHD" or struct.unpack_from("<I", data, 8)[0] != 172:
        raise ValueError("Unsupported Wwise bank version")
    sources, position = [], 0
    while position < len(data):
        if position + 8 > len(data):
            raise ValueError("Truncated Wwise chunk header")
        size = struct.unpack_from("<I", data, position + 4)[0]
        payload = data[position + 8:position + 8 + size]
        if len(payload) != size:
            raise ValueError("Truncated Wwise bank")
        if data[position:position + 4] == b"HIRC":
            if len(payload) < 4:
                raise ValueError("Truncated Wwise hierarchy")
            offset = 4
            for _ in range(struct.unpack_from("<I", payload)[0]):
                if offset + 5 > len(payload):
                    raise ValueError("Truncated Wwise object header")
                kind, length = payload[offset], struct.unpack_from("<I", payload, offset + 1)[0]
                obj = payload[offset + 5:offset + 5 + length]
                if len(obj) != length:
                    raise ValueError("Truncated Wwise object")
                if kind == 2:
                    locations = [4]
                elif kind == 11:
                    if len(obj) < 8:
                        raise ValueError("Truncated Wwise music source")
                    count = struct.unpack_from("<I", obj, 4)[0]
                    if count > (len(obj) - 8) // 18:
                        raise ValueError("Truncated Wwise music sources")
                    locations = [8 + i * 18 for i in range(count)]
                else:
                    locations = []
                for location in locations:
                    if len(obj) < location + 9:
                        raise ValueError("Truncated Wwise source")
                    if struct.unpack_from("<I", obj, location)[0] != 0x00140001:
                        raise ValueError("Unsupported Wwise source codec")
                    sources.append(struct.unpack_from("<I", obj, location + 5)[0])
                offset += 5 + length
            if offset != len(payload):
                raise ValueError("Unexpected Wwise hierarchy trailing bytes")
        position += 8 + size
    return sorted(set(sources))


def event_id(name: str) -> int:
    value = 2166136261
    for byte in name.lower().encode("ascii"):
        value = ((value * 16777619) & 0xffffffff) ^ byte
    return value


def has_authored_event(data: bytes, name: str) -> bool:
    if not data.startswith(b"BKHD"):
        return False
    bank_media_ids(data)  # Validate all chunk and hierarchy bounds before inspecting IDs.
    position = 0
    while position < len(data):
        size = struct.unpack_from("<I", data, position + 4)[0]
        if data[position:position + 4] == b"HIRC":
            offset, end = position + 12, position + 8 + size
            while offset < end:
                length = struct.unpack_from("<I", data, offset + 1)[0]
                if data[offset] == 4 and length >= 4 and struct.unpack_from("<I", data, offset + 5)[0] == event_id(name):
                    return True
                offset += 5 + length
        position += 8 + size
    return False


def rover_parameters(xml: Path, rover: str | None) -> list[str]:
    """Select only verified male/female switches, preserving other ambiguity."""
    values = {event_id("male"), event_id("female")}
    params = set()
    for node in ET.parse(xml).iter("object"):
        if node.get("name") != "CAkSwitchCntr":
            continue
        switches = {int(field.get("value")) for field in node.iter("field")
                    if field.get("name") == "ulSwitchID"}
        if not values.issubset(switches):
            continue
        if rover not in {"male", "female"}:
            raise ValueError("Gender-dependent soundtrack requires an authored Rover variant")
        group = node.find(".//field[@name='ulGroupID']")
        kind = node.find(".//field[@name='eGroupType']")
        if group is None or kind is None or kind.get("value") not in {"0", "1"}:
            raise ValueError("Unsupported Wwise gender switch")
        pair = f"{int(group.get('value'))}={event_id(rover)}"
        params.add(f"[{pair}]" if kind.get("value") == "0" else f"({pair})")
    return sorted(params)


async def render_bank(bank: Path, audio: Path, work: Path, decoder: Path, rover, run_tool):
    """Return (decoded WAV, durable evidence) for exactly one authored event."""
    from wuwa_story_worker.cutscene_import import soundtrack_wem

    renderer = tool_path("WUWA_WWISER_PATH")
    digest = hashlib.sha256(bank.read_bytes() + str(rover).encode() + renderer.read_bytes()).hexdigest()[:20]
    root = work / "wwise" / digest
    root.mkdir(parents=True, exist_ok=True)
    source = root / bank.name
    shutil.copyfile(bank, source)
    # The base Init bank carries the shared bus gains; it is exported separately
    # because localized voice packages do not contain it.
    initializers = sorted(path for path in audio.rglob("Init.bnk") if path.is_file())
    if len(initializers) > 1:
        raise ValueError("Ambiguous Wwise Init bank")
    inputs = [source]
    for initializer in initializers:
        local = root / "Init.bnk"
        shutil.copyfile(initializer, local)
        inputs.append(local)
    xml = root / "bank.xml"
    await run_tool([sys.executable, str(renderer), "-d", "xml", "-dn", str(root / "bank"),
                    "--", str(source)], 120)
    params = await asyncio.to_thread(rover_parameters, xml, rover)
    for stale in root.glob("*.txtp"):
        stale.unlink()
    command = [sys.executable, str(renderer), "-d", "none", "-g", "-gwd", "-gra",
               "-go", str(root), "-gw", "wem", "-gf", str(event_id(bank.stem))]
    if params:
        command += ["-gp", *params]
    result = await run_tool([*command, "--", *map(str, inputs)], 120)
    report = (getattr(result, "stderr", b"") or b"").decode(errors="replace")
    (root / "renderer.log").write_text(report, encoding="utf-8")
    if "missing" in report.casefold() or "error" in report.casefold():
        detail = next(line for line in report.splitlines() if "missing" in line.casefold() or "error" in line.casefold())
        raise ValueError(f"Wwise event has unresolved dependencies: {detail[:220]}")
    outputs = list(root.glob("*.txtp"))
    if len(outputs) != 1:
        raise ValueError(f"Wwise event requires an explicit non-gender selection ({len(outputs)} renditions)")
    recipe = outputs[0]
    body = recipe.read_text(encoding="utf-8-sig").split("# AUTOGENERATED", 1)[0]
    identities = sorted(set(map(int, re.findall(r"(?m)^\s*wem/(\d+)\.wem\b", body))))
    if not identities or any(line.strip() and not line.lstrip().startswith(("wem/", "group", "#", "commands"))
                             for line in body.splitlines()):
        raise ValueError("Unsupported Wwise rendition resource path")
    originals = [bank, *initializers, recipe, xml]
    media = root / "wem"
    media.mkdir(exist_ok=True)
    for identity in identities:
        original = soundtrack_wem(audio, identity)
        target = media / f"{identity}.wem"
        if not target.exists():
            target.hardlink_to(original)
        originals.append(original)
    output = root / "rendered.wav"
    await run_tool([str(decoder.resolve()), "-i", "-o", str(output), str(recipe)], 300)
    if not output.is_file() or output.stat().st_size <= 44:
        raise ValueError("Empty rendered Wwise soundtrack")
    return output, originals
