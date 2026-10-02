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


def plan_cutscene(config_db: Path, assets: Path, name: str, asset_version: str) -> dict:
    if asset_version != "3.7.0":
        raise ValueError("Config schema is verified for client 3.7.0 only")
    # Read-only: never create or modify an absent game database.
    with sqlite3.connect(config_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        videos = db.execute(
            "SELECT CgId, GirlOrBoy, BinData FROM videodata WHERE CgName = ? ORDER BY CgId", (name,)
        ).fetchall()
        sounds = db.execute(
            "SELECT GirlOrBoy, BinData FROM videosound WHERE CgName = ? ORDER BY CaptionId", (name,)
        ).fetchall()
    if not videos:
        raise ValueError("Cutscene not found in VideoData")
    inputs, clips, options = [], [], []
    for cg_id, gender, blob in videos:
        key = "asset:ue:" + engine_path(blob)
        soundtrack = []
        for sound_gender, sound_blob in sounds:
            if sound_gender not in (2, gender):
                continue
            event = engine_path(sound_blob).rsplit(".", 1)[-1]
            banks = list(assets.rglob(event + ".bnk"))
            if len(banks) != 1:
                raise ValueError(f"Export the exact event bank before planning: {event}")
            start, end = sound_timing(sound_blob)
            soundtrack.append(
                {
                    "bank": banks[0].relative_to(assets).as_posix(),
                    "start_seconds": start,
                    "end_seconds": end,
                }
            )
        inputs.append({"asset": key, "soundtrack": soundtrack})
        clips.append({"id": f"cg-{cg_id}", "kind": "clip", "asset": key})
        options.append(
            {
                "label": "Male Rover"
                if gender == 1
                else "Female Rover"
                if gender == 0
                else f"CG {cg_id}",
                "next": f"cg-{cg_id}",
            }
        )
    nodes = (
        clips
        if len(clips) == 1
        else [
            {"id": "variants", "kind": "choice", "prompt": "Choose a variant", "options": options},
            *clips,
        ]
    )
    recipe = CutsceneRecipe.model_validate(
        {
            "cutscene": "cutscene:" + name,
            "asset_version": asset_version,
            "videos": inputs,
            "compare_variants": len(inputs) > 1,
            "flow": {
                "entry": nodes[0]["id"],
                "nodes": nodes,
                "evidence": f"{config_db.name}: VideoData CG {name}, gender-specific/shared VideoSound with source seconds. Shared prefixes and narrative choices are not inferred.",
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
