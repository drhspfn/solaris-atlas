"""Build cutscene recipes from current game VideoData/VideoSound configuration."""

import argparse
import json
import math
import re
import sqlite3
import struct
from pathlib import Path

from flatbuffers.table import Table
from wuwa_story.ingestion.cutscenes import CutsceneRecipe
from wuwa_story.version import MIN_SUPPORTED_CLIENT_VERSION, is_supported_cutscene_version

from wuwa_story_worker.cutscene_caption_audio import caption_soundtracks


def engine_path(blob: bytes) -> str:
    paths = re.findall(rb"/Game/[A-Za-z0-9_./-]+\.[A-Za-z0-9_]+", blob)
    if len(paths) != 1:
        raise ValueError("Expected one authored Unreal asset path")
    return paths[0].decode("ascii")


def sound_timing(blob: bytes) -> tuple[float, float | None]:
    """Verified 3.7 VideoSound fields: StartMoment/EndMoment are seconds."""
    table = Table(blob, struct.unpack_from("<I", blob)[0])
    start_offset, end_offset = table.Offset(12), table.Offset(14)
    start = struct.unpack_from("<f", blob, table.Pos + start_offset)[0] if start_offset else 0
    end = struct.unpack_from("<f", blob, table.Pos + end_offset)[0] if end_offset else -1
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("Non-finite source soundtrack timing")
    return max(0, start), None if end < 0 else end


def plan_cutscene(config_db: Path, assets: Path, name: str, asset_version: str, *, missing_assets: list | None = None,
                  frame_audio: list[dict] | None = None, fallback_sources: list[dict] | None = None) -> dict:
    if not is_supported_cutscene_version(asset_version):
        raise ValueError(f"Config schema requires client >= {MIN_SUPPORTED_CLIENT_VERSION}, got {asset_version}")
    # Read-only: never create or modify an absent game database.
    videos, sounds = [], []
    if config_db.is_file():
        with sqlite3.connect(config_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
            videos = db.execute(
                "SELECT CgId, GirlOrBoy, BinData FROM videodata WHERE CgName = ? ORDER BY CgId", (name,)
            ).fetchall()
            sounds = db.execute(
                "SELECT GirlOrBoy, BinData FROM videosound WHERE CgName = ? ORDER BY CaptionId", (name,)
            ).fetchall()
    elif not fallback_sources:
        with sqlite3.connect(config_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
            videos = db.execute(
                "SELECT CgId, GirlOrBoy, BinData FROM videodata WHERE CgName = ? ORDER BY CgId", (name,)
            ).fetchall()
            sounds = db.execute(
                "SELECT GirlOrBoy, BinData FROM videosound WHERE CgName = ? ORDER BY CaptionId", (name,)
            ).fetchall()
    if videos:
        variant_entries = [
            {
                "clip_id": f"cg-{cg_id}",
                "gender": gender,
                "asset": "asset:ue:" + engine_path(blob),
                "label": "Male Rover" if gender == 1 else "Female Rover" if gender == 0 else f"CG {cg_id}",
            }
            for cg_id, gender, blob in videos
        ]
        evidence = (
            f"{config_db.name}: VideoData CG {name}, gender-specific/shared VideoSound with source seconds. "
            "Shared prefixes and narrative choices are not inferred."
        )
    elif fallback_sources:
        variant_entries = [
            {
                "clip_id": f"clip-{index}",
                "gender": item.get("girl_or_boy", 2),
                "asset": item["asset"],
                "label": (
                    "Male Rover" if item.get("girl_or_boy", 2) == 1
                    else "Female Rover" if item.get("girl_or_boy", 2) == 0
                    else f"Clip {index + 1}"
                ),
            }
            for index, item in enumerate(fallback_sources)
        ]
        bases = sorted({item.get("basis", "archive discovery") for item in fallback_sources})
        evidence = (
            f"Client archive discovery ({', '.join(bases)}) for {name} (absent from VideoData). "
            "Shared prefixes and narrative choices are not inferred."
        )
    else:
        raise ValueError("Cutscene not found in VideoData")
    inputs, clips, options = [], [], []
    input_assets = {}
    banks_by_name = {}
    # VideoConfig.GetVideoSounds selects the whole shared list when present;
    # gender-specific rows are a fallback, not additional layers to mix.
    shared_sounds = [row for row in sounds if row[0] == 2]
    for path in assets.rglob("*"):
        if path.is_file() and path.suffix.casefold() == ".bnk":
            banks_by_name.setdefault(path.name.casefold(), []).append(path)
    for entry in variant_entries:
        clip_id = entry["clip_id"]
        gender = entry["gender"]
        key = entry["asset"]
        soundtrack = []
        for sound_gender, sound_blob in shared_sounds or sounds:
            if not shared_sounds and sound_gender != gender:
                continue
            event = engine_path(sound_blob).rsplit(".", 1)[-1]
            banks = banks_by_name.get((event + ".bnk").casefold(), [])
            if not banks and missing_assets is not None:
                if event not in missing_assets:
                    missing_assets.append(event)
                continue
            localized = {language: [bank for bank in banks if bank.parent.name.casefold() == language]
                         for language in ("en", "ja", "ko", "zh")}
            if len(banks) == 1 and not any(localized.values()):
                selected = [(None, banks[0])]
            elif all(len(paths) == 1 for paths in localized.values()) and len(banks) == 4:
                selected = [(language, paths[0]) for language, paths in localized.items()]
            else:
                raise ValueError(f"Incomplete or ambiguous localized event banks: {event}")
            start, end = sound_timing(sound_blob)
            for language, bank in selected:
                soundtrack.append({"bank": bank.relative_to(assets).as_posix(),
                                   "language": language,
                                   "rover": "male" if gender == 1 else "female" if gender == 0 else None,
                                   "start_seconds": start, "end_seconds": end})
        if config_db.is_file():
            soundtrack.extend(caption_soundtracks(config_db, assets, name, gender,
                                                   missing_assets=missing_assets))
        if key in input_assets:
            # One movie may serve two authored Rover variants. Keep the audio
            # alternatives separate while publishing the video only once.
            for track in soundtrack:
                if track not in input_assets[key]["soundtrack"]:
                    input_assets[key]["soundtrack"].append(track)
        else:
            value = {"asset": key, "soundtrack": soundtrack, **({'frame_audio': frame_audio} if frame_audio else {})}
            input_assets[key] = value
            inputs.append(value)
        clips.append({"id": clip_id, "kind": "clip", "asset": key,
                      "rover": "male" if gender == 1 else "female" if gender == 0 else None})
        options.append(
            {
                "label": entry["label"],
                "next": clip_id,
                "rover": "male" if gender == 1 else "female" if gender == 0 else None,
            }
        )
    if len(clips) == 1:
        nodes = clips
    elif not videos and all(clip.get("rover") is None for clip in clips):
        # Multiple sequential non-gender fragments from the same sequence chain cleanly.
        for index in range(len(clips) - 1):
            clips[index]["next"] = clips[index + 1]["id"]
        nodes = clips
    else:
        nodes = [
            {"id": "variants", "kind": "choice", "prompt": "Choose a variant", "options": options},
            *clips,
        ]
    recipe = CutsceneRecipe.model_validate(
        {
            "cutscene": "cutscene:" + name,
            "asset_version": asset_version,
            "videos": inputs,
            "compare_variants": len(inputs) > 1 and any(clip.get("rover") is not None for clip in clips),
            "flow": {
                "entry": nodes[0]["id"],
                "nodes": nodes,
                "evidence": evidence,
            },
        }
    )
    return recipe.model_dump()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-db", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--asset-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    recipe = plan_cutscene(args.config_db, args.assets, args.name, args.asset_version)
    args.output.write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    print(json.dumps({"cutscene": recipe["cutscene"], "videos": len(recipe["videos"])}))
