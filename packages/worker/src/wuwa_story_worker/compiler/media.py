"""Compile explicit video and audio references from BinData tables.

This module does not infer physical media package membership. Identifiers in
PlotAudio, Wwise event paths, and CgFile remain references to game resources.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


Emit = Callable[[str, dict[str, Any]], None]
Edge = Callable[[str, str, str, str, str, str, dict[str, Any] | None], None]
Diagnostic = Callable[[str, str, str, str, str], None]


def _bin_root(data_root: str | Path) -> Path:
    root = Path(data_root)
    return root if root.name == "BinData" else root / "BinData"


def _rows(root: Path, relative: str, diagnostic: Diagnostic) -> list[dict[str, Any]]:
    path = root / relative
    source = f"BinData/{relative}"
    if not path.is_file():
        diagnostic("missing_media_table", "warning", source, "$", "Table is unavailable")
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        diagnostic("invalid_media_table", "error", source, "$", str(exc))
        return []
    if not isinstance(rows, list):
        diagnostic("unsupported_media_schema", "error", source, "$", "Expected a JSON array")
        return []
    result = []
    for row, value in enumerate(rows):
        if not isinstance(value, dict):
            diagnostic("unsupported_media_row", "error", source, f"$[{row}]", "Expected an object")
            result.append({"_unsupported_raw_value": value})
            continue
        result.append(value)
    return result


def _source(relative: str, row: int) -> dict[str, Any]:
    return {"file": f"BinData/{relative}", "row": row, "raw_path": f"$[{row}]"}


def _asset_id(value: str, namespace: str) -> str:
    return f"asset:{namespace}:{value}"


def compile_media(
    data_root: str | Path,
    emit: Emit,
    edge: Edge,
    diagnostic: Diagnostic,
    localize: Callable[[str], Any] | None = None,
) -> None:
    """Emit media nodes and only joins supported by exact source fields.

    ``emit(kind, record)`` receives a record with ``id``, ``source`` and ``raw``.
    ``edge(from_id, to_id, type, basis, source_file, raw_path, extra)`` receives
    the literal source field as its basis. The function is deterministic for an
    unchanged input tree; callbacks own storage and deduplication.
    """

    root = _bin_root(data_root)
    assets: set[str] = set()
    groups: set[str] = set()
    video_rows = _rows(root, "cgVedio/videodata.json", diagnostic)
    known_cg_names = {row.get("CgName") for row in video_rows
                      if isinstance(row.get("CgName"), str) and row.get("CgName")}

    def resolve_cg_name(value: Any) -> tuple[Any, str]:
        if isinstance(value, str) and value in known_cg_names:
            return value, "exact"
        if isinstance(value, str) and value.strip() in known_cg_names:
            matches = {name for name in known_cg_names if name.strip() == value.strip()}
            if len(matches) == 1:
                return next(iter(matches)), "unique_whitespace_normalization"
        if isinstance(value, str) and value:
            return value, "exact"
        return value, "unresolved"

    def asset(value: Any, namespace: str, source: dict[str, Any], field: str) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        identity = _asset_id(value, namespace)
        if identity not in assets:
            emit("asset_reference", {
                "id": identity, "value": value, "namespace": namespace,
                "source": source, "raw": {field: value},
            })
            assets.add(identity)
        return identity

    def group(name: Any, source: dict[str, Any], raw_value: Any = None,
              resolution: str = "exact") -> str | None:
        if not isinstance(name, str) or not name:
            diagnostic("missing_cg_name", "warning", source["file"], source["raw_path"], "CgName is empty")
            return None
        identity = f"cutscene:{name}"
        if identity not in groups:
            emit("cutscene", {"id": identity, "cg_name": name, "source": source,
                               "normalized_value": name,
                               "resolution": resolution,
                               "raw": {"CgName": name if raw_value is None else raw_value},
                               "resolution_basis": "CgName"})
            groups.add(identity)
        return identity

    relative = "cgVedio/videodata.json"
    for row, raw in enumerate(video_rows):
        src = _source(relative, row)
        cg = group(raw.get("CgName"), src)
        identity = f"cutscene_variant:{raw.get('CgId', 'missing')}:{row}"
        emit("cutscene_variant", {"id": identity, "source": src, "raw": raw,
                                  "cg_id": raw.get("CgId"), "cg_name": raw.get("CgName"),
                                  "girl_or_boy": raw.get("GirlOrBoy"),
                                  "belong_branch": raw.get("BelongBranch")})
        if cg:
            edge(cg, identity, "has_variant", "explicit CgName equality", src["file"],
                 f"$[{row}].CgName", None)
        ref = asset(raw.get("CgFile"), "ue", src, "CgFile")
        if ref:
            edge(identity, ref, "references_asset", "CgFile", src["file"],
                 f"$[{row}].CgFile", None)
        else:
            diagnostic("missing_cutscene_asset", "warning", src["file"],
                       f"$[{row}].CgFile", f"CgName={raw.get('CgName')!r}")

    relative = "cgVedio/videocaption.json"
    for row, raw in enumerate(_rows(root, relative, diagnostic)):
        src = _source(relative, row)
        cg_name, cg_resolution = resolve_cg_name(raw.get("CgName"))
        cg = group(cg_name, src, raw.get("CgName"), cg_resolution)
        identity = f"caption:{raw.get('CaptionId', 'missing')}:{row}"
        record = {"id": identity, "source": src, "raw": raw,
                  "caption_id": raw.get("CaptionId"), "cg_name": raw.get("CgName"),
                  "localization_key": raw.get("CaptionText"),
                  "timing": {k: v for k, v in raw.items()
                             if k.startswith("ShowMoment") or k.startswith("Duration")}}
        key = raw.get("CaptionText")
        if localize and isinstance(key, str) and key:
            record["localized_text"] = localize(key)
        emit("caption", record)
        if cg:
            edge(cg, identity, "uses_caption",
                 "explicit CgName equality" if cg_resolution == "exact" else "unique whitespace-normalized CgName",
                 src["file"], f"$[{row}].CgName",
                 None if cg_resolution == "exact" else {"raw_value": raw.get("CgName"),
                                                       "normalized_value": cg_name,
                                                       "resolution": cg_resolution})

    relative = "cgVedio/videosound.json"
    for row, raw in enumerate(_rows(root, relative, diagnostic)):
        src = _source(relative, row)
        cg_name, cg_resolution = resolve_cg_name(raw.get("CgName"))
        cg = group(cg_name, src, raw.get("CgName"), cg_resolution)
        identity = f"audio_event:videosound:{row}"
        emit("audio_event", {"id": identity, "source": src, "raw": raw,
                             "event_path": raw.get("EventPath"), "cg_name": cg_name,
                             "raw_cg_name": raw.get("CgName"), "cg_name_resolution": cg_resolution})
        if cg:
            edge(cg, identity,
                 "uses_audio_event" if cg_resolution == "exact" else "uses_audio_event_normalized",
                 "explicit CgName equality" if cg_resolution == "exact" else "unique whitespace-normalized CgName",
                 src["file"], f"$[{row}].CgName",
                 {"girl_or_boy": raw.get("GirlOrBoy"), "raw_value": raw.get("CgName"),
                  "normalized_value": cg_name, "resolution": cg_resolution})
        ref = asset(raw.get("EventPath"), "ue", src, "EventPath")
        if ref:
            edge(identity, ref, "references_asset", "EventPath", src["file"],
                 f"$[{row}].EventPath", None)
        else:
            diagnostic("missing_audio_event_path", "warning", src["file"],
                       f"$[{row}].EventPath", f"CgName={raw.get('CgName')!r}")

    relative = "cgVedio/videoqte.json"
    for row, raw in enumerate(_rows(root, relative, diagnostic)):
        src = _source(relative, row)
        cg_name, resolution = resolve_cg_name(raw.get("CgName"))
        identity = f"video_qte:{raw.get('Id', 'missing')}:{row}"
        emit("video_qte", {"id": identity, "source": src, "raw": raw,
                            "cg_name": cg_name, "qte_id": raw.get("QteId"),
                            "resolution": resolution})
        if cg_name in known_cg_names:
            cg = group(cg_name, src, raw.get("CgName"), resolution)
            if cg:
                edge(cg, identity, "has_video_qte", "CgName = videodata.CgName",
                     src["file"], f"$[{row}].CgName",
                     {"raw_value": raw.get("CgName"), "normalized_value": cg_name,
                      "resolution": resolution})
        else:
            diagnostic("unresolved_video_qte_cutscene", "warning", src["file"],
                       f"$[{row}].CgName", raw.get("CgName"))

    relative = "plot_audio/plotaudio.json"
    for row, raw in enumerate(_rows(root, relative, diagnostic)):
        src = _source(relative, row)
        key = raw.get("Id")
        if not isinstance(key, str) or not key:
            diagnostic("missing_plot_audio_id", "error", src["file"], f"$[{row}].Id", str(key))
            identity = f"voice_ref:row:{row}"
        else:
            identity = f"voice_ref:{key}"
        emit("voice_reference", {"id": identity, "source": src, "raw": raw,
                                 "plot_audio_id": key, "file_name": raw.get("FileName")})
        ref = asset(raw.get("FileName"), "plot_audio_filename", src, "FileName")
        if ref:
            edge(identity, ref, "references_asset", "FileName", src["file"],
                 f"$[{row}].FileName", {"physical_path_resolved": False})

    relative = "audio/audio.json"
    for row, raw in enumerate(_rows(root, relative, diagnostic)):
        src = _source(relative, row)
        identity = f"audio_event:config:{raw.get('Id', 'missing')}:{row}"
        emit("audio_event", {"id": identity, "source": src, "raw": raw,
                             "event_id": raw.get("Id"), "event_path": raw.get("Path")})
        ref = asset(raw.get("Path"), "ue", src, "Path")
        if ref:
            edge(identity, ref, "references_asset", "Path", src["file"], f"$[{row}].Path", None)

    relative = "QuestRefVideo/questrefvideoconfig.json"
    for row, raw in enumerate(_rows(root, relative, diagnostic)):
        src = _source(relative, row)
        identity = f"quest_video_package_ref:{row}"
        emit("asset_reference", {"id": identity, "source": src, "raw": raw,
                                 "quest_id": raw.get("QuestId"),
                                 "package_name": raw.get("PakName"),
                                 "online_branch": raw.get("OnlineBranch"),
                                 "girl_or_boy": raw.get("GirlOrBoy")})
        quest_id = raw.get("QuestId")
        if isinstance(quest_id, int):
            edge(f"quest:{quest_id}", identity, "references_video_package",
                 "QuestId", src["file"], f"$[{row}].QuestId", None)
        package = raw.get("PakName")
        branch = raw.get("OnlineBranch")
        if isinstance(package, str) and package:
            # The pair identifies a package reference, not an inferred file path.
            ref = f"asset:video_package:{branch}:{package}"
            if ref not in assets:
                emit("asset_reference", {"id": ref, "namespace": "video_package",
                                         "package_name": package, "online_branch": branch,
                                         "source": src, "raw": {"PakName": package,
                                                                 "OnlineBranch": branch}})
                assets.add(ref)
            edge(identity, ref, "references_asset", "PakName + OnlineBranch", src["file"],
                 f"$[{row}].PakName", {"package_name": package, "online_branch": branch})
