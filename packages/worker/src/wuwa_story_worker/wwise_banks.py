"""Render authored Wwise events with the pinned WWiser/vgmstream toolchain.

WWiser preserves clips, fades, bus gains and switches in TXTP. In particular,
the male/female sources in a bank must never be mixed together indiscriminately.
"""

import asyncio
import hashlib
import math
import re
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from wuwa_story_worker.tooling import tool_path


def bank_media_ids(data: bytes) -> list[int]:
    """Read bounded v172 sound/music source tables, without choosing a branch."""
    return _bank_sources(data)[0]


def _bank_sources(data: bytes) -> tuple[list[int], dict[int, float]]:
    if len(data) < 12 or data[:4] != b"BKHD" or struct.unpack_from("<I", data, 8)[0] != 172:
        raise ValueError("Unsupported Wwise bank version")
    sources, position, silence_ids, effects = [], 0, set(), {}
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
                if kind == 17 and len(obj) >= 4:
                    identity = struct.unpack_from('<I', obj)[0]
                    if identity in effects:
                        raise ValueError('Duplicate Wwise source effect')
                    effects[identity] = obj
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
                    plugin = struct.unpack_from("<I", obj, location)[0]
                    if plugin == 0x00650002 and kind == 2:
                        # v172 Silence references an FxCustom, not a downloadable WEM.
                        if len(obj) < 26 or obj[8] != 0 or obj[13:26] != bytes(13):
                            raise ValueError('Unsupported Wwise Silence source data')
                        silence_ids.add(struct.unpack_from('<I', obj, 9)[0])
                        continue
                    if plugin != 0x00140001:
                        object_id = struct.unpack_from("<I", obj)[0]
                        raise ValueError(
                            f"Unsupported Wwise source codec 0x{plugin:08x} "
                            f"(HIRC type {kind}, object {object_id}, source offset {location})"
                        )
                    sources.append(struct.unpack_from("<I", obj, location + 5)[0])
                offset += 5 + length
            if offset != len(payload):
                raise ValueError("Unexpected Wwise hierarchy trailing bytes")
        position += 8 + size
    silences = {}
    for identity in silence_ids:
        effect = effects.get(identity, b'')
        if (len(effect) != 31 or struct.unpack_from('<II', effect, 4) != (0x00650002, 12)
                or effect[24:] != bytes(7)):
            raise ValueError('Unsupported Wwise Silence effect or modulation')
        duration, minus, plus = struct.unpack_from('<fff', effect, 12)
        if not math.isfinite(duration) or duration <= 0 or minus != 0 or plus != 0:
            raise ValueError('Unsupported Wwise Silence duration or randomization')
        silences[identity] = duration
    return sorted(set(sources)), silences


def _silence_recipe(body: str, bank: Path, xml: Path) -> str:
    """Correct the pinned WWiser's Silence duration and finite-loop rendering.

    Its int==set bug emits a default 1s; TXTP #B overrides #l rather than
    multiplying it. Only one fixed, unmodulated source is supported here.
    """
    _, silences = _bank_sources(bank.read_bytes())
    sounds = [node for node in ET.parse(xml).iter('object')
              if node.get('name') == 'CAkSound'
              and node.find(".//field[@name='ulPluginID'][@value='6619138']") is not None]
    if len(sounds) != 1 or len(silences) != 1:
        raise ValueError('Unsupported Wwise Silence source multiplicity')
    sound = sounds[0]
    source = sound.find(".//field[@name='sourceID']")
    if source is None or int(source.get('value')) not in silences:
        raise ValueError('Unresolved Wwise Silence source effect')
    for name in ('uNumCurves', 'ulNumStateProps', 'ulNumStateGroups', 'uNumFx'):
        controls = sound.findall(f".//field[@name='{name}']")
        if not controls or any(field.get('value') != '0' for field in controls):
            raise ValueError('Unsupported Wwise Silence modulation')
    bundles = sound.findall(".//object[@name='NodeInitialParams']/object")
    if len(bundles) != 2 or bundles[1].find("field[@name='cProps'][@value='0']") is None:
        raise ValueError('Unsupported Wwise Silence randomized properties')
    props = bundles[0].findall("list[@name='pProps']/object")
    if len(props) != 1 or props[0].find("field[@name='pID'][@value='74']") is None:
        raise ValueError('Unsupported Wwise Silence properties')
    loop_field = props[0].find("field[@name='pValue']")
    if loop_field is None:
        raise ValueError('Missing Wwise Silence loop count')
    loops = int(loop_field.get('value'))
    duration = silences[int(source.get('value'))]
    if loops <= 0 or not math.isfinite(duration * loops):
        raise ValueError('Unsupported Wwise Silence infinite loop')
    pattern = re.compile(r'(?m)^([ \t]*)\?\.plugin-silence #B ([0-9.]+) #e #l ([0-9]+)\.0[ \t]*$')
    matches = list(pattern.finditer(body))
    if (not matches or len(matches) != body.count('?.plugin-silence')
            or any(int(m[3]) != loops or float(m[2]) not in (1.0, duration) for m in matches)):
        raise ValueError('Unsupported Wwise Silence rendition')
    # TXTP integer times are samples, so retain a decimal point for seconds.
    return pattern.sub(lambda m: f'{m[1]}?.plugin-silence #B {duration * loops:.9f}', body)


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


def rover_parameters(xml: Path, rover: str | None, *, language: str | None = None) -> list[str]:
    """Bind authored Rover/language values, preserving every other selector."""
    values = {event_id("male"), event_id("female")}
    params = set()
    for node in ET.parse(xml).iter("object"):
        if node.get("name") != "CAkSwitchCntr":
            continue
        switches = {int(field.get("value")) for field in node.iter("field")
                    if field.get("name") == "ulSwitchID"}
        group = node.find(".//field[@name='ulGroupID']")
        kind = node.find(".//field[@name='eGroupType']")
        if group is not None and int(group.get('value')) == event_id('language'):
            if kind is None or kind.get('value') != '1':
                raise ValueError('Unsupported Wwise language State group')
            if language not in {'en', 'ja', 'ko', 'zh'}:
                raise ValueError('Language-dependent soundtrack requires an explicit audio language')
            if event_id(language) not in switches:
                raise ValueError(f'Requested audio language is absent from the authored State: {language}')
            params.add(f'({event_id("language")}={event_id(language)})')
            continue
        if not values.issubset(switches):
            continue
        if rover not in {"male", "female"}:
            raise ValueError("Gender-dependent soundtrack requires an authored Rover variant")
        if group is None or kind is None or kind.get("value") not in {"0", "1"}:
            raise ValueError("Unsupported Wwise gender switch")
        pair = f"{int(group.get('value'))}={event_id(rover)}"
        params.add(f"[{pair}]" if kind.get("value") == "0" else f"({pair})")
    return sorted(params)


async def render_bank(bank: Path, audio: Path, work: Path, decoder: Path, rover, run_tool, *, language: str | None = None):
    """Return (decoded WAV, durable evidence) for exactly one authored event."""
    from wuwa_story_worker.cutscene_import import soundtrack_wem

    renderer = tool_path("WUWA_WWISER_PATH")
    selection = str(rover).encode() + (b'\0language:' + language.encode('ascii') if language is not None else b'')
    digest = hashlib.sha256(bank.read_bytes() + selection + renderer.read_bytes()).hexdigest()[:20]
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
    params = await asyncio.to_thread(rover_parameters, xml, rover, language=language)
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
    original_recipe = None
    if '?.plugin-silence' in body:
        body = _silence_recipe(body, bank, xml)
        original_recipe = recipe.with_suffix('.wwiser-original')
        shutil.copyfile(recipe, original_recipe)
        recipe.write_text(body, encoding='utf-8')
    identities = sorted(set(map(int, re.findall(r"(?m)^\s*wem/(\d+)\.wem\b", body))))
    if not identities or any(line.strip() and not line.lstrip().startswith(("wem/", "group", "#", "commands"))
                             and not re.fullmatch(r'\?\.plugin-silence #B [0-9.]+', line.strip())
                             for line in body.splitlines()):
        raise ValueError("Unsupported Wwise rendition resource path")
    originals = [bank, *initializers, recipe, xml]
    if original_recipe is not None:
        originals.append(original_recipe)
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
